from __future__ import annotations

import json as _json
import logging as _logging
import os as _os
import threading
import urllib.request as _urllib_request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_LOG = _logging.getLogger("vw.policy")

# Policy source priority:
#   1. vw-config API  (VW_CONFIG_URL — single source of truth when running)
#   2. Local file      (VW_POLICY_PATH — K8s mount or co-located fallback)
_VW_CONFIG_URL = _os.environ.get("VW_CONFIG_URL", "http://vw-config:8006")
_VW_CONFIG_TIMEOUT = float(_os.environ.get("VW_CONFIG_TIMEOUT", "2"))

# Local file search order: explicit env → K8s mount → co-located in repo
_POLICY_FILE_CANDIDATES = [
    _os.environ.get("VW_POLICY_PATH", ""),
    "/etc/vw-policy/policy.yaml",
    str(Path(__file__).parent.parent / "policy.yaml"),
]


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


def _fetch_policy_from_vw_config() -> dict[str, Any] | None:
    """Try to fetch policy rules from vw-config API. Returns None on failure."""
    try:
        url = f"{_VW_CONFIG_URL}/api/v1/policy"
        req = _urllib_request.Request(url, method="GET")
        with _urllib_request.urlopen(req, timeout=_VW_CONFIG_TIMEOUT) as resp:
            data = _json.loads(resp.read())
        if isinstance(data, dict) and "rules" in data:
            _LOG.info("Policy loaded from vw-config API (%d rules)", len(data.get("rules", [])))
            return data
    except Exception as exc:
        _LOG.debug("vw-config API unavailable (%s); will try local file", exc)
    return None


def _resolve_policy_path() -> str | None:
    """Find the first existing policy file from the candidate list."""
    for p in _POLICY_FILE_CANDIDATES:
        if p and Path(p).is_file():
            return p
    return None


class PolicyEngine:
    def __init__(self, path: str | None = None):
        self.path = path or _resolve_policy_path()
        self._lock = threading.RLock()
        self._policy: dict[str, Any] = {}
        self._source: str = "none"
        self.reload()

    def reload(self) -> None:
        with self._lock:
            # Primary: try vw-config API (single source of truth for policy rules)
            vw_policy = _fetch_policy_from_vw_config()
            if vw_policy is not None:
                self._policy = vw_policy
                self._source = "vw-config"
                return

            # Fallback: local policy file (for bootstrap or when vw-config is down)
            path = self.path or _resolve_policy_path()
            if path:
                try:
                    with open(path, "rb") as f:
                        doc = yaml.safe_load(f.read().decode("utf-8")) or {}
                    if not isinstance(doc, dict):
                        raise ValueError("policy_document_must_be_mapping")
                    self._policy = doc
                    self._source = f"file:{path}"
                    _LOG.info("Policy loaded from local file: %s", path)
                    return
                except Exception as exc:
                    _LOG.warning("Failed to load policy file %s: %s", path, exc)

            # Last resort: deny-all
            _LOG.warning("No policy source available; using default-deny")
            self._policy = {"rules": [{"id": "default-deny", "effect": "deny", "when": [{"always": True}]}]}
            self._source = "empty-default"

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
