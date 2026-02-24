"""Tests for mgmt-api config reconciliation (mapping + upsert logic)."""

from __future__ import annotations

import pytest

# Import mapping helpers directly — they're pure functions, no DB needed
from services.mgmt_api_reconcile_helpers import (
    wall_to_db,
    source_to_db,
    config_tag,
)


# ── _config_tag ──────────────────────────────────────────────────────────

def test_config_tag():
    assert config_tag("wall-alpha") == "config:wall-alpha"
    assert config_tag("vdi-01") == "config:vdi-01"


# ── _wall_to_db mapping ────────────────────────────────────────────────

class TestWallMapping:

    def test_tile_wall(self):
        cw = {
            "id": "wall-alpha",
            "type": "tiles",
            "classification": "confidential",
            "resolution": "1920x1080",
            "latency_class": "interactive",
            "tile_count": 24,
            "grid": {"rows": 6, "cols": 4},
            "tags": {"mission": "alpha", "room": "ops-center"},
        }
        db = wall_to_db(cw)
        assert db["name"] == "wall-alpha"
        assert db["wall_type"] == "tilewall"
        assert db["tile_count"] == 24  # 6 × 4
        assert db["resolution"] == "1920x1080"
        assert "config:wall-alpha" in db["tags"]
        assert "mission:alpha" in db["tags"]
        assert "room:ops-center" in db["tags"]

    def test_bigscreen_wall(self):
        cw = {
            "id": "wall-charlie",
            "type": "bigscreen",
            "classification": "confidential",
            "resolution": "3840x2160",
            "latency_class": "broadcast",
            "screens": 2,
            "tags": {"mission": "alpha"},
        }
        db = wall_to_db(cw)
        assert db["wall_type"] == "bigscreen"
        assert db["tile_count"] == 2  # screens fallback
        assert "config:wall-charlie" in db["tags"]

    def test_missing_grid_defaults(self):
        cw = {"id": "wall-x", "type": "tiles", "tags": {}}
        db = wall_to_db(cw)
        assert db["tile_count"] == 1  # no grid → 1×1
        assert db["resolution"] == "1920x1080"  # default

    def test_tags_as_list(self):
        cw = {"id": "wall-y", "type": "tiles", "tags": ["a", "b"]}
        db = wall_to_db(cw)
        assert "a" in db["tags"]
        assert "b" in db["tags"]
        assert "config:wall-y" in db["tags"]

    def test_tags_deduplication(self):
        cw = {"id": "wall-z", "type": "tiles", "tags": {"x": "1", "x": "1"}}
        db = wall_to_db(cw)
        assert db["tags"].count("x:1") == 1


# ── _source_to_db mapping ──────────────────────────────────────────────

class TestSourceMapping:

    def test_vdi_source(self):
        cs = {
            "id": "vdi-01",
            "type": "webrtc",
            "codec": "h264",
            "resolution": "1920x1080",
            "bitrate_kbps": 6000,
            "tags": {"classification": "confidential", "mission": "alpha"},
        }
        db = source_to_db(cs)
        assert db["name"] == "vdi-01"
        assert db["source_type"] == "vdi"
        assert db["protocol"] == "webrtc"
        assert db["endpoint_url"] == ""  # no endpoint for WebRTC
        assert db["codec"] == "h264"
        assert "config:vdi-01" in db["tags"]
        assert "classification:confidential" in db["tags"]

    def test_srt_source(self):
        cs = {
            "id": "hdmi-01",
            "type": "srt",
            "endpoint": "srt://10.10.10.20:9000",
            "codec": "h264",
            "tags": {"classification": "confidential"},
        }
        db = source_to_db(cs)
        assert db["source_type"] == "hdmi"
        assert db["protocol"] == "srt"
        assert db["endpoint_url"] == "srt://10.10.10.20:9000"
        assert "config:hdmi-01" in db["tags"]

    def test_rtsp_source(self):
        cs = {
            "id": "hdmi-02",
            "type": "rtsp",
            "endpoint": "rtsp://10.10.10.21:554/stream1",
            "codec": "h264",
            "tags": {},
        }
        db = source_to_db(cs)
        assert db["source_type"] == "hdmi"
        assert db["protocol"] == "rtsp"
        assert db["endpoint_url"] == "rtsp://10.10.10.21:554/stream1"

    def test_unknown_type_defaults(self):
        cs = {"id": "x", "type": "unknown", "tags": {}}
        db = source_to_db(cs)
        assert db["source_type"] == "hdmi"
        assert db["protocol"] == "other"

    def test_missing_fields_defaults(self):
        cs = {"id": "y", "tags": {}}
        db = source_to_db(cs)
        assert db["codec"] == "h264"
        assert db["endpoint_url"] == ""
        assert db["health_status"] == "unknown"
