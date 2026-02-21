from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

WallType = Literal["tilewall", "bigscreen"]
SourceType = Literal["vdi", "hdmi"]
Protocol = Literal["rtsp", "rtp", "srt", "webrtc", "http", "other"]


class WallIn(BaseModel):
    name: str
    wall_type: WallType
    tile_count: int = Field(ge=1)
    resolution: str
    tags: list[str] = Field(default_factory=list)


class Wall(WallIn):
    id: int


class SourceIn(BaseModel):
    name: str
    source_type: SourceType
    protocol: Protocol
    endpoint_url: str
    codec: str = "h264"
    tags: list[str] = Field(default_factory=list)
    health_status: str = "unknown"


class Source(SourceIn):
    id: int


class LayoutIn(BaseModel):
    wall_id: int
    name: str
    grid_config: dict[str, Any]
    preset_name: str = ""
    is_active: bool = False


class Layout(BaseModel):
    id: int
    wall_id: int
    name: str
    version: int
    grid_config: dict[str, Any]
    preset_name: str
    is_active: bool
    created_by: str
    created_at: str


class PolicyEvalRequest(BaseModel):
    wall_id: int
    source_id: int
    operator_id: str
    operator_roles: list[str] = Field(default_factory=list)
    operator_tags: list[str] = Field(default_factory=list)


class PolicyEvalResponse(BaseModel):
    allowed: bool
    reason: str
    matched_rules: list[dict[str, Any]] = Field(default_factory=list)


class TokenSubscribeRequest(BaseModel):
    wall_id: int
    source_id: int
    tile_id: str


class TokenSubscribeResponse(BaseModel):
    allowed: bool
    reason: str
    token: Optional[str] = None


class BundleExport(BaseModel):
    walls: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    active_layouts: list[dict[str, Any]]


class BundleImportRequest(BaseModel):
    ring: Literal["dev", "test", "prod"]
    payload: dict[str, Any]
    hmac_hex: Optional[str] = None


class AuditEventOut(BaseModel):
    id: int
    ts: str
    action: str
    actor: str
    object_type: str
    object_id: str
    details: dict[str, Any]
    prev_hash: str
    hash: str


class WhoAmI(BaseModel):
    sub: str = ""
    preferred_username: str = ""
    roles: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)
