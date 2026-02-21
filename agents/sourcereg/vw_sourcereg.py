#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from agents._common.vw_http import MTLSConfig, request_json

LOG = logging.getLogger("vw.sourcereg")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


class SourceReg:
    def __init__(self, cfg: Dict[str, Any], *, state_dir: Path) -> None:
        self.cfg = cfg
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "source_state.json"

        self.mtls = MTLSConfig(
            ca_cert=str(cfg["ca_cert"]),
            client_cert=str(cfg["client_cert"]),
            client_key=str(cfg["client_key"]),
        )
        self.mgmt_api_url = str(cfg["mgmt_api_url"]).rstrip("/")
        self.health_url = str(cfg["health_url"]).rstrip("/")
        self.check_interval = int(cfg.get("check_interval", 15))

        self.source_id = str(cfg.get("source_id") or "").strip()

        # load persisted source_id if present
        if not self.source_id and self.state_path.exists():
            try:
                st = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.source_id = str(st.get("source_id") or "").strip()
            except Exception:
                pass

    def _metadata(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id or None,
            "source_name": self.cfg.get("source_name"),
            "source_type": self.cfg.get("source_type"),
            "protocol": self.cfg.get("protocol"),
            "endpoint_url": self.cfg.get("endpoint_url"),
            "codec": self.cfg.get("codec"),
            "tags": self.cfg.get("tags") or [],
        }

    def register_if_needed(self) -> None:
        if self.source_id:
            return
        url = f"{self.mgmt_api_url}/api/v1/sources"
        status, data = request_json("POST", url, mtls=self.mtls, json_body=self._metadata(), retries=2)
        if status >= 400:
            LOG.error("source registration failed: %s %s", status, data)
            return
        new_id = data.get("source_id") or data.get("id")
        if not new_id:
            LOG.error("registration response missing source_id")
            return
        self.source_id = str(new_id)
        _atomic_write(self.state_path, json.dumps({"source_id": self.source_id}, indent=2))
        LOG.info("registered new source_id=%s", self.source_id)

    def heartbeat(self) -> None:
        if not self.source_id:
            return
        url = f"{self.health_url}/api/v1/sources/{self.source_id}/heartbeat"
        payload = {"source_id": self.source_id, "ts": int(time.time())}
        status, data = request_json("POST", url, mtls=self.mtls, json_body=payload, retries=2)
        if status >= 400:
            LOG.warning("heartbeat failed: %s %s", status, data)

    def run(self) -> None:
        self.register_if_needed()
        while True:
            self.heartbeat()
            time.sleep(self.check_interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="Videowall Source Registration Agent")
    ap.add_argument("--config", default="/etc/videowall/sourcereg/config.yaml")
    ap.add_argument("--state-dir", default="/var/lib/vw-sourcereg")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    _setup_logging(args.log_level)
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    SourceReg(cfg, state_dir=Path(args.state_dir)).run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
