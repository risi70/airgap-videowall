# SPDX-License-Identifier: EUPL-1.2
"""vw-config HTTP API — configuration authority service."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config_authority import (
    ConfigWatcher,
    PlatformConfig,
    dry_run,
    load_config,
    load_config_file,
)

LOG = logging.getLogger("vw.config.api")

CONFIG_PATH = os.getenv("VW_CONFIG_PATH", "/etc/videowall/platform-config.yaml")
POLL_INTERVAL = float(os.getenv("VW_CONFIG_POLL_INTERVAL", "5"))

app = FastAPI(title="vw-config", version="0.2.0",
              description="Configuration Authority — declarative YAML-driven platform config")

_watcher: ConfigWatcher | None = None
_lock = threading.Lock()


def _get_config() -> PlatformConfig:
    if _watcher and _watcher.current:
        return _watcher.current
    raise HTTPException(status_code=503, detail="Configuration not loaded")


@app.on_event("startup")
def _startup():
    global _watcher
    p = Path(CONFIG_PATH)
    if p.exists():
        _watcher = ConfigWatcher(p, poll_interval=POLL_INTERVAL)
        _watcher.load_initial()
        t = threading.Thread(target=_watcher.watch_forever, daemon=True)
        t.start()
        LOG.info("Config watcher started: %s (poll=%ss)", CONFIG_PATH, POLL_INTERVAL)
    else:
        LOG.warning("Config file not found: %s — service will return 503 until config is provided", CONFIG_PATH)


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    if _watcher and _watcher.current:
        return {"status": "ok", "config_hash": _watcher.current.derived.config_hash}
    return JSONResponse({"status": "no_config"}, status_code=503)


# ── Full Config ───────────────────────────────────────────────────────────

@app.get("/api/v1/config")
def get_config():
    """Return current platform configuration + derived metrics."""
    cfg = _get_config()
    return {
        "platform": {
            "version": cfg.platform.version,
            "max_concurrent_streams": cfg.platform.max_concurrent_streams,
            "codec_policy": {"tiles": cfg.platform.codec_policy.tiles, "mosaics": cfg.platform.codec_policy.mosaics},
        },
        "walls": [_wall_dict(w) for w in cfg.walls],
        "sources": [_source_dict(s) for s in cfg.sources],
        "derived": {
            "total_walls": cfg.derived.total_walls,
            "tile_walls": cfg.derived.tile_walls,
            "bigscreen_walls": cfg.derived.bigscreen_walls,
            "total_tiles": cfg.derived.total_tiles,
            "total_screens": cfg.derived.total_screens,
            "total_endpoints": cfg.derived.total_display_endpoints,
            "sfu_rooms_needed": cfg.derived.sfu_rooms_needed,
            "mosaic_pipelines_needed": cfg.derived.mosaic_pipelines_needed,
            "estimated_bandwidth_gbps": round(cfg.derived.estimated_bandwidth_gbps, 2),
            "concurrency_headroom": cfg.derived.concurrency_headroom,
            "config_hash": cfg.derived.config_hash,
        },
        "loaded_from": cfg.loaded_from,
        "loaded_at": cfg.loaded_at,
    }


@app.get("/api/v1/config/version")
def get_version():
    cfg = _get_config()
    return {"version": cfg.platform.version, "config_hash": cfg.derived.config_hash}


# ── Walls ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/walls")
def list_walls():
    cfg = _get_config()
    return {"walls": [_wall_dict(w) for w in cfg.walls]}


@app.get("/api/v1/walls/{wall_id}")
def get_wall(wall_id: str):
    cfg = _get_config()
    w = cfg.get_wall(wall_id)
    if not w:
        raise HTTPException(404, detail=f"Wall not found: {wall_id}")
    return _wall_dict(w)


# ── Sources ───────────────────────────────────────────────────────────────

@app.get("/api/v1/sources")
def list_sources():
    cfg = _get_config()
    return {"sources": [_source_dict(s) for s in cfg.sources]}


@app.get("/api/v1/sources/{source_id}")
def get_source(source_id: str):
    cfg = _get_config()
    s = cfg.get_source(source_id)
    if not s:
        raise HTTPException(404, detail=f"Source not found: {source_id}")
    return _source_dict(s)


# ── Policy ────────────────────────────────────────────────────────────────

@app.get("/api/v1/policy")
def get_policy():
    cfg = _get_config()
    return {
        "taxonomy": cfg.policy.taxonomy,
        "rules": [{"id": r.id, "effect": r.effect, "description": r.description, "when": r.when}
                  for r in cfg.policy.rules],
        "allow_list": cfg.policy.allow_list,
    }


# ── Dry Run / Validation ─────────────────────────────────────────────────

@app.post("/api/v1/config/dry-run")
async def config_dry_run(request: Request):
    """Validate a config YAML without applying it."""
    body = await request.body()
    result = dry_run(body.decode("utf-8"))
    status = 200 if result.get("valid") else 422
    return JSONResponse(result, status_code=status)


@app.post("/api/v1/config/reload")
def config_reload():
    """Force config reload from file."""
    if not _watcher:
        raise HTTPException(503, detail="No config watcher active")
    cfg = _watcher.check_and_reload()
    if cfg:
        return {"reloaded": True, "version": cfg.platform.version, "hash": cfg.derived.config_hash}
    return {"reloaded": False, "reason": "No changes detected or reload failed"}


# ── Helpers ───────────────────────────────────────────────────────────────

def _wall_dict(w) -> dict:
    d = {"id": w.id, "type": w.type, "resolution": w.resolution,
         "latency_class": w.latency_class, "tile_count": w.tile_count, "tags": w.tags}
    if w.classification:
        d["classification"] = w.classification
    if w.grid:
        d["grid"] = {"rows": w.grid.rows, "cols": w.grid.cols}
    if w.type == "bigscreen":
        d["screens"] = w.screens
    return d


def _source_dict(s) -> dict:
    d = {"id": s.id, "type": s.type, "tags": s.tags}
    if s.endpoint:
        d["endpoint"] = s.endpoint
    if s.codec:
        d["codec"] = s.codec
    if s.resolution:
        d["resolution"] = s.resolution
    if s.bitrate_kbps:
        d["bitrate_kbps"] = s.bitrate_kbps
    return d
