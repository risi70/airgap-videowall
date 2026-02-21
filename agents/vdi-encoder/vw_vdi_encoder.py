#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socketserver
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

LOG = logging.getLogger("vw.vdi-encoder")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@dataclass
class EncoderState:
    started_at: float
    last_exit_code: Optional[int] = None
    restarts: int = 0
    running: bool = False

    def as_health(self) -> Dict[str, object]:
        return {
            "running": self.running,
            "started_at": int(self.started_at),
            "uptime_s": int(time.time() - self.started_at),
            "restarts": self.restarts,
            "last_exit_code": self.last_exit_code,
        }


class Handler(http.server.BaseHTTPRequestHandler):
    state: EncoderState = EncoderState(started_at=time.time())

    def _send(self, code: int, body: str, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, json.dumps(self.state.as_health()))
            return
        if self.path == "/metrics":
            # Minimal Prometheus exposition
            s = self.state
            metrics = []
            metrics.append("# HELP vw_vdi_encoder_running 1 if pipeline is running")
            metrics.append("# TYPE vw_vdi_encoder_running gauge")
            metrics.append(f"vw_vdi_encoder_running {1 if s.running else 0}")
            metrics.append("# HELP vw_vdi_encoder_restarts Number of pipeline restarts")
            metrics.append("# TYPE vw_vdi_encoder_restarts counter")
            metrics.append(f"vw_vdi_encoder_restarts {s.restarts}")
            if s.last_exit_code is not None:
                metrics.append("# HELP vw_vdi_encoder_last_exit_code Last pipeline exit code")
                metrics.append("# TYPE vw_vdi_encoder_last_exit_code gauge")
                metrics.append(f"vw_vdi_encoder_last_exit_code {s.last_exit_code}")
            body = "\n".join(metrics) + "\n"
            self._send(200, body, "text/plain; version=0.0.4")
            return
        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt: str, *args) -> None:
        # Quiet default http.server logging (use main logger).
        LOG.debug("http: " + fmt, *args)


def build_pipeline(
    *,
    display: str,
    resolution: str,
    fps: int,
    bitrate_kbps: int,
    output_mode: str,
    output_url: str,
) -> str:
    # Common
    caps = f"video/x-raw,framerate={fps}/1"
    if resolution:
        w, h = resolution.lower().split("x")
        caps += f",width={int(w)},height={int(h)}"

    # ximagesrc uses DISPLAY environment; set in systemd unit or arg.
    # Low-latency H.264 encode:
    enc = f"x264enc tune=zerolatency speed-preset=veryfast bitrate={bitrate_kbps} key-int-max={fps} bframes=0"
    if output_mode.lower() == "srt":
        # mpegts over SRT
        return f"ximagesrc use-damage=0 ! videoconvert ! videoscale ! {caps} ! {enc} ! h264parse config-interval=1 ! mpegtsmux ! srtsink uri=\"{output_url}\""
    if output_mode.lower() == "rtp":
        # RTP over UDP (output_url like udp://ip:port)
        return f"ximagesrc use-damage=0 ! videoconvert ! videoscale ! {caps} ! {enc} ! rtph264pay pt=96 config-interval=1 ! udpsink host={output_url.split(':')[0]} port={output_url.split(':')[1]}"
    if output_mode.lower() == "webrtc":
        # WebRTC publish via webrtcbin to Janus VideoRoom.
        # Signaling is handled out-of-band by the Janus REST API or
        # a companion signaling helper.  webrtcbin negotiates ICE/DTLS
        # and sends SRTP directly to the Janus SFU.
        return (
            f"ximagesrc use-damage=0 ! videoconvert ! videoscale ! {caps} "
            f"! {enc} ! h264parse config-interval=1 "
            f"! rtph264pay pt=96 config-interval=1 "
            f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
            f"! webrtcbin name=sendrecv bundle-policy=max-bundle"
        )
    raise ValueError(f"Unsupported output_mode={output_mode}; expected srt|rtp|webrtc")


def run_pipeline(pipeline: str, *, display: str, state: EncoderState) -> int:
    cmd = ["gst-launch-1.0", "-e"] + pipeline.split()
    env = os.environ.copy()
    env["DISPLAY"] = display
    LOG.info("gst-launch: %s", " ".join(cmd))
    state.running = True
    try:
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = p.wait()
        return rc
    finally:
        state.running = False


def start_health_server(port: int, state: EncoderState) -> threading.Thread:
    Handler.state = state
    httpd = socketserver.TCPServer(("", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    LOG.info("health endpoint on :%d (/healthz, /metrics)", port)
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description="Videowall VDI encoder agent (GStreamer)")
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--display", default=":0")
    ap.add_argument("--resolution", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", type=int, default=4000, help="kbps")
    ap.add_argument("--output-mode", choices=["srt", "rtp", "webrtc"], default="srt")
    ap.add_argument("--output-url", required=True, help="SRT uri (srt://host:port?...) or rtp host:port")
    ap.add_argument("--health-port", type=int, default=9100)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    _setup_logging(args.log_level)

    state = EncoderState(started_at=time.time())
    start_health_server(args.health_port, state)

    pipeline = build_pipeline(
        display=args.display,
        resolution=args.resolution,
        fps=args.fps,
        bitrate_kbps=args.bitrate,
        output_mode=args.output_mode,
        output_url=args.output_url,
    )

    # watchdog loop
    max_restarts = 10
    for i in range(max_restarts + 1):
        rc = run_pipeline(pipeline, display=args.display, state=state)
        state.last_exit_code = rc
        if rc == 0:
            return 0
        state.restarts += 1
        delay = min(5 * (i + 1), 30)
        LOG.warning("pipeline exited rc=%d; restart in %ds", rc, delay)
        time.sleep(delay)

    LOG.error("max restarts exceeded; exiting")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
