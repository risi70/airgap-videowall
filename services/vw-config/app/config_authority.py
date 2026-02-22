# SPDX-License-Identifier: EUPL-1.2
"""
vw-config — Configuration Authority for the Videowall Platform.

Loads, validates, watches, and distributes platform configuration.
All wall/source/policy definitions are YAML-driven; no code changes
needed for scaling.

Features:
  - JSONSchema validation (Draft 2020-12)
  - Semantic validation (unique IDs, tiles→grid, bigscreen→screens, concurrency)
  - Canonical JSON + SHA-256 hash
  - Last-known-good state with error exposure
  - File watcher (configurable poll interval)
  - Append-only JSONL event log
  - Dry-run simulation
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

LOG = logging.getLogger("vw.config")

EVENT_LOG_PATH = Path(os.getenv("VW_CONFIG_EVENT_LOG",
                                "/var/lib/vw-config/events.jsonl"))


# ── Schema Validation ─────────────────────────────────────────────────────

def _load_schema() -> dict:
    """Load JSONSchema from well-known paths."""
    paths = [
        Path(__file__).parent.parent.parent.parent / "config" / "schema.json",
        Path("/opt/videowall/config/schema.json"),
        Path("/etc/videowall/schema.json"),
    ]
    for p in paths:
        if p.exists():
            return json.loads(p.read_text())
    LOG.warning("JSONSchema not found; schema validation will be skipped")
    return {}


def validate_schema(data: dict) -> list[str]:
    """Validate against JSONSchema. Returns error strings (empty = valid)."""
    try:
        import jsonschema
    except ImportError:
        LOG.warning("jsonschema package not installed; skipping schema validation")
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


def validate_semantic(data: dict) -> list[str]:
    """Semantic validation beyond what JSONSchema can express."""
    errors = []

    # Unique wall IDs
    wall_ids = [w.get("id", "") for w in data.get("walls", [])]
    seen = set()
    for wid in wall_ids:
        if wid in seen:
            errors.append(f"Duplicate wall id: '{wid}'")
        seen.add(wid)

    # Unique source IDs
    source_ids = [s.get("id", "") for s in data.get("sources", [])]
    seen = set()
    for sid in source_ids:
        if sid in seen:
            errors.append(f"Duplicate source id: '{sid}'")
        seen.add(sid)

    # Cross-type: wall id must not collide with source id
    overlap = set(wall_ids) & set(source_ids)
    if overlap:
        errors.append(f"IDs used in both walls and sources: {overlap}")

    # tiles→grid, bigscreen→screens (belt-and-suspenders with schema)
    for w in data.get("walls", []):
        wtype = w.get("type", "")
        wid = w.get("id", "?")
        if wtype == "tiles" and "grid" not in w:
            errors.append(f"Wall '{wid}': type=tiles requires 'grid'")
        if wtype == "bigscreen" and "screens" not in w:
            errors.append(f"Wall '{wid}': type=bigscreen requires 'screens'")

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
    type: str = "tiles"
    classification: str = "unclassified"
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
    type: str = "webrtc"
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
    worst_case_concurrency: int = 0
    concurrency_headroom: int = 0
    config_hash: str = ""

    @staticmethod
    def compute(platform: PlatformSettings, walls: list[WallConfig],
                sources: list[SourceConfig], canonical_json: str) -> DerivedMetrics:
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
        m.sfu_rooms_needed = m.tile_walls
        m.mosaic_pipelines_needed = m.bigscreen_walls

        tile_bw = m.total_tiles * 6.0
        screen_bw = m.total_screens * 15.0
        source_bw = sum(s.bitrate_kbps / 1000.0 for s in sources if s.bitrate_kbps > 0)
        m.estimated_bandwidth_gbps = round((tile_bw + screen_bw + source_bw) / 1000.0, 3)

        # worst case: every source on every endpoint simultaneously
        m.worst_case_concurrency = m.total_display_endpoints
        m.concurrency_headroom = platform.max_concurrent_streams - m.worst_case_concurrency
        m.config_hash = hashlib.sha256(canonical_json.encode()).hexdigest()
        return m


# ── Platform Config (assembled) ──────────────────────────────────────────

@dataclass
class PlatformConfig:
    platform: PlatformSettings = field(default_factory=PlatformSettings)
    walls: list[WallConfig] = field(default_factory=list)
    sources: list[SourceConfig] = field(default_factory=list)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    derived: DerivedMetrics = field(default_factory=DerivedMetrics)
    canonical_json: str = ""
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


# ── Canonical JSON ────────────────────────────────────────────────────────

def _to_canonical_dict(data: dict) -> dict:
    """Create a canonical representation (sorted keys, stable)."""
    if isinstance(data, dict):
        return {k: _to_canonical_dict(v) for k, v in sorted(data.items())}
    if isinstance(data, list):
        return [_to_canonical_dict(i) for i in data]
    return data


def canonical_json(data: dict) -> str:
    """Produce stable-ordered JSON with no extra whitespace."""
    return json.dumps(_to_canonical_dict(data), sort_keys=True, separators=(",", ":"))


# ── Event Log ─────────────────────────────────────────────────────────────

def _emit_event(event_type: str, old_hash: str, new_hash: str,
                error: str = "", source_path: str = ""):
    """Append a JSONL event to the local event log."""
    try:
        EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "source": source_path,
        }
        if error:
            entry["error"] = error
        with open(EVENT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception as e:
        LOG.warning("Failed to write event log: %s", e)


# ── Loader ────────────────────────────────────────────────────────────────

def _parse_wall(raw: dict) -> WallConfig:
    grid = None
    if "grid" in raw:
        grid = WallGrid(rows=raw["grid"]["rows"], cols=raw["grid"]["cols"])
    return WallConfig(
        id=raw["id"], type=raw.get("type", "tiles"),
        classification=raw.get("classification", "unclassified"),
        grid=grid, screens=raw.get("screens", 1),
        resolution=raw.get("resolution", "1920x1080"),
        latency_class=raw.get("latency_class", "interactive"),
        tags=raw.get("tags", {}),
    )


def _parse_source(raw: dict) -> SourceConfig:
    return SourceConfig(
        id=raw["id"], type=raw.get("type", "webrtc"),
        endpoint=raw.get("endpoint", ""),
        codec=raw.get("codec", ""), resolution=raw.get("resolution", ""),
        bitrate_kbps=raw.get("bitrate_kbps", 0),
        tags=raw.get("tags", {}),
    )


def _parse_policy(raw: dict) -> PolicyConfig:
    rules = [PolicyRule(id=r["id"], effect=r.get("effect", "deny"),
                        description=r.get("description", ""),
                        when=r.get("when", {}))
             for r in raw.get("rules", [])]
    return PolicyConfig(taxonomy=raw.get("taxonomy", {}), rules=rules,
                        allow_list=raw.get("allow_list", []))


class ConfigError(Exception):
    """Raised when config is invalid."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_config(yaml_text: str, source_path: str = "<string>") -> PlatformConfig:
    """Parse YAML text into a validated PlatformConfig."""
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ConfigError(["Config must be a YAML mapping"])

    # Schema validation
    schema_errors = validate_schema(data)
    if schema_errors:
        raise ConfigError(schema_errors)

    # Semantic validation
    sem_errors = validate_semantic(data)
    if sem_errors:
        raise ConfigError(sem_errors)

    plat_raw = data.get("platform", {})
    cp = plat_raw.get("codec_policy", {})
    lc = plat_raw.get("latency_classes", {})

    platform = PlatformSettings(
        version=plat_raw.get("version", "0.0.0"),
        max_concurrent_streams=plat_raw.get("max_concurrent_streams", 64),
        codec_policy=CodecPolicy(tiles=cp.get("tiles", "h264"),
                                 mosaics=cp.get("mosaics", "hevc")),
        latency_classes=LatencyClasses(
            interactive_max_ms=lc.get("interactive_max_ms", 500),
            broadcast_max_ms=lc.get("broadcast_max_ms", 6000)),
    )

    walls = [_parse_wall(w) for w in data.get("walls", [])]
    sources = [_parse_source(s) for s in data.get("sources", [])]
    policy = _parse_policy(data.get("policy", {}))

    cj = canonical_json(data)
    derived = DerivedMetrics.compute(platform, walls, sources, cj)

    # Concurrency guardrail
    if derived.worst_case_concurrency > platform.max_concurrent_streams:
        raise ConfigError([
            f"Concurrency exceeded: {derived.worst_case_concurrency} endpoints "
            f"> max_concurrent_streams={platform.max_concurrent_streams}"
        ])

    cfg = PlatformConfig(
        platform=platform, walls=walls, sources=sources, policy=policy,
        derived=derived, canonical_json=cj, raw_yaml=yaml_text,
        loaded_from=source_path, loaded_at=time.time(),
    )

    LOG.info("Config loaded: %d walls (%d tile, %d bigscreen), %d sources, "
             "%d endpoints, concurrency %d/%d, hash=%.16s from=%s",
             derived.total_walls, derived.tile_walls, derived.bigscreen_walls,
             derived.total_sources, derived.total_display_endpoints,
             derived.worst_case_concurrency, platform.max_concurrent_streams,
             derived.config_hash, source_path)
    return cfg


def load_config_file(path: str | Path) -> PlatformConfig:
    p = Path(path)
    return load_config(p.read_text(), source_path=str(p))


# ── File Watcher with Last-Known-Good ─────────────────────────────────────

class ConfigWatcher:
    """Polls a config file. Keeps last-known-good on reload failure."""

    def __init__(self, path: str | Path, poll_interval: float = 5.0):
        self.path = Path(path)
        self.poll_interval = poll_interval
        self._last_hash: str = ""
        self._callbacks: list = []
        self.current: Optional[PlatformConfig] = None
        self.last_reload_ts: float = 0.0
        self.last_error: Optional[str] = None

    def on_reload(self, callback):
        self._callbacks.append(callback)

    def _file_hash(self) -> str:
        if not self.path.exists():
            return ""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load_initial(self) -> PlatformConfig:
        cfg = load_config_file(self.path)
        self._last_hash = self._file_hash()
        self.current = cfg
        self.last_reload_ts = time.time()
        self.last_error = None
        _emit_event("config_applied", "", cfg.derived.config_hash,
                     source_path=str(self.path))
        return cfg

    def check_and_reload(self) -> Optional[PlatformConfig]:
        """Check for changes. Returns new config or None. Never raises."""
        current_hash = self._file_hash()
        if current_hash == self._last_hash:
            return None

        LOG.info("Config file changed; reloading...")
        old_hash = self.current.derived.config_hash if self.current else ""
        try:
            cfg = load_config_file(self.path)
            self._last_hash = current_hash
            self.current = cfg
            self.last_reload_ts = time.time()
            self.last_error = None
            _emit_event("config_applied", old_hash, cfg.derived.config_hash,
                         source_path=str(self.path))
            for cb in self._callbacks:
                try:
                    cb(cfg)
                except Exception as e:
                    LOG.error("Callback error: %s", e)
            return cfg
        except (ConfigError, yaml.YAMLError, Exception) as e:
            err_str = str(e)
            LOG.error("Config reload FAILED (keeping previous): %s", err_str)
            self._last_hash = current_hash  # don't retry same broken file
            self.last_error = err_str
            _emit_event("config_rejected", old_hash, "", error=err_str,
                         source_path=str(self.path))
            return None

    def force_reload(self) -> Optional[PlatformConfig]:
        """Force reload regardless of file hash."""
        self._last_hash = ""  # reset hash to force check
        return self.check_and_reload()

    def watch_forever(self):
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
            "walls": d.total_walls,
            "sources": d.total_sources,
            "total_tiles": d.total_tiles,
            "total_screens": d.total_screens,
            "total_endpoints": d.total_display_endpoints,
            "sfu_rooms": d.sfu_rooms_needed,
            "mosaic_pipelines": d.mosaic_pipelines_needed,
            "estimated_bandwidth_gbps": d.estimated_bandwidth_gbps,
            "worst_case_concurrency": d.worst_case_concurrency,
            "concurrency_headroom": d.concurrency_headroom,
            "predicted_hash": d.config_hash,
        }
    except (ConfigError, yaml.YAMLError, ValueError) as e:
        errors = e.errors if isinstance(e, ConfigError) else [str(e)]
        return {"valid": False, "errors": errors}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}
