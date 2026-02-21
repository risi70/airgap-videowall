#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from typing import List, Optional

LOG = logging.getLogger("vw.big-player")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_cmd(*, mode: str, stream_url: str, display: Optional[str]) -> List[str]:
    mode = mode.lower()
    if mode == "srt":
        # Use ffplay for SRT over mpegts, or mpv with srt:// URL if supported.
        ffplay = shutil.which("ffplay") or "ffplay"
        cmd = [ffplay, "-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0", stream_url]
    else:
        mpv = shutil.which("mpv") or "mpv"
        cmd = [mpv, "--no-terminal", "--fullscreen", "--really-quiet", stream_url]
    if display:
        # mpv supports --screen; ffplay uses SDL display selection via SDL_VIDEO_FULLSCREEN_DISPLAY
        # Keep generic; operators can wrap with env vars if needed.
        pass
    return cmd


def run_watchdog(cmd: List[str], max_restarts: int = 10) -> int:
    restarts = 0
    while True:
        LOG.info("exec: %s", " ".join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = p.wait()
        if rc == 0:
            return 0
        restarts += 1
        if restarts > max_restarts:
            LOG.error("max restarts exceeded; rc=%d", rc)
            return rc
        delay = min(5 * restarts, 30)
        LOG.warning("player exited rc=%d; restarting in %ds", rc, delay)
        time.sleep(delay)


def main() -> int:
    ap = argparse.ArgumentParser(description="Videowall Big Screen Player")
    ap.add_argument("--wall-id", required=True)
    ap.add_argument("--mode", choices=["srt", "webrtc"], required=True)
    ap.add_argument("--stream-url", required=True)
    ap.add_argument("--display", default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    _setup_logging(args.log_level)

    cmd = build_cmd(mode=args.mode, stream_url=args.stream_url, display=args.display)
    return run_watchdog(cmd)

if __name__ == "__main__":
    raise SystemExit(main())
