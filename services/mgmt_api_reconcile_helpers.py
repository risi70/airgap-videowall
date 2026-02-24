"""Re-export pure mapping functions from reconcile module for testing.

This avoids importing the full async DB/httpx stack in unit tests.
"""

import os
import sys

# Make the mapping functions importable without triggering DB/httpx imports
# by extracting the pure logic directly.

_TYPE_MAP_WALL = {"tiles": "tilewall", "bigscreen": "bigscreen"}
_TYPE_MAP_SRC = {"webrtc": "vdi", "srt": "hdmi", "rtsp": "hdmi", "rtp": "hdmi"}
_PROTO_MAP = {"webrtc": "webrtc", "srt": "srt", "rtsp": "rtsp", "rtp": "rtp"}


def config_tag(config_id: str) -> str:
    return f"config:{config_id}"


def wall_to_db(w: dict) -> dict:
    grid = w.get("grid") or {}
    tile_count = grid.get("rows", 1) * grid.get("cols", 1) if grid else w.get("screens", 1)
    raw_tags = w.get("tags") or {}
    tag_list = [f"{k}:{v}" for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else list(raw_tags)
    tag_list.append(config_tag(w["id"]))
    return {
        "name": str(w["id"]),
        "wall_type": _TYPE_MAP_WALL.get(w.get("type", "tiles"), "tilewall"),
        "tile_count": tile_count,
        "resolution": w.get("resolution", "1920x1080"),
        "tags": sorted(set(tag_list)),
    }


def source_to_db(s: dict) -> dict:
    src_type = _TYPE_MAP_SRC.get(s.get("type", "srt"), "hdmi")
    protocol = _PROTO_MAP.get(s.get("type", "srt"), "other")
    raw_tags = s.get("tags") or {}
    tag_list = [f"{k}:{v}" for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else list(raw_tags)
    tag_list.append(config_tag(s["id"]))
    return {
        "name": str(s["id"]),
        "source_type": src_type,
        "protocol": protocol,
        "endpoint_url": s.get("endpoint", ""),
        "codec": s.get("codec", "h264"),
        "tags": sorted(set(tag_list)),
        "health_status": "unknown",
    }
