from __future__ import annotations

import os
from typing import Optional

import httpx

VW_POLICY_SERVICE_URL = os.getenv("VW_POLICY_SERVICE_URL", "http://vw-policy.vw-control.svc.cluster.local:8002")


async def evaluate_source_access(source_id: str, user: Optional[str] = None) -> bool:
    url = f"{VW_POLICY_SERVICE_URL.rstrip('/')}/evaluate"
    payload = {"source_id": source_id, "action": "use"}
    headers = {}
    if user:
        headers["X-User"] = user
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            # Accept either {"allow": true} or {"decision":"allow"}
            if isinstance(data, dict):
                if data.get("allow") is True:
                    return True
                if str(data.get("decision", "")).lower() == "allow":
                    return True
            return False
    except Exception:
        # Fail closed
        return False
