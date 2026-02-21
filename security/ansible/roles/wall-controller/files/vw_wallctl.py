#!/usr/bin/env python3
import os, time, json, ssl, argparse
import requests

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/videowall/wallctl/wallctl.yml")
    args = ap.parse_args()

    # Minimal controller loop: heartbeat + fetch desired layout.
    # In the full platform, this would manage players via mgmt-api + local orchestration.
    import yaml
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    session = requests.Session()
    session.verify = cfg["tls"]["ca"]
    session.cert = (cfg["tls"]["cert"], cfg["tls"]["key"])

    wall_id = cfg["wall_id"]
    mgmt = cfg["mgmt_api"]

    while True:
        try:
            hb = {"wall_id": wall_id, "ts": int(time.time()), "status": "online"}
            r = session.post(f"{mgmt}/api/v1/walls/{wall_id}/heartbeat", json=hb, timeout=5)
            r.raise_for_status()

            r = session.get(f"{mgmt}/api/v1/walls/{wall_id}/desired-layout", timeout=5)
            if r.status_code == 200:
                desired = r.json()
                # Write out for local player manager (external).
                os.makedirs("/opt/videowall/wallctl/state", exist_ok=True)
                with open("/opt/videowall/wallctl/state/desired-layout.json", "w", encoding="utf-8") as out:
                    json.dump(desired, out, indent=2)
        except Exception as e:
            # Keep running; errors are observable via journald/promtail.
            print(f"[wallctl] error: {e}", flush=True)
        time.sleep(cfg.get("heartbeat_interval_s", 5))

if __name__ == "__main__":
    main()
