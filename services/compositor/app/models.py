from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class MosaicInput(BaseModel):
    source_id: str
    position: int = Field(..., description="0-based cell index in row-major order")
    width: int = 1
    height: int = 1
    # Optional explicit source URL override (otherwise compositor resolves via mgmt-api in real system)
    source_url: Optional[str] = None
    source_protocol: Literal["srt", "rtsp", "rtp", "webrtc"] = "srt"


class MosaicDefinition(BaseModel):
    id: str = Field(..., description="Client-generated ID")
    wall_id: str
    name: str
    resolution: str = "3840x2160"
    fps: int = 30
    codec: Literal["hevc", "h264"] = "hevc"
    output_mode: Literal["srt", "webrtc"] = "srt"
    output_url: str
    grid_cols: int = 2
    grid_rows: int = 2
    inputs: List[MosaicInput] = Field(default_factory=list)

    running: bool = False
    pid: Optional[int] = None
