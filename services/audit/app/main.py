from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VW_", case_sensitive=False)
    db_dsn: str = "postgresql://vw:vw@postgres:5432/vw"
    db_min_size: int = 1
    db_max_size: int = 10
    chain_id: str = "vw-audit"

settings = Settings()

POOL: Optional[asyncpg.Pool] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_store (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  chain_id    TEXT NOT NULL,
  action      TEXT NOT NULL,
  actor       TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id   TEXT NOT NULL,
  details     JSONB NOT NULL,
  prev_hash   TEXT NOT NULL,
  hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_store_ts ON audit_store(ts);
CREATE INDEX IF NOT EXISTS idx_audit_store_action ON audit_store(action);
CREATE INDEX IF NOT EXISTS idx_audit_store_actor ON audit_store(actor);
"""

def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def _canon_event(core: dict[str, Any]) -> bytes:
    return json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")

async def get_pool() -> asyncpg.Pool:
    global POOL
    if POOL is None:
        POOL = await asyncpg.create_pool(settings.db_dsn, min_size=settings.db_min_size, max_size=settings.db_max_size)
    return POOL

async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)

async def close_pool() -> None:
    global POOL
    if POOL is not None:
        await POOL.close()
        POOL = None

class IngestIn(BaseModel):
    action: str
    actor: str
    object_type: str
    object_id: str
    details: dict[str, Any] = Field(default_factory=dict)

class EventOut(BaseModel):
    id: int
    ts: str
    chain_id: str
    action: str
    actor: str
    object_type: str
    object_id: str
    details: dict[str, Any]
    prev_hash: str
    hash: str

app = FastAPI(title="vw-audit", version="0.1.0")

@app.on_event("startup")
async def _startup() -> None:
    await init_db()

@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()

@app.post("/ingest", response_model=EventOut)
async def ingest(ev: IngestIn) -> EventOut:
    pool = await get_pool()
    ts = datetime.now(timezone.utc)
    core = {
        "ts": ts.isoformat(),
        "chain_id": settings.chain_id,
        "action": ev.action,
        "actor": ev.actor,
        "object_type": ev.object_type,
        "object_id": ev.object_id,
        "details": ev.details,
    }
    canonical = _canon_event(core)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT hash FROM audit_store WHERE chain_id=$1 ORDER BY id DESC LIMIT 1", settings.chain_id)
        prev_hash = row["hash"] if row else "0"*64
        h = _sha256_hex((prev_hash + "|").encode("utf-8") + canonical)
        ins = await conn.fetchrow(
            """
            INSERT INTO audit_store (ts, chain_id, action, actor, object_type, object_id, details, prev_hash, hash)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id, ts, chain_id, action, actor, object_type, object_id, details, prev_hash, hash
            """,
            ts, settings.chain_id, ev.action, ev.actor, ev.object_type, ev.object_id, json.dumps(ev.details), prev_hash, h
        )
    d = dict(ins)
    d["ts"] = d["ts"].isoformat()
    if isinstance(d["details"], str):
        d["details"] = json.loads(d["details"])
    return EventOut(**d)

@app.get("/query", response_model=list[EventOut])
async def query(
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[EventOut]:
    pool = await get_pool()
    clauses = ["chain_id=$1"]
    args: list[Any] = [settings.chain_id]
    idx = 2

    if action:
        clauses.append(f"action=${idx}"); args.append(action); idx += 1
    if actor:
        clauses.append(f"actor=${idx}"); args.append(actor); idx += 1
    if since:
        clauses.append(f"ts>=${idx}"); args.append(datetime.fromisoformat(since.replace("Z","+00:00"))); idx += 1
    if until:
        clauses.append(f"ts<=${idx}"); args.append(datetime.fromisoformat(until.replace("Z","+00:00"))); idx += 1

    where = " AND ".join(clauses)
    q = f"""
      SELECT id, ts, chain_id, action, actor, object_type, object_id, details, prev_hash, hash
      FROM audit_store
      WHERE {where}
      ORDER BY id DESC
      LIMIT {int(limit)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *args)

    out: list[EventOut] = []
    for r in rows:
        d = dict(r)
        d["ts"] = d["ts"].isoformat()
        if isinstance(d["details"], str):
            d["details"] = json.loads(d["details"])
        out.append(EventOut(**d))
    return out

@app.get("/verify")
async def verify(last_n: int = Query(default=1000, ge=1, le=200000)) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, ts, chain_id, action, actor, object_type, object_id, details, prev_hash, hash
            FROM audit_store
            WHERE chain_id=$1
            ORDER BY id DESC
            LIMIT $2
            """,
            settings.chain_id,
            last_n,
        )

    # verify forward order
    rows_fwd = list(reversed(rows))
    expected_prev = "0"*64
    broken: list[dict[str, Any]] = []
    verified = 0

    for r in rows_fwd:
        d = dict(r)
        prev_hash = d["prev_hash"]
        h = d["hash"]
        if prev_hash != expected_prev:
            broken.append({"id": int(d["id"]), "reason": "prev_hash_mismatch", "expected_prev": expected_prev, "found_prev": prev_hash})
            expected_prev = h
            continue

        details = d["details"]
        if isinstance(details, str):
            details_obj = json.loads(details)
        else:
            details_obj = details

        core = {
            "ts": d["ts"].astimezone(timezone.utc).isoformat(),
            "chain_id": d["chain_id"],
            "action": d["action"],
            "actor": d["actor"],
            "object_type": d["object_type"],
            "object_id": d["object_id"],
            "details": details_obj,
        }
        calc = _sha256_hex((expected_prev + "|").encode("utf-8") + _canon_event(core))
        if calc != h:
            broken.append({"id": int(d["id"]), "reason": "hash_mismatch", "expected": calc, "found": h})
        else:
            verified += 1
        expected_prev = h

    return {"chain_id": settings.chain_id, "checked": len(rows_fwd), "verified": verified, "broken": broken}
