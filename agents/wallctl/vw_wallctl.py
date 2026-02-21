#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from agents._common.vw_http import MTLSConfig, request_json

LOG = logging.getLogger("vw.wallctl")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class FailoverRules:
    max_retries: int = 3
    retry_delay: float = 2.0
    fallback_to_slate: bool = True


class WallCtl:
    def __init__(self, cfg: Dict[str, Any], *, state_dir: Path) -> None:
        self.cfg = cfg
        self.state_dir = state_dir
        self.wall_id = str(cfg["wall_id"])
        self.controller_id = str(cfg["controller_id"])
        self.mgmt_api_url = str(cfg["mgmt_api_url"]).rstrip("/")
        self.health_url = str(cfg["health_url"]).rstrip("/")
        self.heartbeat_interval = int(cfg.get("heartbeat_interval", 10))
        self.token_refresh_interval = int(cfg.get("token_refresh_interval", 240))
        self.layout_poll_interval = int(cfg.get("layout_poll_interval", 30))
        self.tile_health_interval = int(cfg.get("tile_health_interval", 5))
        self.tile_player_binary = str(cfg["tile_player_binary"])
        self.safe_slate_image = str(cfg["safe_slate_image"])
        fr = cfg.get("failover_rules", {}) or {}
        self.failover = FailoverRules(
            max_retries=int(fr.get("max_retries", 3)),
            retry_delay=float(fr.get("retry_delay", 2)),
            fallback_to_slate=bool(fr.get("fallback_to_slate", True)),
        )

        self.mtls = MTLSConfig(
            ca_cert=str(cfg["ca_cert"]),
            client_cert=str(cfg["client_cert"]),
            client_key=str(cfg["client_key"]),
        )

        self._stop = False
        self._tile_procs: Dict[str, subprocess.Popen] = {}
        self._token_cache: Dict[str, Dict[str, Any]] = {}
        self._layout_cache_path = self.state_dir / "last-known-good-layout.json"
        self._token_cache_path = self.state_dir / "token-cache.json"
        self._current_layout: Dict[str, Any] = {}

    def _load_caches(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self._token_cache_path.exists():
            try:
                self._token_cache = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
            except Exception:
                LOG.warning("Failed to read token cache; starting empty")
                self._token_cache = {}
        if self._layout_cache_path.exists():
            try:
                self._current_layout = json.loads(self._layout_cache_path.read_text(encoding="utf-8"))
            except Exception:
                LOG.warning("Failed to read layout cache; starting empty")
                self._current_layout = {}

    def register(self) -> None:
        url = f"{self.health_url}/api/v1/walls/{self.wall_id}/heartbeat"
        payload = {
            "wall_id": self.wall_id,
            "controller_id": self.controller_id,
            "ts": int(time.time()),
            "event": "register",
        }
        status, data = request_json("POST", url, mtls=self.mtls, json_body=payload, retries=2)
        if status >= 400:
            LOG.warning("register failed: %s %s", status, data)
        else:
            LOG.info("registered wallctl: %s", data.get("status", "ok"))

    def _send_heartbeat(self) -> None:
        url = f"{self.health_url}/api/v1/walls/{self.wall_id}/heartbeat"
        payload = {
            "wall_id": self.wall_id,
            "controller_id": self.controller_id,
            "ts": int(time.time()),
            "tiles_running": len(self._tile_procs),
        }
        status, data = request_json("POST", url, mtls=self.mtls, json_body=payload, retries=2)
        if status >= 400:
            LOG.warning("heartbeat failed: %s %s", status, data)

    def fetch_active_layout(self) -> Dict[str, Any]:
        url = f"{self.mgmt_api_url}/api/v1/walls/{self.wall_id}/layout/active"
        status, data = request_json("GET", url, mtls=self.mtls, retries=2)
        if status < 400 and data:
            _atomic_write(self._layout_cache_path, json.dumps(data, indent=2))
            return data
        # fall back to cache
        if self._layout_cache_path.exists():
            try:
                cached = json.loads(self._layout_cache_path.read_text(encoding="utf-8"))
                LOG.warning("layout fetch failed (%s); using cached layout", status)
                return cached
            except Exception:
                pass
        LOG.warning("layout fetch failed (%s) and no cache; using empty layout", status)
        return {"wall_id": self.wall_id, "tiles": {}}

    def request_subscribe_token(self, source_id: str, tile_id: str) -> Optional[str]:
        key = f"{source_id}:{tile_id}"
        cached = self._token_cache.get(key)
        now = int(time.time())
        if cached and int(cached.get("exp", 0)) - now > 30:
            return str(cached.get("token"))

        url = f"{self.mgmt_api_url}/api/v1/tokens/subscribe"
        payload = {"source_id": source_id, "tile_id": tile_id, "wall_id": self.wall_id}
        status, data = request_json("POST", url, mtls=self.mtls, json_body=payload, retries=2)
        if status >= 400:
            LOG.warning("token request failed: %s %s", status, data)
            return None

        token = data.get("token")
        exp = int(data.get("exp", now + self.token_refresh_interval))
        if token:
            self._token_cache[key] = {"token": token, "exp": exp}
            _atomic_write(self._token_cache_path, json.dumps(self._token_cache, indent=2))
            return str(token)
        return None

    def _tile_proc_key(self, tile_id: str) -> str:
        return str(tile_id)

    def _launch_tile_player(self, *, tile_id: str, token: str, stream_info: Dict[str, Any]) -> subprocess.Popen:
        # stream_info expected keys: sfu_url, room_id, display (optional)
        sfu_url = str(stream_info.get("sfu_url", ""))
        room_id = str(stream_info.get("room_id", ""))
        display = str(stream_info.get("display", ""))
        args = [
            sys.executable,
            self.tile_player_binary,
            "--tile-id", str(tile_id),
            "--token", token,
            "--sfu-url", sfu_url,
            "--room-id", room_id,
        ]
        if display:
            args += ["--display", display]
        LOG.info("starting tile %s: %s", tile_id, " ".join(args))
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _show_slate(self, tile_id: str) -> None:
        # Minimal: log + attempt to render image using fbi (framebuffer) if present.
        LOG.warning("showing safe slate for tile %s", tile_id)
        if shutil.which("fbi"):
            try:
                subprocess.Popen(["fbi", "-T", "1", "-noverbose", "-a", self.safe_slate_image],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def start_tile(self, tile_id: str, source_id: str, stream_info: Dict[str, Any]) -> None:
        token = self.request_subscribe_token(source_id, tile_id)
        if not token:
            if self.failover.fallback_to_slate:
                self._show_slate(tile_id)
            return

        # Stop if running
        self._stop_tile(tile_id)

        tries = 0
        while tries <= self.failover.max_retries:
            try:
                proc = self._launch_tile_player(tile_id=tile_id, token=token, stream_info=stream_info)
                self._tile_procs[self._tile_proc_key(tile_id)] = proc
                return
            except Exception as e:
                LOG.exception("failed to start tile %s (try %d): %s", tile_id, tries + 1, e)
                tries += 1
                time.sleep(self.failover.retry_delay * tries)

        if self.failover.fallback_to_slate:
            self._show_slate(tile_id)

    def _stop_tile(self, tile_id: str) -> None:
        key = self._tile_proc_key(tile_id)
        proc = self._tile_procs.pop(key, None)
        if not proc:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass

    def _stop_all_tiles(self) -> None:
        for tile_id in list(self._tile_procs.keys()):
            self._stop_tile(tile_id)

    def _check_tile_health(self) -> None:
        dead = []
        for tile_key, proc in self._tile_procs.items():
            if proc.poll() is not None:
                dead.append(tile_key)
        for tile_key in dead:
            LOG.warning("tile player exited: %s", tile_key)
            self._tile_procs.pop(tile_key, None)

    def apply_layout(self, layout: Dict[str, Any]) -> None:
        # Layout schema (minimal):
        # { "wall_id": "...", "tiles": { "tile-1": {"source_id":"src-1","stream":{...}}, ... } }
        new_tiles: Dict[str, Any] = (layout.get("tiles") or {})
        cur_tiles: Dict[str, Any] = (self._current_layout.get("tiles") or {})

        # stop removed or changed
        for tile_id, cur in cur_tiles.items():
            new = new_tiles.get(tile_id)
            if new is None or new.get("source_id") != cur.get("source_id") or new.get("stream") != cur.get("stream"):
                self._stop_tile(tile_id)

        # start new/changed
        for tile_id, new in new_tiles.items():
            cur = cur_tiles.get(tile_id)
            if cur is None or new.get("source_id") != cur.get("source_id") or new.get("stream") != cur.get("stream"):
                src = str(new.get("source_id"))
                stream = dict(new.get("stream") or {})
                self.start_tile(tile_id=str(tile_id), source_id=src, stream_info=stream)

        self._current_layout = layout
        _atomic_write(self._layout_cache_path, json.dumps(layout, indent=2))

    def _handle_sig(self, *_: Any) -> None:
        self._stop = True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_sig)
        signal.signal(signal.SIGINT, self._handle_sig)

        self._load_caches()
        self.register()

        next_hb = 0.0
        next_layout = 0.0
        next_tilechk = 0.0

        while not self._stop:
            now = time.time()
            if now >= next_hb:
                self._send_heartbeat()
                next_hb = now + self.heartbeat_interval
            if now >= next_layout:
                layout = self.fetch_active_layout()
                self.apply_layout(layout)
                next_layout = now + self.layout_poll_interval
            if now >= next_tilechk:
                self._check_tile_health()
                next_tilechk = now + self.tile_health_interval
            time.sleep(1.0)

        LOG.info("stopping tiles")
        self._stop_all_tiles()


def main() -> int:
    ap = argparse.ArgumentParser(description="Videowall wall controller agent")
    ap.add_argument("--config", default="/etc/videowall/wallctl/config.yaml")
    ap.add_argument("--state-dir", default="/var/lib/vw-wallctl")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    _setup_logging(args.log_level)

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    wall = WallCtl(cfg, state_dir=Path(args.state_dir))
    wall.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
