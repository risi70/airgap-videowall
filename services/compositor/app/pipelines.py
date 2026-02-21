from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Tuple

from .models import MosaicDefinition, MosaicInput


@dataclass(frozen=True)
class PipelineSpec:
    argv: List[str]
    pretty: str


def _has_dri() -> bool:
    return os.path.exists("/dev/dri/renderD128")


def _gst(cmd: str) -> PipelineSpec:
    argv = ["gst-launch-1.0", "-e"] + cmd.split()
    return PipelineSpec(argv=argv, pretty="gst-launch-1.0 -e " + cmd)


def _parse_res(res: str) -> Tuple[int, int]:
    w, h = res.lower().split("x")
    return int(w), int(h)


def _cell_xy(pos: int, cols: int, cell_w: int, cell_h: int) -> Tuple[int, int]:
    r = pos // cols
    c = pos % cols
    return c * cell_w, r * cell_h


def _src_element(inp: MosaicInput) -> str:
    # In this module we assume inputs are SRT MPEG-TS/H.264 or H.265.
    # If RTSP is provided, we depayload H.264.
    if inp.source_protocol == "srt":
        # srtsrc -> tsdemux -> queue
        return f"srtsrc uri={inp.source_url} ! tsdemux name=demux_{inp.source_id} demux_{inp.source_id}. ! queue"
    if inp.source_protocol == "rtsp":
        return f"rtspsrc location={inp.source_url} latency=200 protocols=tcp ! rtph264depay ! queue"
    if inp.source_protocol == "rtp":
        # Require udp port in source_url like "udp://0.0.0.0:5004" is not supported directly; treat as caps-less udp
        return f"udpsrc port=5004 ! rtph264depay ! queue"
    raise ValueError(f"Unsupported source_protocol={inp.source_protocol}")


def build_mosaic_pipeline(m: MosaicDefinition) -> PipelineSpec:
    out_w, out_h = _parse_res(m.resolution)
    cell_w = out_w // m.grid_cols
    cell_h = out_h // m.grid_rows

    use_gpu = _has_dri()
    mixer = "glvideomixer" if use_gpu else "compositor"

    # Build per-input branch:
    # src -> decodebin -> videoconvert -> videoscale -> capsfilter -> queue -> mixer.sink_N (with xpos/ypos)
    branches = []
    mixer_sinks = []
    for idx, inp in enumerate(m.inputs):
        if not inp.source_url:
            raise ValueError(f"inputs[{idx}].source_url is required in this module build")
        xpos, ypos = _cell_xy(inp.position, m.grid_cols, cell_w, cell_h)
        # size based on span
        tw = cell_w * max(1, inp.width)
        th = cell_h * max(1, inp.height)

        # For SRT, demux provides elementary stream(s). Use decodebin.
        src = _src_element(inp)
        branch = (
            f"{src} ! decodebin ! videoconvert ! videoscale "
            f"! video/x-raw,width={tw},height={th},framerate={m.fps}/1 "
            f"! queue ! {mixer}.sink_{idx}"
        )
        branches.append(branch)
        mixer_sinks.append(f"{mixer}.sink_{idx}::xpos={xpos} {mixer}.sink_{idx}::ypos={ypos}")

    # Mixer output -> encoder -> mux -> sink
    if m.codec == "hevc":
        enc = "vaapih265enc" if use_gpu else "x265enc"
    else:
        enc = "vaapih264enc" if use_gpu else "x264enc"

    # Note: bitrate tuning is left to values/env; default to sane, low-latency settings.
    enc_props = "key-int-max=60" if "x264" in enc or "vaapi" in enc else ""

    sink = f"mpegtsmux ! srtsink uri={m.output_url}"

    cmd = (
        f"{' '.join(branches)} {mixer} name={mixer} "
        f"{' '.join(mixer_sinks)} "
        f"! videoconvert ! {enc} {enc_props} ! {sink}"
    ).strip()

    return _gst(cmd)
