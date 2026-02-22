# SPDX-License-Identifier: EUPL-1.2
"""
Integration test: validate that wall/source counts are dynamically configurable.

Test plan:
1. Load config with 1 wall → verify derived metrics
2. Update config to 4 walls → verify derived metrics update
3. Add sources → verify concurrency tracking
4. Exceed concurrency → verify rejection
5. Duplicate IDs → verify rejection
6. Invalid schema → verify rejection
7. Dry-run → verify no side effects
"""
import json
import pytest
from pathlib import Path

# Adjust import path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "vw-config"))
from app.config_authority import load_config, dry_run, validate_schema, ConfigError


CONFIG_1WALL = """
platform:
  version: "1.0.0"
  max_concurrent_streams: 64
  codec_policy:
    tiles: h264
    mosaics: hevc

walls:
  - id: wall-alpha
    type: tiles
    classification: confidential
    grid: { rows: 4, cols: 3 }
    resolution: 1920x1080
    latency_class: interactive
    tags:
      mission: alpha

sources:
  - id: vdi-01
    type: webrtc
    tags:
      classification: confidential
      mission: alpha
"""

CONFIG_4WALLS = """
platform:
  version: "1.1.0"
  max_concurrent_streams: 128
  codec_policy:
    tiles: h264
    mosaics: hevc

walls:
  - id: wall-alpha
    type: tiles
    classification: confidential
    grid: { rows: 6, cols: 4 }
    resolution: 1920x1080
    latency_class: interactive
    tags: { mission: alpha }

  - id: wall-bravo
    type: tiles
    classification: secret
    grid: { rows: 6, cols: 4 }
    resolution: 1920x1080
    latency_class: interactive
    tags: { mission: bravo }

  - id: wall-charlie
    type: bigscreen
    screens: 2
    resolution: 3840x2160
    latency_class: broadcast
    tags: { mission: alpha }

  - id: wall-delta
    type: bigscreen
    screens: 2
    resolution: 3840x2160
    latency_class: broadcast
    tags: { mission: training }

sources:
  - id: vdi-01
    type: webrtc
    codec: h264
    bitrate_kbps: 6000
    tags: { classification: confidential, mission: alpha }
  - id: vdi-02
    type: webrtc
    codec: h264
    bitrate_kbps: 6000
    tags: { classification: secret, mission: bravo }
  - id: hdmi-01
    type: srt
    endpoint: "srt://10.10.10.20:9000"
    bitrate_kbps: 8000
    tags: { classification: confidential, mission: alpha }
  - id: hdmi-02
    type: rtsp
    endpoint: "rtsp://10.10.10.21:554/stream1"
    bitrate_kbps: 8000
    tags: { classification: restricted, mission: bravo }

policy:
  taxonomy:
    classifications: ["unclassified", "restricted", "confidential", "secret"]
  rules:
    - id: rule-clearance
      effect: allow
      when: { source_tags_subset_of_operator_tags: true }
    - id: rule-default-deny
      effect: deny
      when: { always: true }
"""


class TestDynamicConfigScaling:
    """Verify wall/source counts are configurable without code changes."""

    def test_1wall_derived_metrics(self):
        cfg = load_config(CONFIG_1WALL)
        assert cfg.derived.total_walls == 1
        assert cfg.derived.tile_walls == 1
        assert cfg.derived.bigscreen_walls == 0
        assert cfg.derived.total_tiles == 12  # 4×3
        assert cfg.derived.total_sources == 1
        assert cfg.derived.sfu_rooms_needed == 1
        assert cfg.derived.mosaic_pipelines_needed == 0

    def test_4walls_derived_metrics(self):
        cfg = load_config(CONFIG_4WALLS)
        assert cfg.derived.total_walls == 4
        assert cfg.derived.tile_walls == 2
        assert cfg.derived.bigscreen_walls == 2
        assert cfg.derived.total_tiles == 48  # 2 × (6×4)
        assert cfg.derived.total_screens == 4  # 2 × 2
        assert cfg.derived.total_display_endpoints == 52  # 48+4
        assert cfg.derived.total_sources == 4
        assert cfg.derived.sfu_rooms_needed == 2
        assert cfg.derived.mosaic_pipelines_needed == 2

    def test_scaling_no_code_change(self):
        """The critical test: same code path, different config, different metrics."""
        cfg1 = load_config(CONFIG_1WALL)
        cfg4 = load_config(CONFIG_4WALLS)
        assert cfg1.derived.total_walls != cfg4.derived.total_walls
        assert cfg1.derived.total_tiles != cfg4.derived.total_tiles
        assert cfg1.platform.version != cfg4.platform.version

    def test_wall_lookup_by_id(self):
        cfg = load_config(CONFIG_4WALLS)
        w = cfg.get_wall("wall-bravo")
        assert w is not None
        assert w.classification == "secret"
        assert w.tile_count == 24

    def test_source_lookup_by_id(self):
        cfg = load_config(CONFIG_4WALLS)
        s = cfg.get_source("hdmi-01")
        assert s is not None
        assert s.type == "srt"
        assert s.bitrate_kbps == 8000

    def test_concurrency_exceeded_rejects(self):
        """Config with too many endpoints should be rejected."""
        bad = """
platform:
  version: "1.0.0"
  max_concurrent_streams: 10
walls:
  - id: wall-big
    type: tiles
    grid: { rows: 4, cols: 4 }
sources: []
"""
        with pytest.raises((ValueError, ConfigError), match="Concurrency"):
            load_config(bad)

    def test_duplicate_ids_rejected(self):
        bad = """
platform:
  version: "1.0.0"
walls:
  - id: wall-x
    type: tiles
    grid: { rows: 2, cols: 2 }
  - id: wall-x
    type: tiles
    grid: { rows: 2, cols: 2 }
sources: []
"""
        with pytest.raises((ValueError, ConfigError), match="Duplicate"):
            load_config(bad)

    def test_dry_run_returns_metrics(self):
        result = dry_run(CONFIG_4WALLS)
        assert result["valid"] is True
        assert result["walls"] == 4
        assert result["sources"] == 4
        assert result["total_tiles"] == 48
        assert result["sfu_rooms"] == 2

    def test_dry_run_invalid(self):
        result = dry_run("not: valid: yaml: []")
        # Should return errors without crashing
        assert isinstance(result, dict)

    def test_policy_loaded(self):
        cfg = load_config(CONFIG_4WALLS)
        assert len(cfg.policy.rules) == 2
        assert cfg.policy.rules[0].id == "rule-clearance"
        assert cfg.policy.taxonomy["classifications"] == ["unclassified", "restricted", "confidential", "secret"]

    def test_codec_policy(self):
        cfg = load_config(CONFIG_4WALLS)
        assert cfg.platform.codec_policy.tiles == "h264"
        assert cfg.platform.codec_policy.mosaics == "hevc"

    def test_bandwidth_estimate(self):
        cfg = load_config(CONFIG_4WALLS)
        assert cfg.derived.estimated_bandwidth_gbps > 0

    def test_config_hash_changes(self):
        cfg1 = load_config(CONFIG_1WALL)
        cfg4 = load_config(CONFIG_4WALLS)
        assert cfg1.derived.config_hash != cfg4.derived.config_hash


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
