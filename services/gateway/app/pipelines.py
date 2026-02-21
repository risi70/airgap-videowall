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
        if ing.output_protocol == "webrtc":
            return _build_rtsp_to_webrtc(ing)
        cmd = (
            f"rtspsrc location={ing.input_url} latency={ing.rtsp_latency_ms} "
            f"protocols={ing.rtsp_protocols} ! rtph264depay ! h264parse "
            f"! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    if ing.input_protocol == "srt":
        # Expect MPEG-TS carrying H.264; demux -> parse -> remux
        if ing.output_protocol == "webrtc":
            return _build_srt_to_webrtc(ing)
        cmd = (
            f"srtsrc uri={ing.input_url} ! tsdemux ! h264parse "
            f"! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    if ing.input_protocol == "rtp":
        if not ing.rtp_port:
            raise ValueError("rtp_port is required for input_protocol=rtp")
        if ing.output_protocol == "webrtc":
            return _build_rtp_to_webrtc(ing)
        cmd = (
            f"udpsrc port={ing.rtp_port} caps={ing.rtp_caps} "
            f"! rtph264depay ! h264parse ! mpegtsmux ! srtsink uri={ing.output_url}"
        )
        return _gst(cmd)

    raise ValueError(f"Unsupported input_protocol={ing.input_protocol}")


# ---------------------------------------------------------------------------
# WebRTC republish pipelines
#
# These use the GStreamer webrtcbin element to publish into a Janus VideoRoom
# as a standard WebRTC publisher.  The signaling is handled by an external
# helper (vw-gw-webrtc-signaler) or the Janus REST API.
#
# Pipeline pattern:
#   source → tsdemux/depay → h264parse → rtph264pay pt=96
#     → webrtcbin name=sendrecv bundle-policy=max-bundle
#
# The signaling URL and room ID are passed via the IngestDefinition fields
# (output_url = Janus HTTP/WS base URL, janus_room_id, janus_token).
# ---------------------------------------------------------------------------


def _build_srt_to_webrtc(ing: IngestDefinition) -> PipelineSpec:
    """SRT MPEG-TS H.264 → WebRTC publish to Janus VideoRoom."""
    cmd = (
        f"srtsrc uri={ing.input_url} "
        f"! tsdemux "
        f"! h264parse config-interval=1 "
        f"! rtph264pay pt=96 config-interval=1 "
        f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
        f"! webrtcbin name=sendrecv bundle-policy=max-bundle"
    )
    return _gst(cmd)


def _build_rtsp_to_webrtc(ing: IngestDefinition) -> PipelineSpec:
    """RTSP H.264 → WebRTC publish to Janus VideoRoom."""
    cmd = (
        f"rtspsrc location={ing.input_url} latency={ing.rtsp_latency_ms} "
        f"protocols={ing.rtsp_protocols} "
        f"! rtph264depay "
        f"! h264parse config-interval=1 "
        f"! rtph264pay pt=96 config-interval=1 "
        f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
        f"! webrtcbin name=sendrecv bundle-policy=max-bundle"
    )
    return _gst(cmd)


def _build_rtp_to_webrtc(ing: IngestDefinition) -> PipelineSpec:
    """Raw RTP H.264 UDP → WebRTC publish to Janus VideoRoom."""
    cmd = (
        f"udpsrc port={ing.rtp_port} caps={ing.rtp_caps} "
        f"! rtph264depay "
        f"! h264parse config-interval=1 "
        f"! rtph264pay pt=96 config-interval=1 "
        f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
        f"! webrtcbin name=sendrecv bundle-policy=max-bundle"
    )
    return _gst(cmd)
