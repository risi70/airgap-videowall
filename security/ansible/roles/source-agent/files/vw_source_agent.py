#!/usr/bin/env python3
import time, argparse, subprocess, json
import requests, yaml

# Minimal source agent:
# - registers itself
# - periodically probes an input (placeholder)
# - publishes health
#
# In the full platform, this would manage a GStreamer pipeline to gateway ingest (RTP/SRT/RTSP).

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/videowall/source-agent/source-agent.yml")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    s = requests.Session()
    s.verify = cfg["tls"]["ca"]
    s.cert = (cfg["tls"]["cert"], cfg["tls"]["key"])

    src_id = cfg["source_id"]
    mgmt = cfg["mgmt_api"]

    # Register (idempotent)
    try:
        s.post(f"{mgmt}/api/v1/sources/{src_id}/register", json=cfg.get("metadata", {}), timeout=5)
    except Exception:
        pass

    while True:
        status = {"source_id": src_id, "ts": int(time.time()), "health_status": "online"}
        try:
            # Placeholder probe: if configured command fails, mark offline.
            cmd = cfg.get("probe_cmd", "")
            if cmd:
                r = subprocess.run(cmd, shell=True, timeout=10)
                if r.returncode != 0:
                    status["health_status"] = "offline"
            s.post(f"{mgmt}/api/v1/sources/{src_id}/health", json=status, timeout=5)
        except Exception as e:
            print(f"[source-agent] error: {e}", flush=True)
        time.sleep(cfg.get("health_interval_s", 5))

if __name__ == "__main__":
    main()
