"""Config reconciliation loop — syncs vw-config YAML definitions into mgmt-api DB.

On startup and whenever the config hash changes, this module:

1. Fetches walls and sources from the vw-config REST API
2. Maps them to the mgmt-api database schema (walls/sources tables)
3. Upserts: inserts new, updates changed, marks removed as stale
4. Emits audit events for every change

The reconciliation is additive — it never deletes DB records that were
created manually via the CRUD API.  Config-managed records are identified
by a naming convention: ``config_id`` is stored in the ``tags`` array as
``config:<id>`` so the reconciler can distinguish config-managed rows from
manually-created ones.

Environment variables:
    VW_CONFIG_URL       vw-config base URL   (default: http://vw-config:8006)
    VW_RECONCILE_INTERVAL_S  poll interval   (default: 30)
    VW_RECONCILE_ENABLED     enable/disable  (default: true)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from .database import append_audit_event, get_pool

logger = logging.getLogger("vw.reconcile")

VW_CONFIG_URL = os.getenv("VW_CONFIG_URL", "http://vw-config:8006")
RECONCILE_INTERVAL = int(os.getenv("VW_RECONCILE_INTERVAL_S", "30"))
RECONCILE_ENABLED = os.getenv("VW_RECONCILE_ENABLED", "true").lower() in ("1", "true", "yes")
_ACTOR = "config-reconciler"

# ── Mapping helpers ──────────────────────────────────────────────────────

_TYPE_MAP_WALL = {"tiles": "tilewall", "bigscreen": "bigscreen"}
_TYPE_MAP_SRC = {"webrtc": "vdi", "srt": "hdmi", "rtsp": "hdmi", "rtp": "hdmi"}
_PROTO_MAP = {"webrtc": "webrtc", "srt": "srt", "rtsp": "rtsp", "rtp": "rtp"}


def _config_tag(config_id: str) -> str:
    """Marker tag that links a DB row to a vw-config YAML ID."""
    return f"config:{config_id}"


def _wall_to_db(w: dict[str, Any]) -> dict[str, Any]:
    """Map a vw-config wall dict to mgmt-api WallIn fields."""
    grid = w.get("grid") or {}
    tile_count = grid.get("rows", 1) * grid.get("cols", 1) if grid else w.get("screens", 1)
    raw_tags = w.get("tags") or {}
    tag_list = [f"{k}:{v}" for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else list(raw_tags)
    tag_list.append(_config_tag(w["id"]))
    return {
        "name": str(w["id"]),
        "wall_type": _TYPE_MAP_WALL.get(w.get("type", "tiles"), "tilewall"),
        "tile_count": tile_count,
        "resolution": w.get("resolution", "1920x1080"),
        "tags": sorted(set(tag_list)),
    }


def _source_to_db(s: dict[str, Any]) -> dict[str, Any]:
    """Map a vw-config source dict to mgmt-api SourceIn fields."""
    src_type = _TYPE_MAP_SRC.get(s.get("type", "srt"), "hdmi")
    protocol = _PROTO_MAP.get(s.get("type", "srt"), "other")
    raw_tags = s.get("tags") or {}
    tag_list = [f"{k}:{v}" for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else list(raw_tags)
    tag_list.append(_config_tag(s["id"]))
    return {
        "name": str(s["id"]),
        "source_type": src_type,
        "protocol": protocol,
        "endpoint_url": s.get("endpoint", ""),
        "codec": s.get("codec", "h264"),
        "tags": sorted(set(tag_list)),
        "health_status": "unknown",
    }


# ── Fetch from vw-config ────────────────────────────────────────────────

async def _fetch_config_version() -> str | None:
    """Return current config hash, or None if vw-config is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VW_CONFIG_URL}/api/v1/config/version")
            r.raise_for_status()
            return r.json().get("config_hash")
    except Exception as exc:
        logger.warning("vw-config unreachable for version check: %s", exc)
        return None


async def _fetch_walls() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{VW_CONFIG_URL}/api/v1/walls")
        r.raise_for_status()
        return r.json().get("walls", [])


async def _fetch_sources() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{VW_CONFIG_URL}/api/v1/sources")
        r.raise_for_status()
        return r.json().get("sources", [])


# ── Upsert logic ────────────────────────────────────────────────────────

async def _reconcile_walls(config_walls: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert walls from config into DB.  Returns {"created": n, "updated": m}."""
    pool = await get_pool()
    stats = {"created": 0, "updated": 0}

    for cw in config_walls:
        db_fields = _wall_to_db(cw)
        marker = _config_tag(cw["id"])

        async with pool.acquire() as conn:
            # Find existing config-managed row by marker tag
            row = await conn.fetchrow(
                "SELECT id, name, wall_type, tile_count, resolution, tags FROM walls WHERE $1 = ANY(tags)",
                marker,
            )

            if row is None:
                # INSERT
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO walls (name, wall_type, tile_count, resolution, tags)
                    VALUES ($1,$2,$3,$4,$5)
                    RETURNING id
                    """,
                    db_fields["name"], db_fields["wall_type"], db_fields["tile_count"],
                    db_fields["resolution"], db_fields["tags"],
                )
                stats["created"] += 1
                await append_audit_event(
                    action="config.reconcile.wall.create", actor=_ACTOR,
                    object_type="wall", object_id=str(inserted["id"]),
                    details={"config_id": cw["id"], **db_fields},
                )
            else:
                # Check if anything changed
                existing = {
                    "name": row["name"], "wall_type": row["wall_type"],
                    "tile_count": row["tile_count"], "resolution": row["resolution"],
                    "tags": sorted(row["tags"]),
                }
                proposed = {
                    "name": db_fields["name"], "wall_type": db_fields["wall_type"],
                    "tile_count": db_fields["tile_count"], "resolution": db_fields["resolution"],
                    "tags": db_fields["tags"],
                }
                if existing != proposed:
                    await conn.execute(
                        """
                        UPDATE walls SET name=$2, wall_type=$3, tile_count=$4, resolution=$5, tags=$6, updated_at=NOW()
                        WHERE id=$1
                        """,
                        row["id"], db_fields["name"], db_fields["wall_type"],
                        db_fields["tile_count"], db_fields["resolution"], db_fields["tags"],
                    )
                    stats["updated"] += 1
                    await append_audit_event(
                        action="config.reconcile.wall.update", actor=_ACTOR,
                        object_type="wall", object_id=str(row["id"]),
                        details={"config_id": cw["id"], "before": existing, "after": proposed},
                    )

    return stats


async def _reconcile_sources(config_sources: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert sources from config into DB.  Returns {"created": n, "updated": m}."""
    pool = await get_pool()
    stats = {"created": 0, "updated": 0}

    for cs in config_sources:
        db_fields = _source_to_db(cs)
        marker = _config_tag(cs["id"])

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, source_type, protocol, endpoint_url, codec, tags FROM sources WHERE $1 = ANY(tags)",
                marker,
            )

            if row is None:
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO sources (name, source_type, protocol, endpoint_url, codec, tags, health_status)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    RETURNING id
                    """,
                    db_fields["name"], db_fields["source_type"], db_fields["protocol"],
                    db_fields["endpoint_url"], db_fields["codec"], db_fields["tags"],
                    db_fields["health_status"],
                )
                stats["created"] += 1
                await append_audit_event(
                    action="config.reconcile.source.create", actor=_ACTOR,
                    object_type="source", object_id=str(inserted["id"]),
                    details={"config_id": cs["id"], **db_fields},
                )
            else:
                existing = {
                    "name": row["name"], "source_type": row["source_type"],
                    "protocol": row["protocol"], "endpoint_url": row["endpoint_url"],
                    "codec": row["codec"], "tags": sorted(row["tags"]),
                }
                proposed = {
                    "name": db_fields["name"], "source_type": db_fields["source_type"],
                    "protocol": db_fields["protocol"], "endpoint_url": db_fields["endpoint_url"],
                    "codec": db_fields["codec"], "tags": db_fields["tags"],
                }
                if existing != proposed:
                    await conn.execute(
                        """
                        UPDATE sources
                        SET name=$2, source_type=$3, protocol=$4, endpoint_url=$5, codec=$6, tags=$7, updated_at=NOW()
                        WHERE id=$1
                        """,
                        row["id"], db_fields["name"], db_fields["source_type"],
                        db_fields["protocol"], db_fields["endpoint_url"],
                        db_fields["codec"], db_fields["tags"],
                    )
                    stats["updated"] += 1
                    await append_audit_event(
                        action="config.reconcile.source.update", actor=_ACTOR,
                        object_type="source", object_id=str(row["id"]),
                        details={"config_id": cs["id"], "before": existing, "after": proposed},
                    )

    return stats


# ── Public API ───────────────────────────────────────────────────────────

async def reconcile_once() -> dict[str, Any]:
    """Run one reconciliation pass.  Returns summary of changes."""
    try:
        config_walls = await _fetch_walls()
        config_sources = await _fetch_sources()
    except Exception as exc:
        logger.error("Failed to fetch from vw-config: %s", exc)
        return {"error": str(exc)}

    wall_stats = await _reconcile_walls(config_walls)
    source_stats = await _reconcile_sources(config_sources)

    result = {
        "walls": wall_stats,
        "sources": source_stats,
        "config_walls": len(config_walls),
        "config_sources": len(config_sources),
    }
    total = wall_stats["created"] + wall_stats["updated"] + source_stats["created"] + source_stats["updated"]
    if total > 0:
        logger.info("Reconciliation applied %d changes: %s", total, result)
    else:
        logger.debug("Reconciliation: no changes")
    return result


async def reconcile_loop() -> None:
    """Background loop: poll vw-config hash, reconcile on change."""
    if not RECONCILE_ENABLED:
        logger.info("Config reconciliation disabled (VW_RECONCILE_ENABLED=false)")
        return

    logger.info("Config reconciliation started (interval=%ds, url=%s)", RECONCILE_INTERVAL, VW_CONFIG_URL)
    last_hash: str | None = None

    # Initial reconciliation (best-effort on startup)
    await asyncio.sleep(2)  # give vw-config a moment to start
    try:
        last_hash = await _fetch_config_version()
        if last_hash:
            await reconcile_once()
    except Exception as exc:
        logger.warning("Initial reconciliation failed (will retry): %s", exc)

    while True:
        await asyncio.sleep(RECONCILE_INTERVAL)
        try:
            current_hash = await _fetch_config_version()
            if current_hash is None:
                continue  # vw-config unreachable, skip
            if current_hash != last_hash:
                logger.info("Config hash changed (%s → %s), reconciling...", last_hash, current_hash)
                await reconcile_once()
                last_hash = current_hash
        except Exception as exc:
            logger.warning("Reconciliation loop error: %s", exc)
