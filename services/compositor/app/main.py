from __future__ import annotations

import threading
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import PlainTextResponse

from .models import MosaicDefinition
from .pipelines import build_mosaic_pipeline
from .policy import evaluate_source_access
from .process import ProcHandle, start_process, stop_process

APP_NAME = "vw-compositor"

app = FastAPI(title=APP_NAME, version="0.1.0")

_mosaics: Dict[str, MosaicDefinition] = {}
_processes: Dict[str, ProcHandle] = {}


def _drain_stderr(handle: ProcHandle) -> None:
    try:
        for _ in handle.popen.stderr:
            if handle.popen.poll() is not None:
                break
    except Exception:
        pass


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/mosaics")
def list_mosaics():
    return list(_mosaics.values())


@app.post("/mosaics")
async def create_or_update_mosaic(m: MosaicDefinition, x_user: Optional[str] = Header(default=None)):
    # Enforce policy for each input source_id
    for inp in m.inputs:
        allowed = await evaluate_source_access(inp.source_id, user=x_user)
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Policy denied source_id={inp.source_id}")

    if m.id in _processes and _processes[m.id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Mosaic is running; stop it before updating.")

    _mosaics[m.id] = m
    return m


@app.get("/mosaics/{mosaic_id}")
def get_mosaic(mosaic_id: str):
    m = _mosaics.get(mosaic_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mosaic not found")
    return m


@app.delete("/mosaics/{mosaic_id}")
def delete_mosaic(mosaic_id: str):
    if mosaic_id in _processes and _processes[mosaic_id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Mosaic is running; stop it before deleting.")
    _mosaics.pop(mosaic_id, None)
    _processes.pop(mosaic_id, None)
    return {"deleted": mosaic_id}


@app.post("/mosaics/{mosaic_id}/start")
def start_mosaic(mosaic_id: str):
    m = _mosaics.get(mosaic_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mosaic not found")
    if mosaic_id in _processes and _processes[mosaic_id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Already running")

    try:
        spec = build_mosaic_pipeline(m)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    handle = start_process(spec)
    _processes[mosaic_id] = handle

    m.running = True
    m.pid = handle.popen.pid
    _mosaics[mosaic_id] = m

    threading.Thread(target=_drain_stderr, args=(handle,), daemon=True).start()

    return {"started": mosaic_id, "pid": handle.popen.pid, "pipeline": spec.pretty}


@app.post("/mosaics/{mosaic_id}/stop")
def stop_mosaic(mosaic_id: str):
    handle = _processes.get(mosaic_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Mosaic not running")
    stop_process(handle)
    _processes.pop(mosaic_id, None)

    m = _mosaics.get(mosaic_id)
    if m:
        m.running = False
        m.pid = None
        _mosaics[mosaic_id] = m

    return {"stopped": mosaic_id}


@app.get("/mosaics/{mosaic_id}/logs", response_class=PlainTextResponse)
def mosaic_logs(mosaic_id: str) -> str:
    return "Logs are streamed to pod logs. Use `kubectl logs` for vw-compositor."
