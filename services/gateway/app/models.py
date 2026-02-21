from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal, Optional


class ProbeRequest(BaseModel):
    url: str
    protocol: Literal["rtsp", "srt", "rtp", "http", "https", "file"]


class ProbeResponse(BaseModel):
    reachable: bool
    codec: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    audio: Optional[bool] = None
    error: Optional[str] = None


class IngestDefinition(BaseModel):
    id: str = Field(..., description="Client-generated ID")
    name: str
    input_url: str
    input_protocol: Literal["rtsp", "srt", "rtp"]
    output_url: str
    output_protocol: Literal["srt", "webrtc"] = "srt"

    # Janus WebRTC republish fields (required when output_protocol=webrtc)
    janus_url: Optional[str] = Field(default=None, description="Janus HTTP/WS base URL for signaling")
    janus_room_id: Optional[int] = Field(default=None, description="Janus VideoRoom room ID")
    janus_token: Optional[str] = Field(default=None, description="Janus auth token for publish")

    # RTP specifics
    rtp_port: Optional[int] = Field(default=None, description="Required for input_protocol=rtp")
    rtp_caps: Optional[str] = Field(
        default="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
        description="GStreamer caps string for udpsrc (input_protocol=rtp).",
    )

    # RTSP specifics
    rtsp_latency_ms: int = 200
    rtsp_protocols: str = "tcp"

    # SRT specifics
    srt_mode: str = "caller"  # caller|listener|rendezvous (passed as part of uri typically)

    # Operational
    running: bool = False
    pid: Optional[int] = None
