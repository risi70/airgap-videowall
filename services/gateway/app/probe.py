from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Optional

from .models import ProbeRequest, ProbeResponse


def _run_ffprobe(url: str, timeout_s: int = 10) -> Dict[str, Any]:
    # Keep ffprobe quiet but machine-readable.
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        url,
    ]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "ffprobe failed").strip())
    return json.loads(completed.stdout)


def probe(req: ProbeRequest) -> ProbeResponse:
    try:
        data = _run_ffprobe(req.url, timeout_s=10)

        streams = data.get("streams", []) or []
        fmt = data.get("format", {}) or {}

        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)

        codec = v.get("codec_name") if v else None
        width = v.get("width") if v else None
        height = v.get("height") if v else None
        resolution = f"{width}x{height}" if (width and height) else None

        fps = None
        if v and v.get("avg_frame_rate"):
            # avg_frame_rate like "25/1"
            num, den = v["avg_frame_rate"].split("/")
            if den != "0":
                fps = float(num) / float(den)

        bitrate = None
        br = (v or {}).get("bit_rate") or fmt.get("bit_rate")
        if br:
            try:
                bitrate = int(int(br) / 1000)
            except Exception:
                bitrate = None

        return ProbeResponse(
            reachable=True,
            codec=codec,
            resolution=resolution,
            fps=fps,
            bitrate_kbps=bitrate,
            audio=bool(a),
        )
    except subprocess.TimeoutExpired:
        return ProbeResponse(reachable=False, error="ffprobe timeout")
    except Exception as e:
        return ProbeResponse(reachable=False, error=str(e))
