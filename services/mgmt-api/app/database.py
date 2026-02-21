from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from .config import settings

POOL: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global POOL
    if POOL is None:
        POOL = await asyncpg.create_pool(
            dsn=settings.db_dsn,
            min_size=settings.db_min_size,
            max_size=settings.db_max_size,
        )
    return POOL


async def close_pool() -> None:
    global POOL
    if POOL is not None:
        await POOL.close()
        POOL = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS walls (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  wall_type   TEXT NOT NULL,
  tile_count  INTEGER NOT NULL,
  resolution  TEXT NOT NULL,
  tags        TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sources (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  source_type   TEXT NOT NULL,
  protocol      TEXT NOT NULL,
  endpoint_url  TEXT NOT NULL,
  codec         TEXT NOT NULL,
  tags          TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  health_status TEXT NOT NULL DEFAULT 'unknown',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS layouts (
  id          SERIAL PRIMARY KEY,
  wall_id     INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  version     INTEGER NOT NULL,
  grid_config JSONB NOT NULL,
  preset_name TEXT NOT NULL DEFAULT '',
  is_active   BOOLEAN NOT NULL DEFAULT FALSE,
  created_by  TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_layouts_wall_id ON layouts(wall_id);
CREATE INDEX IF NOT EXISTS idx_layouts_active ON layouts(wall_id, is_active);

CREATE TABLE IF NOT EXISTS audit_events (
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

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);

CREATE TABLE IF NOT EXISTS source_health (
  source_id   INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  last_seen   TIMESTAMPTZ NOT NULL,
  status      TEXT NOT NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS wall_health (
  wall_id     INTEGER PRIMARY KEY REFERENCES walls(id) ON DELETE CASCADE,
  last_seen   TIMESTAMPTZ NOT NULL,
  status      TEXT NOT NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""


async def init_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def append_audit_event(
    *,
    action: str,
    actor: str,
    object_type: str,
    object_id: str,
    details: dict[str, Any],
    chain_id: str | None = None,
) -> dict[str, Any]:
    chain = chain_id or settings.audit_chain_id
    pool = await get_pool()
    ts = datetime.now(timezone.utc)

    event_core = {
        "ts": ts.isoformat(),
        "chain_id": chain,
        "action": action,
        "actor": actor,
        "object_type": object_type,
        "object_id": object_id,
        "details": details,
    }
    canonical = json.dumps(event_core, sort_keys=True, separators=(",", ":")).encode("utf-8")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hash FROM audit_events WHERE chain_id=$1 ORDER BY id DESC LIMIT 1",
            chain,
        )
        prev_hash = row["hash"] if row else "0" * 64
        h = _sha256_hex((prev_hash + "|").encode("utf-8") + canonical)

        inserted = await conn.fetchrow(
            """
            INSERT INTO audit_events (ts, chain_id, action, actor, object_type, object_id, details, prev_hash, hash)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id, ts, action, actor, object_type, object_id, details, prev_hash, hash
            """,
            ts,
            chain,
            action,
            actor,
            object_type,
            object_id,
            json.dumps(details),
            prev_hash,
            h,
        )
        return dict(inserted)


async def ensure_layout_version(conn: asyncpg.Connection, wall_id: int) -> int:
    row = await conn.fetchrow("SELECT COALESCE(MAX(version),0) AS v FROM layouts WHERE wall_id=$1", wall_id)
    return int(row["v"]) + 1


async def activate_layout(conn: asyncpg.Connection, layout_id: int) -> dict[str, Any]:
    layout = await conn.fetchrow("SELECT id, wall_id FROM layouts WHERE id=$1", layout_id)
    if not layout:
        raise KeyError("layout_not_found")
    wall_id = int(layout["wall_id"])
    await conn.execute("UPDATE layouts SET is_active=FALSE WHERE wall_id=$1 AND id<>$2", wall_id, layout_id)
    await conn.execute("UPDATE layouts SET is_active=TRUE WHERE id=$1", layout_id)
    updated = await conn.fetchrow("SELECT id, wall_id, name, version, is_active FROM layouts WHERE id=$1", layout_id)
    return dict(updated)
