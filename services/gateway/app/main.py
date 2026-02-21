from __future__ import annotations

import os
import threading
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from .models import IngestDefinition, ProbeRequest, ProbeResponse
from .pipelines import build_ingest_pipeline
from .probe import probe
from .process import ProcHandle, start_process, stop_process

APP_NAME = "vw-gw"
LOG_TAIL_LINES = int(os.getenv("VW_GW_LOG_TAIL_LINES", "200"))

app = FastAPI(title=APP_NAME, version="0.1.0")

_ingests: Dict[str, IngestDefinition] = {}
_processes: Dict[str, ProcHandle] = {}


def _tail_reader(ingest_id: str, handle: ProcHandle) -> None:
    # Best-effort background drain to avoid pipe blockage.
    try:
        for _ in handle.popen.stderr:
            if handle.popen.poll() is not None:
                break
    except Exception:
        pass


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.post("/probe", response_model=ProbeResponse)
def probe_endpoint(req: ProbeRequest) -> ProbeResponse:
    return probe(req)


@app.get("/ingests")
def list_ingests():
    return list(_ingests.values())


@app.post("/ingests")
def create_or_update_ingest(ing: IngestDefinition):
    if ing.id in _processes and _processes[ing.id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Ingest is running; stop it before updating.")
    _ingests[ing.id] = ing
    return ing


@app.delete("/ingests/{ingest_id}")
def delete_ingest(ingest_id: str):
    if ingest_id in _processes and _processes[ingest_id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Ingest is running; stop it before deleting.")
    _ingests.pop(ingest_id, None)
    _processes.pop(ingest_id, None)
    return {"deleted": ingest_id}


@app.post("/ingests/{ingest_id}/start")
def start_ingest(ingest_id: str):
    ing = _ingests.get(ingest_id)
    if not ing:
        raise HTTPException(status_code=404, detail="Ingest not found")
    if ingest_id in _processes and _processes[ingest_id].popen.poll() is None:
        raise HTTPException(status_code=409, detail="Already running")

    try:
        spec = build_ingest_pipeline(ing)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    handle = start_process(spec)
    _processes[ingest_id] = handle

    ing.running = True
    ing.pid = handle.popen.pid
    _ingests[ingest_id] = ing

    threading.Thread(target=_tail_reader, args=(ingest_id, handle), daemon=True).start()

    return {"started": ingest_id, "pid": handle.popen.pid, "pipeline": spec.pretty}


@app.post("/ingests/{ingest_id}/stop")
def stop_ingest(ingest_id: str):
    handle = _processes.get(ingest_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Ingest not running")
    stop_process(handle)
    _processes.pop(ingest_id, None)

    ing = _ingests.get(ingest_id)
    if ing:
        ing.running = False
        ing.pid = None
        _ingests[ingest_id] = ing

    return {"stopped": ingest_id}


@app.get("/ingests/{ingest_id}/logs", response_class=PlainTextResponse)
def ingest_logs(ingest_id: str) -> str:
    handle = _processes.get(ingest_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Ingest not running")
    # We do not persist logs; return last chunk from current stderr buffer if available.
    # If stderr is not seekable, this returns a note.
    return "Logs are streamed to pod logs. Use `kubectl logs` for vw-gw."
