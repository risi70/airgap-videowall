# SPDX-License-Identifier: EUPL-1.2
"""
vw-config — Configuration Authority for the Videowall Platform

Loads, validates, watches, and distributes platform configuration.
All wall/source/policy definitions are YAML-driven; no code changes
needed for scaling.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

LOG = logging.getLogger("vw.config")


# ── Schema Validation ─────────────────────────────────────────────────────

def _load_schema() -> dict:
    """Load JSONSchema from the config directory."""
    schema_paths = [
        Path(__file__).parent.parent.parent.parent / "config" / "schema.json",
        Path("/opt/videowall/config/schema.json"),
        Path("/etc/videowall/schema.json"),
    ]
    for p in schema_paths:
        if p.exists():
            return json.loads(p.read_text())
    LOG.warning("JSONSchema not found; validation will be skipped")
    return {}


def validate_config(data: dict) -> list[str]:
    """Validate config dict against JSONSchema. Returns list of errors (empty = valid)."""
    try:
        import jsonschema
    except ImportError:
        LOG.warning("jsonschema not installed; skipping validation")
        return []

    schema = _load_schema()
    if not schema:
        return []

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in validator.iter_errors(data):
        path = ".".join(str(p) for p in err.absolute_path)
        errors.append(f"{path}: {err.message}" if path else err.message)
    return errors


# ── Data Classes ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CodecPolicy:
    tiles: str = "h264"
    mosaics: str = "hevc"


@dataclass(frozen=True)
class LatencyClasses:
    interactive_max_ms: int = 500
    broadcast_max_ms: int = 6000


@dataclass(frozen=True)
class PlatformSettings:
    version: str = "0.0.0"
    max_concurrent_streams: int = 64
    codec_policy: CodecPolicy = field(default_factory=CodecPolicy)
    latency_classes: LatencyClasses = field(default_factory=LatencyClasses)


@dataclass(frozen=True)
class WallGrid:
    rows: int = 1
    cols: int = 1


@dataclass(frozen=True)
class WallConfig:
    id: str = ""
    type: str = "tiles"  # tiles | bigscreen
    classification: str = ""
    grid: Optional[WallGrid] = None
    screens: int = 1
    resolution: str = "1920x1080"
    latency_class: str = "interactive"
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def tile_count(self) -> int:
        if self.type == "tiles" and self.grid:
            return self.grid.rows * self.grid.cols
        return self.screens


@dataclass(frozen=True)
class SourceConfig:
    id: str = ""
    type: str = "webrtc"  # webrtc | rtsp | srt | rtp
    endpoint: str = ""
    codec: str = ""
    resolution: str = ""
    bitrate_kbps: int = 0
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyRule:
    id: str = ""
    effect: str = "deny"
    description: str = ""
    when: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyConfig:
    taxonomy: dict[str, list[str]] = field(default_factory=dict)
    rules: list[PolicyRule] = field(default_factory=list)
    allow_list: list[dict[str, str]] = field(default_factory=list)


# ── Derived Metrics ───────────────────────────────────────────────────────

@dataclass
class DerivedMetrics:
    """Computed at load time from the declarative config."""
    total_walls: int = 0
    tile_walls: int = 0
    bigscreen_walls: int = 0
    total_tiles: int = 0
    total_screens: int = 0
    total_display_endpoints: int = 0
    total_sources: int = 0
    sources_by_type: dict[str, int] = field(default_factory=dict)
    sfu_rooms_needed: int = 0
    mosaic_pipelines_needed: int = 0
    estimated_bandwidth_gbps: float = 0.0
    concurrency_headroom: int = 0
    config_hash: str = ""

    @staticmethod
    def compute(platform: PlatformSettings, walls: list[WallConfig],
                sources: list[SourceConfig], raw_yaml: str) -> DerivedMetrics:
        m = DerivedMetrics()
        m.total_walls = len(walls)
        m.tile_walls = sum(1 for w in walls if w.type == "tiles")
        m.bigscreen_walls = sum(1 for w in walls if w.type == "bigscreen")
        m.total_tiles = sum(w.tile_count for w in walls if w.type == "tiles")
        m.total_screens = sum(w.screens for w in walls if w.type == "bigscreen")
        m.total_display_endpoints = m.total_tiles + m.total_screens
        m.total_sources = len(sources)
        m.sources_by_type = {}
        for s in sources:
            m.sources_by_type[s.type] = m.sources_by_type.get(s.type, 0) + 1

        # SFU rooms: one per tile-wall
        m.sfu_rooms_needed = m.tile_walls
        # Mosaic pipelines: one per bigscreen wall
        m.mosaic_pipelines_needed = m.bigscreen_walls

        # Bandwidth estimate (rule of thumb)
        tile_bw = m.total_tiles * 6.0  # 6 Mbps per 1080p tile
        screen_bw = m.total_screens * 15.0  # 15 Mbps per 4K mosaic
        source_bw = sum(s.bitrate_kbps / 1000.0 for s in sources if s.bitrate_kbps > 0)
        m.estimated_bandwidth_gbps = (tile_bw + screen_bw + source_bw) / 1000.0

        m.concurrency_headroom = platform.max_concurrent_streams - m.total_display_endpoints
        m.config_hash = hashlib.sha256(raw_yaml.encode()).hexdigest()[:16]
        return m


# ── Platform Config (assembled) ──────────────────────────────────────────

@dataclass
class PlatformConfig:
    platform: PlatformSettings = field(default_factory=PlatformSettings)
    walls: list[WallConfig] = field(default_factory=list)
    sources: list[SourceConfig] = field(default_factory=list)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    derived: DerivedMetrics = field(default_factory=DerivedMetrics)
    raw_yaml: str = ""
    loaded_from: str = ""
    loaded_at: float = 0.0

    def get_wall(self, wall_id: str) -> Optional[WallConfig]:
        return next((w for w in self.walls if w.id == wall_id), None)

    def get_source(self, source_id: str) -> Optional[SourceConfig]:
        return next((s for s in self.sources if s.id == source_id), None)

    def wall_ids(self) -> list[str]:
        return [w.id for w in self.walls]

    def source_ids(self) -> list[str]:
        return [s.id for s in self.sources]


# ── Loader ────────────────────────────────────────────────────────────────

def _parse_wall(raw: dict) -> WallConfig:
    grid = None
    if "grid" in raw:
        grid = WallGrid(rows=raw["grid"]["rows"], cols=raw["grid"]["cols"])
    return WallConfig(
        id=raw["id"],
        type=raw.get("type", "tiles"),
        classification=raw.get("classification", ""),
        grid=grid,
        screens=raw.get("screens", 1),
        resolution=raw.get("resolution", "1920x1080"),
        latency_class=raw.get("latency_class", "interactive"),
        tags=raw.get("tags", {}),
    )


def _parse_source(raw: dict) -> SourceConfig:
    return SourceConfig(
        id=raw["id"],
        type=raw.get("type", "webrtc"),
        endpoint=raw.get("endpoint", ""),
        codec=raw.get("codec", ""),
        resolution=raw.get("resolution", ""),
        bitrate_kbps=raw.get("bitrate_kbps", 0),
        tags=raw.get("tags", {}),
    )


def _parse_policy(raw: dict) -> PolicyConfig:
    rules = [PolicyRule(
        id=r["id"],
        effect=r.get("effect", "deny"),
        description=r.get("description", ""),
        when=r.get("when", {}),
    ) for r in raw.get("rules", [])]
    return PolicyConfig(
        taxonomy=raw.get("taxonomy", {}),
        rules=rules,
        allow_list=raw.get("allow_list", []),
    )


def load_config(yaml_text: str, source_path: str = "<string>") -> PlatformConfig:
    """Parse YAML text into a validated PlatformConfig."""
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")

    errors = validate_config(data)
    if errors:
        raise ValueError(f"Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    # Check for duplicate IDs
    wall_ids = [w["id"] for w in data.get("walls", [])]
    source_ids = [s["id"] for s in data.get("sources", [])]
    dupes = [x for x in wall_ids if wall_ids.count(x) > 1]
    dupes += [x for x in source_ids if source_ids.count(x) > 1]
    if dupes:
        raise ValueError(f"Duplicate IDs: {set(dupes)}")

    plat_raw = data.get("platform", {})
    cp = plat_raw.get("codec_policy", {})
    lc = plat_raw.get("latency_classes", {})

    platform = PlatformSettings(
        version=plat_raw.get("version", "0.0.0"),
        max_concurrent_streams=plat_raw.get("max_concurrent_streams", 64),
        codec_policy=CodecPolicy(tiles=cp.get("tiles", "h264"), mosaics=cp.get("mosaics", "hevc")),
        latency_classes=LatencyClasses(
            interactive_max_ms=lc.get("interactive_max_ms", 500),
            broadcast_max_ms=lc.get("broadcast_max_ms", 6000),
        ),
    )

    walls = [_parse_wall(w) for w in data.get("walls", [])]
    sources = [_parse_source(s) for s in data.get("sources", [])]
    policy = _parse_policy(data.get("policy", {}))

    derived = DerivedMetrics.compute(platform, walls, sources, yaml_text)

    # Guardrails
    if derived.total_display_endpoints > platform.max_concurrent_streams:
        raise ValueError(
            f"Concurrency exceeded: {derived.total_display_endpoints} endpoints "
            f"> max_concurrent_streams={platform.max_concurrent_streams}"
        )

    cfg = PlatformConfig(
        platform=platform,
        walls=walls,
        sources=sources,
        policy=policy,
        derived=derived,
        raw_yaml=yaml_text,
        loaded_from=source_path,
        loaded_at=time.time(),
    )

    LOG.info("Config loaded: %d walls (%d tile, %d bigscreen), %d sources, %d endpoints, "
             "concurrency %d/%d, hash=%s from=%s",
             derived.total_walls, derived.tile_walls, derived.bigscreen_walls,
             derived.total_sources, derived.total_display_endpoints,
             derived.total_display_endpoints, platform.max_concurrent_streams,
             derived.config_hash, source_path)
    return cfg


def load_config_file(path: str | Path) -> PlatformConfig:
    """Load config from a YAML file."""
    p = Path(path)
    return load_config(p.read_text(), source_path=str(p))


# ── File Watcher ──────────────────────────────────────────────────────────

class ConfigWatcher:
    """Polls a config file for changes and invokes callbacks on reload."""

    def __init__(self, path: str | Path, poll_interval: float = 5.0):
        self.path = Path(path)
        self.poll_interval = poll_interval
        self._last_hash: str = ""
        self._callbacks: list = []
        self.current: Optional[PlatformConfig] = None

    def on_reload(self, callback):
        """Register a callback: fn(new_config: PlatformConfig)"""
        self._callbacks.append(callback)

    def _file_hash(self) -> str:
        if not self.path.exists():
            return ""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load_initial(self) -> PlatformConfig:
        cfg = load_config_file(self.path)
        self._last_hash = self._file_hash()
        self.current = cfg
        return cfg

    def check_and_reload(self) -> Optional[PlatformConfig]:
        """Check for changes; return new config if changed, None otherwise."""
        current_hash = self._file_hash()
        if current_hash == self._last_hash:
            return None

        LOG.info("Config file changed (hash %s → %s); reloading...", self._last_hash[:8], current_hash[:8])
        try:
            cfg = load_config_file(self.path)
            self._last_hash = current_hash
            self.current = cfg
            for cb in self._callbacks:
                try:
                    cb(cfg)
                except Exception as e:
                    LOG.error("Callback error: %s", e)
            return cfg
        except Exception as e:
            LOG.error("Config reload failed (keeping previous): %s", e)
            return None

    def watch_forever(self):
        """Blocking poll loop."""
        self.load_initial()
        while True:
            time.sleep(self.poll_interval)
            self.check_and_reload()


# ── Dry Run ───────────────────────────────────────────────────────────────

def dry_run(yaml_text: str) -> dict[str, Any]:
    """Validate config and return derived metrics without applying."""
    try:
        cfg = load_config(yaml_text, source_path="<dry-run>")
        d = cfg.derived
        return {
            "valid": True,
            "errors": [],
            "version": cfg.platform.version,
            "walls": len(cfg.walls),
            "sources": len(cfg.sources),
            "total_tiles": d.total_tiles,
            "total_screens": d.total_screens,
            "total_endpoints": d.total_display_endpoints,
            "sfu_rooms": d.sfu_rooms_needed,
            "mosaic_pipelines": d.mosaic_pipelines_needed,
            "estimated_bandwidth_gbps": round(d.estimated_bandwidth_gbps, 2),
            "concurrency_headroom": d.concurrency_headroom,
            "config_hash": d.config_hash,
        }
    except (ValueError, yaml.YAMLError) as e:
        return {"valid": False, "errors": str(e).split("\n")}
