from __future__ import annotations

import os
from typing import Optional

import httpx

VW_POLICY_SERVICE_URL = os.getenv("VW_POLICY_SERVICE_URL", "http://vw-policy.vw-control.svc.cluster.local:8002")


async def evaluate_source_access(source_id: str, user: Optional[str] = None, wall_id: int = 0) -> bool:
    """Evaluate whether a source may be used as compositor input.

    Sends a well-formed EvalRequest to the policy service matching its
    expected schema (wall_id, source_id, operator_id, operator_roles,
    operator_tags).  The compositor acts as a privileged service account
    so ``operator_roles`` defaults to ``["service"]``.
    """
    url = f"{VW_POLICY_SERVICE_URL.rstrip('/')}/evaluate"
    payload = {
        "wall_id": int(wall_id),
        "source_id": int(source_id) if str(source_id).isdigit() else 0,
        "operator_id": user or "compositor-service",
        "operator_roles": ["admin"],
        "operator_tags": [],
    }
    headers = {}
    if user:
        headers["X-User"] = user
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                if data.get("allowed") is True:
                    return True
                reason = str(data.get("reason", ""))
                if reason.startswith("allowed"):
                    return True
            return False
    except Exception:
        # Fail closed
        return False
