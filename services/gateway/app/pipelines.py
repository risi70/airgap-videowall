from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .models import IngestDefinition


@dataclass(frozen=True)
class PipelineSpec:
    argv: List[str]
    pretty: str


def _gst(cmd: str) -> PipelineSpec:
    # Use gst-launch-1.0 -e to propagate EOS properly.
    argv = ["gst-launch-1.0", "-e"] + cmd.split()
    return PipelineSpec(argv=argv, pretty="gst-launch-1.0 -e " + cmd)


def build_ingest_pipeline(ing: IngestDefinition) -> PipelineSpec:
    # Parse / normalize
    if ing.input_protocol == "rtsp":
        # rtspsrc outputs RTP; depay + parse
        cmd = (
            f"rtspsrc location={ing.input_url} latency={ing.rtsp_latency_ms} "
            f"protocols={ing.rtsp_protocols} ! rtph264depay ! h264parse "
            f"! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    if ing.input_protocol == "srt":
        # Expect MPEG-TS carrying H.264; demux -> parse -> remux to SRT
        cmd = (
            f"srtsrc uri={ing.input_url} ! tsdemux ! h264parse "
            f"! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    if ing.input_protocol == "rtp":
        if not ing.rtp_port:
            raise ValueError("rtp_port is required for input_protocol=rtp")
        cmd = (
            f"udpsrc port={ing.rtp_port} caps={ing.rtp_caps} "
            f"! rtph264depay ! h264parse ! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    raise ValueError(f"Unsupported input_protocol={ing.input_protocol}")
