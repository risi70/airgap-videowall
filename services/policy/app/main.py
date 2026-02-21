from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

POLICY_PATH = "/etc/vw-policy/policy.yaml"


class EvalRequest(BaseModel):
    wall_id: int
    source_id: int
    operator_id: str
    operator_roles: list[str] = Field(default_factory=list)
    operator_tags: list[str] = Field(default_factory=list)


class EvalResponse(BaseModel):
    allowed: bool
    reason: str
    matched_rules: list[dict[str, Any]] = Field(default_factory=list)


class PolicyEngine:
    def __init__(self, path: str = POLICY_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._policy: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            with open(self.path, "rb") as f:
                doc = yaml.safe_load(f.read().decode("utf-8")) or {}
            if not isinstance(doc, dict):
                raise ValueError("policy_document_must_be_mapping")
            self._policy = doc

    def policy(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._policy)

    def _get_allow_list(self) -> list[dict[str, Any]]:
        al = self._policy.get("allow_list") or []
        return al if isinstance(al, list) else []

    def _get_rules(self) -> list[dict[str, Any]]:
        rules = self._policy.get("rules") or []
        return rules if isinstance(rules, list) else []

    def evaluate(
        self,
        *,
        wall_id: int,
        source_id: int,
        operator_id: str,
        operator_roles: list[str],
        operator_tags: list[str],
        source_tags: list[str],
        wall_tags: list[str],
    ) -> EvalResponse:
        roles = set([r.lower() for r in operator_roles])
        if "admin" in roles:
            return EvalResponse(allowed=True, reason="admin_bypass", matched_rules=[{"id": "admin-bypass"}])

        op_tags = set(operator_tags)
        s_tags = set(source_tags)
        w_tags = set(wall_tags)

        matched: list[dict[str, Any]] = []

        def cond_source_subset() -> bool:
            return s_tags.issubset(op_tags)

        def cond_source_wall_intersect() -> bool:
            return len(s_tags.intersection(w_tags)) > 0

        def cond_explicit_allow() -> bool:
            for entry in self._get_allow_list():
                try:
                    if str(entry.get("operator_id")) == str(operator_id) and int(entry.get("wall_id")) == int(wall_id) and int(entry.get("source_id")) == int(source_id):
                        return True
                except Exception:
                    continue
            return False

        def cond_always() -> bool:
            return True

        cond_map = {
            "source_tags_subset_of_operator_tags": cond_source_subset,
            "source_tags_intersect_wall_tags": cond_source_wall_intersect,
            "in_explicit_allow_list": cond_explicit_allow,
            "always": cond_always,
        }

        with self._lock:
            rules = self._get_rules()
            default_reason = (self._policy.get("defaults") or {}).get("deny_reason") or "default_deny"

            for rule in rules:
                rid = str(rule.get("id") or "rule-unknown")
                effect = str(rule.get("effect") or "deny").lower()
                when = rule.get("when") or []
                if not isinstance(when, list):
                    when = []

                # A rule "matches" if ALL listed conditions are true.
                ok = True
                for cond_obj in when:
                    if not isinstance(cond_obj, dict) or not cond_obj:
                        ok = False
                        break
                    # only one key expected
                    k = next(iter(cond_obj.keys()))
                    fn = cond_map.get(k)
                    if fn is None:
                        ok = False
                        break
                    if not fn():
                        ok = False
                        break

                if ok:
                    matched.append({"id": rid, "effect": effect})
                    if effect == "allow":
                        return EvalResponse(allowed=True, reason=f"allowed_by:{rid}", matched_rules=matched)
                    if effect == "deny":
                        return EvalResponse(allowed=False, reason=f"denied_by:{rid}", matched_rules=matched)

            return EvalResponse(allowed=False, reason=str(default_reason), matched_rules=matched)


app = FastAPI(title="vw-policy", version="0.1.0")
ENGINE = PolicyEngine()


def _coerce_tags(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


# Tag lookup: fetch wall and source tags from mgmt-api for policy evaluation.
# Falls back to empty tags if the API is unreachable (fail-open on enrichment,
# fail-closed on policy decision).

import os as _os
import urllib.request as _urllib_request

_MGMT_API_URL = _os.environ.get("VW_MGMT_API_URL", "http://vw-mgmt-api:8000")


def _lookup_tags(wall_id: int, source_id: int) -> tuple[list[str], list[str]]:
    """Fetch wall and source tags from mgmt-api."""
    wall_tags: list[str] = []
    source_tags: list[str] = []
    try:
        import json as _json
        url = f"{_MGMT_API_URL}/api/v1/walls/{wall_id}"
        req = _urllib_request.Request(url, method="GET")
        with _urllib_request.urlopen(req, timeout=2) as resp:
            data = _json.loads(resp.read())
            wall_tags = [str(t) for t in (data.get("tags") or [])]
    except Exception:
        pass
    try:
        import json as _json
        url = f"{_MGMT_API_URL}/api/v1/sources/{source_id}"
        req = _urllib_request.Request(url, method="GET")
        with _urllib_request.urlopen(req, timeout=2) as resp:
            data = _json.loads(resp.read())
            source_tags = [str(t) for t in (data.get("tags") or [])]
    except Exception:
        pass
    return (wall_tags, source_tags)


@app.post("/evaluate", response_model=EvalResponse)
def evaluate(req: EvalRequest) -> EvalResponse:
    wall_tags, source_tags = _lookup_tags(req.wall_id, req.source_id)
    return ENGINE.evaluate(
        wall_id=req.wall_id,
        source_id=req.source_id,
        operator_id=req.operator_id,
        operator_roles=_coerce_tags(req.operator_roles),
        operator_tags=_coerce_tags(req.operator_tags),
        source_tags=_coerce_tags(source_tags),
        wall_tags=_coerce_tags(wall_tags),
    )


@app.post("/reload")
def reload_policy() -> dict[str, Any]:
    try:
        ENGINE.reload()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"reload_failed:{type(e).__name__}") from e
    return {"reloaded": True}


@app.get("/policy")
def get_policy() -> dict[str, Any]:
    return ENGINE.policy()
