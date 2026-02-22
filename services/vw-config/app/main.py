# SPDX-License-Identifier: EUPL-1.2
"""vw-config HTTP API — Configuration Authority service.

Endpoints:
  GET  /healthz                 → ok + active_hash + last_reload_ts
  GET  /api/v1/config           → active config (canonical JSON) + X-Config-Hash header
  GET  /api/v1/config/raw       → YAML as stored
  GET  /api/v1/config/version   → version + hash + loaded_from + loaded_at
  POST /api/v1/config/dry-run   → validate supplied YAML without applying
  POST /api/v1/config/reload    → force reload from disk
  GET  /api/v1/derived          → derived metrics
  GET  /api/v1/walls            → wall list
  GET  /api/v1/walls/{wall_id}  → single wall
  GET  /api/v1/sources          → source list
  GET  /api/v1/sources/{src_id} → single source
  GET  /api/v1/policy           → policy rules + taxonomy

Env:
  VW_CONFIG_PATH          (default /etc/videowall/platform-config.yaml)
  VW_CONFIG_POLL_INTERVAL (default 5, seconds)
  VW_CONFIG_EVENT_LOG     (default /var/lib/vw-config/events.jsonl)
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .config_authority import ConfigWatcher, PlatformConfig, dry_run

LOG = logging.getLogger("vw.config.api")

CONFIG_PATH = os.getenv("VW_CONFIG_PATH", "/etc/videowall/platform-config.yaml")
POLL_INTERVAL = float(os.getenv("VW_CONFIG_POLL_INTERVAL", "5"))

_watcher: ConfigWatcher | None = None
_watcher_thread: threading.Thread | None = None


def _get_config() -> PlatformConfig:
    if _watcher and _watcher.current:
        return _watcher.current
    raise HTTPException(status_code=503, detail="Configuration not loaded")


def _startup_watcher():
    """Initialise the file watcher. Called from lifespan or directly in tests."""
    global _watcher, _watcher_thread
    p = Path(CONFIG_PATH)
    if not p.exists():
        LOG.warning("Config not found: %s — 503 until config is present", CONFIG_PATH)
        return
    _watcher = ConfigWatcher(p, poll_interval=POLL_INTERVAL)
    _watcher.load_initial()
    # Start poll-loop in background (only load_initial has already run)
    def _poll_loop():
        while True:
            import time
            time.sleep(_watcher.poll_interval)
            _watcher.check_and_reload()
    _watcher_thread = threading.Thread(target=_poll_loop, daemon=True, name="config-watcher")
    _watcher_thread.start()
    LOG.info("Config watcher started: %s (poll=%ss)", CONFIG_PATH, POLL_INTERVAL)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _startup_watcher()
    yield


app = FastAPI(
    title="vw-config",
    version="0.3.0",
    description="Configuration Authority — declarative YAML-driven platform config",
    lifespan=_lifespan,
)


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    if _watcher and _watcher.current:
        resp: dict = {
            "status": "ok",
            "active_hash": _watcher.current.derived.config_hash,
            "last_reload_ts": _watcher.last_reload_ts,
        }
        if _watcher.last_error:
            resp["last_error"] = _watcher.last_error
        return resp
    return JSONResponse({"status": "no_config"}, status_code=503)


# ── Config (canonical JSON) ──────────────────────────────────────────────

@app.get("/api/v1/config")
def get_config():
    cfg = _get_config()
    return Response(
        content=cfg.canonical_json,
        media_type="application/json",
        headers={"X-Config-Hash": cfg.derived.config_hash},
    )


@app.get("/api/v1/config/raw")
def get_config_raw():
    cfg = _get_config()
    return PlainTextResponse(
        content=cfg.raw_yaml,
        headers={"X-Config-Hash": cfg.derived.config_hash},
    )


@app.get("/api/v1/config/version")
def get_version():
    cfg = _get_config()
    return {
        "version": cfg.platform.version,
        "config_hash": cfg.derived.config_hash,
        "loaded_from": cfg.loaded_from,
        "loaded_at": cfg.loaded_at,
    }


# ── Derived Metrics ──────────────────────────────────────────────────────

@app.get("/api/v1/derived")
def get_derived():
    cfg = _get_config()
    d = cfg.derived
    return {
        "total_walls": d.total_walls,
        "tile_walls": d.tile_walls,
        "bigscreen_walls": d.bigscreen_walls,
        "total_tiles": d.total_tiles,
        "total_screens": d.total_screens,
        "total_display_endpoints": d.total_display_endpoints,
        "total_sources": d.total_sources,
        "sources_by_type": d.sources_by_type,
        "sfu_rooms_needed": d.sfu_rooms_needed,
        "mosaic_pipelines_needed": d.mosaic_pipelines_needed,
        "estimated_bandwidth_gbps": d.estimated_bandwidth_gbps,
        "worst_case_concurrency": d.worst_case_concurrency,
        "concurrency_headroom": d.concurrency_headroom,
        "config_hash": d.config_hash,
    }


# ── Walls / Sources / Policy ─────────────────────────────────────────────

@app.get("/api/v1/walls")
def list_walls():
    return {"walls": [_wall_dict(w) for w in _get_config().walls]}

@app.get("/api/v1/walls/{wall_id}")
def get_wall(wall_id: str):
    w = _get_config().get_wall(wall_id)
    if not w:
        raise HTTPException(404, detail=f"Wall not found: {wall_id}")
    return _wall_dict(w)

@app.get("/api/v1/sources")
def list_sources():
    return {"sources": [_source_dict(s) for s in _get_config().sources]}

@app.get("/api/v1/sources/{source_id}")
def get_source(source_id: str):
    s = _get_config().get_source(source_id)
    if not s:
        raise HTTPException(404, detail=f"Source not found: {source_id}")
    return _source_dict(s)

@app.get("/api/v1/policy")
def get_policy():
    cfg = _get_config()
    return {
        "taxonomy": cfg.policy.taxonomy,
        "rules": [{"id": r.id, "effect": r.effect,
                    "description": r.description, "when": r.when}
                  for r in cfg.policy.rules],
        "allow_list": cfg.policy.allow_list,
    }


# ── Dry Run / Reload ─────────────────────────────────────────────────────

@app.post("/api/v1/config/dry-run")
async def config_dry_run(request: Request):
    body = await request.body()
    result = dry_run(body.decode("utf-8"))
    code = 200 if result.get("valid") else 400
    return JSONResponse(result, status_code=code)

@app.post("/api/v1/config/reload")
def config_reload():
    if not _watcher:
        raise HTTPException(503, detail="No config watcher active")
    cfg = _watcher.force_reload()
    if cfg:
        return {"reloaded": True, "version": cfg.platform.version,
                "hash": cfg.derived.config_hash}
    return JSONResponse(
        {"reloaded": False, "error": _watcher.last_error or "No changes"},
        status_code=200,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _wall_dict(w) -> dict:
    d: dict = {"id": w.id, "type": w.type, "classification": w.classification,
               "resolution": w.resolution, "latency_class": w.latency_class,
               "tile_count": w.tile_count, "tags": w.tags}
    if w.grid:
        d["grid"] = {"rows": w.grid.rows, "cols": w.grid.cols}
    if w.type == "bigscreen":
        d["screens"] = w.screens
    return d

def _source_dict(s) -> dict:
    d: dict = {"id": s.id, "type": s.type, "tags": s.tags}
    if s.endpoint:
        d["endpoint"] = s.endpoint
    if s.codec:
        d["codec"] = s.codec
    if s.resolution:
        d["resolution"] = s.resolution
    if s.bitrate_kbps:
        d["bitrate_kbps"] = s.bitrate_kbps
    return d
