#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

LOG = logging.getLogger("vw.tile-player")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_mpv_cmd(*, token: str, sfu_url: str, room_id: str, display: Optional[str]) -> List[str]:
    """Minimal placeholder command.

    In a real deployment, token would be used to build an authenticated URL
    or injected into WebRTC signalling; here we pass it via environment.
    """
    mpv = shutil.which("mpv") or "mpv"
    # Example: play an HLS/RTSP/SRT URL derived from sfu_url/room_id (deployment-specific).
    # We keep it generic; operators should adapt URL template.
    stream_url = f"{sfu_url.rstrip('/')}/play/{room_id}"
    cmd = [mpv, "--no-terminal", "--fullscreen", "--really-quiet", stream_url]
    if display:
        cmd += [f"--screen={display}"]
    # Raspberry Pi: recommend --hwdec=v4l2m2m (note in README).
    return cmd


def run_with_watchdog(cmd: List[str], *, max_restarts: int = 10) -> int:
    restart_count = 0
    while True:
        LOG.info("exec: %s", " ".join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = p.wait()
        if rc == 0:
            return 0
        restart_count += 1
        if restart_count > max_restarts:
            LOG.error("max restarts exceeded (%d); exit rc=%d", max_restarts, rc)
            return rc
        delay = min(5 * restart_count, 30)
        LOG.warning("player exited rc=%d; restarting in %ds", rc, delay)
        time.sleep(delay)


def main() -> int:
    ap = argparse.ArgumentParser(description="Videowall Tile Player wrapper")
    ap.add_argument("--tile-id", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--sfu-url", required=True)
    ap.add_argument("--room-id", required=True)
    ap.add_argument("--display", default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    _setup_logging(args.log_level)

    # Token is exposed to player via env var (adapt as needed).
    os.environ["VW_SUBSCRIBE_TOKEN"] = args.token

    cmd = build_mpv_cmd(token=args.token, sfu_url=args.sfu_url, room_id=args.room_id, display=args.display)
    return run_with_watchdog(cmd)

if __name__ == "__main__":
    raise SystemExit(main())
