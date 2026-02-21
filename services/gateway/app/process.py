from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Optional

from .pipelines import PipelineSpec


@dataclass
class ProcHandle:
    popen: subprocess.Popen
    spec: PipelineSpec


def start_process(spec: PipelineSpec) -> ProcHandle:
    # Start a new process group so we can terminate the whole pipeline.
    popen = subprocess.Popen(
        spec.argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )
    return ProcHandle(popen=popen, spec=spec)


def stop_process(handle: ProcHandle, timeout_s: int = 5) -> None:
    if handle.popen.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(handle.popen.pid), signal.SIGTERM)
        handle.popen.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(handle.popen.pid), signal.SIGKILL)
        handle.popen.wait(timeout=timeout_s)
