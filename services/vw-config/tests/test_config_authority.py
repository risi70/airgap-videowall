# SPDX-License-Identifier: EUPL-1.2
"""Tests for vw-config Configuration Authority."""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config_authority import (
    ConfigError, ConfigWatcher, canonical_json, dry_run, load_config,
)


# ── Fixtures ──────────────────────────────────────────────────────────────
# All fixtures must satisfy the stricter schema:
#   platform: version + max_concurrent_streams required
#   walls: id + type + classification + latency_class required
#   sources: id + type + tags (with classification) required

VALID_MINIMAL = """
platform:
  version: "1.0.0"
  max_concurrent_streams: 64
walls:
  - id: wall-a
    type: tiles
    classification: unclassified
    latency_class: interactive
    grid: { rows: 2, cols: 2 }
sources:
  - id: src-1
    type: webrtc
    tags: { classification: unclassified }
"""

VALID_FULL = """
platform:
  version: "2.0.0"
  max_concurrent_streams: 128
  codec_policy:
    tiles: h264
    mosaics: hevc
  latency_classes:
    interactive_max_ms: 400
    broadcast_max_ms: 5000
walls:
  - id: wall-alpha
    type: tiles
    classification: confidential
    grid: { rows: 6, cols: 4 }
    resolution: 1920x1080
    latency_class: interactive
    tags: { mission: alpha }
  - id: wall-beta
    type: bigscreen
    classification: secret
    screens: 2
    resolution: 3840x2160
    latency_class: broadcast
    tags: { mission: beta }
sources:
  - id: vdi-01
    type: webrtc
    codec: h264
    bitrate_kbps: 6000
    tags: { classification: confidential }
  - id: hdmi-01
    type: srt
    endpoint: "srt://10.10.10.1:9000"
    bitrate_kbps: 8000
    tags: { classification: secret }
policy:
  taxonomy:
    classifications: ["unclassified", "confidential", "secret"]
  rules:
    - id: rule-1
      effect: allow
      when: { source_tags_subset_of_operator_tags: true }
    - id: rule-deny
      effect: deny
      when: { always: true }
"""


# ── Unit: load_config ─────────────────────────────────────────────────────

class TestLoadConfig:
    def test_minimal_valid(self):
        cfg = load_config(VALID_MINIMAL)
        assert cfg.derived.total_walls == 1
        assert cfg.derived.total_tiles == 4
        assert cfg.derived.total_sources == 1

    def test_full_valid(self):
        cfg = load_config(VALID_FULL)
        assert cfg.derived.total_walls == 2
        assert cfg.derived.tile_walls == 1
        assert cfg.derived.bigscreen_walls == 1
        assert cfg.derived.total_tiles == 24
        assert cfg.derived.total_screens == 2
        assert cfg.derived.total_display_endpoints == 26
        assert cfg.derived.sfu_rooms_needed == 1
        assert cfg.derived.mosaic_pipelines_needed == 1

    def test_wall_lookup(self):
        cfg = load_config(VALID_FULL)
        assert cfg.get_wall("wall-alpha") is not None
        assert cfg.get_wall("wall-alpha").grid.rows == 6

    def test_source_lookup(self):
        cfg = load_config(VALID_FULL)
        s = cfg.get_source("hdmi-01")
        assert s is not None and s.type == "srt"


# ── Unit: schema rejection ────────────────────────────────────────────────

class TestSchemaValidation:
    def test_tiles_without_grid_rejected(self):
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls:
  - { id: w, type: tiles, classification: unclassified, latency_class: interactive }
sources: []
"""
        with pytest.raises(ConfigError, match="grid"):
            load_config(bad)

    def test_bigscreen_without_screens_rejected(self):
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls:
  - { id: w, type: bigscreen, classification: unclassified, latency_class: broadcast }
sources: []
"""
        with pytest.raises(ConfigError, match="screens"):
            load_config(bad)

    def test_bad_version_format(self):
        with pytest.raises(ConfigError):
            load_config("platform: {version: nope, max_concurrent_streams: 64}\nwalls: []\nsources: []")

    def test_missing_max_concurrent_streams(self):
        """Spec requirement: platform.max_concurrent_streams is required."""
        with pytest.raises(ConfigError, match="max_concurrent_streams"):
            load_config("platform: {version: '1.0.0'}\nwalls: []\nsources: []")

    def test_missing_wall_classification(self):
        """Spec requirement: wall classification is required."""
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls:
  - { id: w, type: tiles, latency_class: interactive, grid: { rows: 1, cols: 1 } }
sources: []
"""
        with pytest.raises(ConfigError, match="classification"):
            load_config(bad)

    def test_missing_source_tags_classification(self):
        """Spec requirement: sources require tags.classification."""
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls: []
sources:
  - { id: s, type: webrtc, tags: { mission: alpha } }
"""
        with pytest.raises(ConfigError, match="classification"):
            load_config(bad)

    def test_srt_source_requires_endpoint(self):
        """Spec requirement: srt/rtsp/rtp sources require endpoint."""
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls: []
sources:
  - { id: s, type: srt, tags: { classification: unclassified } }
"""
        with pytest.raises(ConfigError, match="endpoint"):
            load_config(bad)

    def test_rtsp_source_requires_endpoint(self):
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls: []
sources:
  - { id: s, type: rtsp, tags: { classification: unclassified } }
"""
        with pytest.raises(ConfigError, match="endpoint"):
            load_config(bad)

    def test_webrtc_source_no_endpoint_ok(self):
        """webrtc sources do not require endpoint."""
        cfg = load_config("""
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls: []
sources:
  - { id: s, type: webrtc, tags: { classification: unclassified } }
""")
        assert cfg.derived.total_sources == 1

    def test_invalid_classification_enum(self):
        bad = """
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls:
  - { id: w, type: tiles, classification: bogus, latency_class: interactive, grid: { rows: 1, cols: 1 } }
sources: []
"""
        with pytest.raises(ConfigError):
            load_config(bad)


# ── Unit: semantic rejection ──────────────────────────────────────────────

class TestSemanticValidation:
    def test_duplicate_wall_ids(self):
        with pytest.raises(ConfigError, match="Duplicate wall"):
            load_config("""
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls:
  - { id: dup, type: tiles, classification: unclassified, latency_class: interactive, grid: { rows: 1, cols: 1 } }
  - { id: dup, type: tiles, classification: unclassified, latency_class: interactive, grid: { rows: 1, cols: 1 } }
sources: []
""")

    def test_duplicate_source_ids(self):
        with pytest.raises(ConfigError, match="Duplicate source"):
            load_config("""
platform: { version: "1.0.0", max_concurrent_streams: 64 }
walls: []
sources:
  - { id: dup, type: webrtc, tags: { classification: unclassified } }
  - { id: dup, type: webrtc, tags: { classification: unclassified } }
""")

    def test_concurrency_exceeded(self):
        with pytest.raises(ConfigError, match="Concurrency"):
            load_config("""
platform: { version: "1.0.0", max_concurrent_streams: 5 }
walls:
  - { id: big, type: tiles, classification: unclassified, latency_class: interactive, grid: { rows: 3, cols: 3 } }
sources: []
""")


# ── Unit: canonical hash ──────────────────────────────────────────────────

class TestCanonicalHash:
    def test_hash_stable(self):
        h1 = load_config(VALID_FULL).derived.config_hash
        h2 = load_config(VALID_FULL).derived.config_hash
        assert h1 == h2

    def test_hash_changes_with_config(self):
        h1 = load_config(VALID_MINIMAL).derived.config_hash
        h2 = load_config(VALID_FULL).derived.config_hash
        assert h1 != h2

    def test_canonical_json_is_valid_json(self):
        cfg = load_config(VALID_FULL)
        parsed = json.loads(cfg.canonical_json)
        assert "platform" in parsed

    def test_canonical_json_deterministic(self):
        c1 = load_config(VALID_FULL).canonical_json
        c2 = load_config(VALID_FULL).canonical_json
        assert c1 == c2


# ── Unit: dry_run ─────────────────────────────────────────────────────────

class TestDryRun:
    def test_valid(self):
        r = dry_run(VALID_FULL)
        assert r["valid"] is True
        assert r["walls"] == 2
        assert "predicted_hash" in r

    def test_schema_error(self):
        r = dry_run("platform: { version: nope }\nwalls: []\nsources: []")
        assert r["valid"] is False
        assert len(r["errors"]) > 0

    def test_yaml_parse_error(self):
        r = dry_run("not: valid: yaml: {{{")
        assert r["valid"] is False

    def test_concurrency_exceeded(self):
        r = dry_run("""
platform: { version: "1.0.0", max_concurrent_streams: 2 }
walls:
  - { id: w, type: tiles, classification: unclassified, latency_class: interactive, grid: { rows: 3, cols: 3 } }
sources: []
""")
        assert r["valid"] is False
        assert any("Concurrency" in e for e in r["errors"])


# ── Unit: ConfigWatcher last-known-good ───────────────────────────────────

class TestWatcher:
    def test_last_known_good_on_bad_reload(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text(VALID_MINIMAL)
        w = ConfigWatcher(f, poll_interval=0.1)
        cfg = w.load_initial()
        orig_hash = cfg.derived.config_hash

        f.write_text("broken yaml {{{")
        result = w.force_reload()
        assert result is None
        assert w.current.derived.config_hash == orig_hash
        assert w.last_error is not None

    def test_successful_reload(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text(VALID_MINIMAL)
        w = ConfigWatcher(f, poll_interval=0.1)
        w.load_initial()
        orig_hash = w.current.derived.config_hash

        f.write_text(VALID_FULL)
        result = w.force_reload()
        assert result is not None
        assert w.current.derived.config_hash != orig_hash
        assert w.last_error is None

    def test_schema_reject_keeps_lkg(self, tmp_path):
        """Overwrite with YAML missing required max_concurrent_streams → LKG preserved."""
        f = tmp_path / "c.yaml"
        f.write_text(VALID_MINIMAL)
        w = ConfigWatcher(f, poll_interval=0.1)
        w.load_initial()
        orig_hash = w.current.derived.config_hash

        f.write_text("platform: { version: '1.0.0' }\nwalls: []\nsources: []")
        result = w.force_reload()
        assert result is None
        assert w.current.derived.config_hash == orig_hash
        assert w.last_error is not None
        assert "max_concurrent_streams" in w.last_error


# ── API: endpoint tests ──────────────────────────────────────────────────

class TestApiEndpoints:
    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path):
        cfg_file = tmp_path / "platform-config.yaml"
        cfg_file.write_text(VALID_FULL)
        os.environ["VW_CONFIG_PATH"] = str(cfg_file)
        os.environ["VW_CONFIG_EVENT_LOG"] = str(tmp_path / "events.jsonl")
        os.environ["VW_CONFIG_POLL_INTERVAL"] = "999"

        import importlib
        from app import main as m
        importlib.reload(m)
        m._watcher = ConfigWatcher(cfg_file, poll_interval=999)
        m._watcher.load_initial()

        self._main = m
        self.cfg_file = cfg_file

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self._main.app)

    def test_healthz(self):
        r = self._client().get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "active_hash" in r.json()

    def test_get_config_canonical_json(self):
        r = self._client().get("/api/v1/config")
        assert r.status_code == 200
        data = r.json()
        assert "platform" in data
        assert "walls" in data
        assert r.headers.get("X-Config-Hash")

    def test_get_config_raw(self):
        r = self._client().get("/api/v1/config/raw")
        assert r.status_code == 200
        assert "platform:" in r.text
        assert r.headers.get("X-Config-Hash")

    def test_get_version(self):
        r = self._client().get("/api/v1/config/version")
        assert r.status_code == 200
        assert r.json()["version"] == "2.0.0"
        assert "config_hash" in r.json()

    def test_get_derived(self):
        r = self._client().get("/api/v1/derived")
        assert r.status_code == 200
        d = r.json()
        assert d["total_walls"] == 2
        assert d["total_tiles"] == 24
        assert d["sfu_rooms_needed"] == 1
        assert d["mosaic_pipelines_needed"] == 1

    def test_list_walls(self):
        r = self._client().get("/api/v1/walls")
        assert len(r.json()["walls"]) == 2

    def test_get_wall_by_id(self):
        r = self._client().get("/api/v1/walls/wall-alpha")
        assert r.json()["id"] == "wall-alpha"
        assert r.json()["tile_count"] == 24

    def test_get_wall_not_found(self):
        assert self._client().get("/api/v1/walls/nope").status_code == 404

    def test_list_sources(self):
        assert len(self._client().get("/api/v1/sources").json()["sources"]) == 2

    def test_get_source_by_id(self):
        r = self._client().get("/api/v1/sources/hdmi-01")
        assert r.json()["type"] == "srt"

    def test_get_policy(self):
        r = self._client().get("/api/v1/policy")
        assert r.status_code == 200
        assert len(r.json()["rules"]) == 2

    def test_dry_run_valid(self):
        r = self._client().post("/api/v1/config/dry-run", content=VALID_MINIMAL)
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_dry_run_invalid_returns_400(self):
        r = self._client().post("/api/v1/config/dry-run",
                                content="platform: {version: nope}\nwalls: []\nsources: []")
        assert r.status_code == 400
        assert r.json()["valid"] is False

    def test_reload_force(self):
        r = self._client().post("/api/v1/config/reload")
        assert r.status_code == 200

    def test_reload_detects_change(self):
        c = self._client()
        h1 = c.get("/api/v1/config/version").json()["config_hash"]

        self.cfg_file.write_text(VALID_MINIMAL)
        resp = c.post("/api/v1/config/reload")
        assert resp.json().get("reloaded") is True

        h2 = c.get("/api/v1/config/version").json()["config_hash"]
        assert h1 != h2

    def test_reload_keeps_last_good_on_corruption(self):
        c = self._client()
        h1 = c.get("/api/v1/config/version").json()["config_hash"]

        self.cfg_file.write_text("broken yaml {{{")
        c.post("/api/v1/config/reload")

        h2 = c.get("/api/v1/config/version").json()["config_hash"]
        assert h1 == h2

        health = c.get("/healthz").json()
        assert "last_error" in health
