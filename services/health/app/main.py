from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VW_", case_sensitive=False)
    db_dsn: str = "postgresql://vw:vw@postgres:5432/vw"
    db_min_size: int = 1
    db_max_size: int = 10
    # component health endpoints (comma-separated)
    component_health_urls: str = "http://vw-sfu:8080/healthz,http://vw-gateway:8080/healthz,http://vw-compositor:8080/healthz"

settings = Settings()
POOL: Optional[asyncpg.Pool] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_health (
  source_id   INTEGER PRIMARY KEY,
  last_seen   TIMESTAMPTZ NOT NULL,
  status      TEXT NOT NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS wall_health (
  wall_id     INTEGER PRIMARY KEY,
  last_seen   TIMESTAMPTZ NOT NULL,
  status      TEXT NOT NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""

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

class HeartbeatWall(BaseModel):
    wall_id: int
    status: str = "ok"
    details: dict[str, Any] = Field(default_factory=dict)

class HeartbeatSource(BaseModel):
    source_id: int
    status: str = "ok"
    details: dict[str, Any] = Field(default_factory=dict)

app = FastAPI(title="vw-health", version="0.1.0")

@app.on_event("startup")
async def _startup() -> None:
    await init_db()

@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()

@app.post("/heartbeat/wall")
async def heartbeat_wall(hb: HeartbeatWall) -> dict[str, Any]:
    pool = await get_pool()
    ts = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO wall_health (wall_id, last_seen, status, details)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (wall_id) DO UPDATE SET last_seen=EXCLUDED.last_seen, status=EXCLUDED.status, details=EXCLUDED.details
            """,
            hb.wall_id, ts, hb.status, json.dumps(hb.details)
        )
    return {"ok": True, "wall_id": hb.wall_id, "ts": ts.isoformat()}

@app.post("/heartbeat/source")
async def heartbeat_source(hb: HeartbeatSource) -> dict[str, Any]:
    pool = await get_pool()
    ts = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_health (source_id, last_seen, status, details)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (source_id) DO UPDATE SET last_seen=EXCLUDED.last_seen, status=EXCLUDED.status, details=EXCLUDED.details
            """,
            hb.source_id, ts, hb.status, json.dumps(hb.details)
        )
        # Keep sources.health_status in sync if sources table exists.
        try:
            await conn.execute("UPDATE sources SET health_status=$2, updated_at=NOW() WHERE id=$1", hb.source_id, hb.status)
        except Exception:
            # If sources table isn't present yet, ignore; mgmt-api creates it.
            pass
    return {"ok": True, "source_id": hb.source_id, "ts": ts.isoformat()}

@app.get("/status/walls")
async def status_walls() -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.name, w.wall_type, w.tile_count, w.resolution, w.tags,
                   wh.last_seen, wh.status, wh.details
            FROM walls w
            LEFT JOIN wall_health wh ON wh.wall_id=w.id
            ORDER BY w.id
            """
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("last_seen"):
            d["last_seen"] = d["last_seen"].isoformat()
        if isinstance(d.get("details"), str):
            d["details"] = json.loads(d["details"])
        out.append(d)
    return out

@app.get("/status/sources")
async def status_sources() -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.name, s.source_type, s.protocol, s.endpoint_url, s.codec, s.tags, s.health_status,
                   sh.last_seen, sh.status, sh.details
            FROM sources s
            LEFT JOIN source_health sh ON sh.source_id=s.id
            ORDER BY s.id
            """
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("last_seen"):
            d["last_seen"] = d["last_seen"].isoformat()
        if isinstance(d.get("details"), str):
            d["details"] = json.loads(d["details"])
        out.append(d)
    return out

@app.get("/status/components")
async def status_components() -> list[dict[str, Any]]:
    urls = [u.strip() for u in settings.component_health_urls.split(",") if u.strip()]
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=2.5) as client:
        for u in urls:
            item = {"url": u, "ok": False, "status_code": None, "body": None}
            try:
                r = await client.get(u)
                item["status_code"] = r.status_code
                item["ok"] = r.status_code == 200
                # small body only
                item["body"] = (r.text[:500] if r.text else "")
            except Exception as e:
                item["body"] = f"error:{type(e).__name__}"
            results.append(item)
    return results
