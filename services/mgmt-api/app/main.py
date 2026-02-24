from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import jwt
from jose.constants import Algorithms

from .config import settings
from .database import activate_layout, append_audit_event, close_pool, ensure_layout_version, get_pool, init_schema
from .models import (
    AuditEventOut,
    BundleExport,
    BundleImportRequest,
    Layout,
    LayoutIn,
    PolicyEvalRequest,
    PolicyEvalResponse,
    Source,
    SourceIn,
    TokenSubscribeRequest,
    TokenSubscribeResponse,
    Wall,
    WallIn,
    WhoAmI,
)
from .reconcile import reconcile_loop, reconcile_once

app = FastAPI(title="vw-mgmt-api", version="0.1.0")


def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _parse_bearer(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_authorization")
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_authorization")
    return parts[1]


def _load_jwks() -> dict[str, Any]:
    if not settings.oidc_jwks_path:
        return {}
    with open(settings.oidc_jwks_path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _get_public_key_for_token(token: str) -> str:
    if settings.oidc_public_key_pem.strip():
        return settings.oidc_public_key_pem.strip()

    jwks = _load_jwks()
    keys = jwks.get("keys", [])
    if not keys:
        raise HTTPException(status_code=500, detail="no_public_key_configured")

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    alg = header.get("alg")
    if alg != "RS256":
        raise HTTPException(status_code=401, detail="unsupported_jwt_alg")

    for k in keys:
        if kid and k.get("kid") != kid:
            continue
        x5c = k.get("x5c")
        if x5c and isinstance(x5c, list) and x5c:
            der = base64.b64decode(x5c[0].encode("ascii"))
            pem = "-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der).decode("ascii") + "-----END CERTIFICATE-----\n"
            return pem
    raise HTTPException(status_code=401, detail="jwks_kid_not_found")


def _extract_roles(claims: dict[str, Any]) -> list[str]:
    roles: set[str] = set()
    ra = claims.get("realm_access") or {}
    rr = ra.get("roles") or []
    for r in rr:
        if isinstance(r, str):
            roles.add(r)

    client_id = settings.oidc_client_id
    res = claims.get("resource_access") or {}
    if isinstance(res, dict) and client_id in res:
        cr = res.get(client_id) or {}
        rs = cr.get("roles") or []
        for r in rs:
            if isinstance(r, str):
                roles.add(r)
    return sorted(roles)


def _decode_and_verify_rs256(token: str) -> dict[str, Any]:
    key = _get_public_key_for_token(token)
    options = {"verify_aud": bool(settings.oidc_audience), "verify_iss": bool(settings.oidc_issuer)}
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[Algorithms.RS256],
            audience=settings.oidc_audience or None,
            issuer=settings.oidc_issuer or None,
            options=options,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"jwt_invalid:{type(e).__name__}") from e


async def get_current_user(request: Request) -> dict[str, Any]:
    token = _parse_bearer(request.headers.get("Authorization"))
    claims = _decode_and_verify_rs256(token)
    claims["_roles"] = _extract_roles(claims)
    return claims


def require_role(*required: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    required_set = set(required)

    async def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        roles = set(user.get("_roles") or [])
        if "admin" in roles:
            return user
        if not required_set.intersection(roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    return _dep


@app.on_event("startup")
async def _startup() -> None:
    await init_schema()
    asyncio.create_task(reconcile_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()


@app.exception_handler(KeyError)
async def _keyerror_handler(_: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/api/v1/auth/whoami", response_model=WhoAmI)
async def whoami(user: dict[str, Any] = Depends(get_current_user)) -> WhoAmI:
    roles = user.get("_roles") or []
    sub = str(user.get("sub") or "")
    preferred = str(user.get("preferred_username") or user.get("username") or "")
    claims = dict(user)
    claims.pop("_roles", None)
    return WhoAmI(sub=sub, preferred_username=preferred, roles=roles, claims=claims)


# ---- Walls ----

@app.get("/api/v1/walls", response_model=list[Wall])
async def list_walls(_: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> list[Wall]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, wall_type, tile_count, resolution, tags FROM walls ORDER BY id")
    return [Wall(**dict(r)) for r in rows]


@app.post("/api/v1/walls", response_model=Wall, status_code=201)
async def create_wall(payload: WallIn, user: dict[str, Any] = Depends(require_role("admin"))) -> Wall:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO walls (name, wall_type, tile_count, resolution, tags)
            VALUES ($1,$2,$3,$4,$5)
            RETURNING id, name, wall_type, tile_count, resolution, tags
            """,
            payload.name, payload.wall_type, payload.tile_count, payload.resolution, payload.tags
        )
    w = Wall(**dict(row))
    await append_audit_event(action="walls.create", actor=actor, object_type="wall", object_id=str(w.id), details=w.model_dump())
    return w


@app.get("/api/v1/walls/{wall_id}", response_model=Wall)
async def get_wall(wall_id: int, _: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> Wall:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name, wall_type, tile_count, resolution, tags FROM walls WHERE id=$1", wall_id)
    if not row:
        raise KeyError("wall_not_found")
    return Wall(**dict(row))


@app.put("/api/v1/walls/{wall_id}", response_model=Wall)
async def update_wall(wall_id: int, payload: WallIn, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> Wall:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE walls SET name=$2, wall_type=$3, tile_count=$4, resolution=$5, tags=$6, updated_at=NOW()
            WHERE id=$1
            RETURNING id, name, wall_type, tile_count, resolution, tags
            """,
            wall_id, payload.name, payload.wall_type, payload.tile_count, payload.resolution, payload.tags
        )
    if not row:
        raise KeyError("wall_not_found")
    w = Wall(**dict(row))
    await append_audit_event(action="walls.update", actor=actor, object_type="wall", object_id=str(w.id), details=w.model_dump())
    return w


@app.delete("/api/v1/walls/{wall_id}", status_code=204)
async def delete_wall(wall_id: int, user: dict[str, Any] = Depends(require_role("admin"))) -> None:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM walls WHERE id=$1", wall_id)
    if res.endswith("0"):
        raise KeyError("wall_not_found")
    await append_audit_event(action="walls.delete", actor=actor, object_type="wall", object_id=str(wall_id), details={})
    return None


# ---- Sources ----

@app.get("/api/v1/sources", response_model=list[Source])
async def list_sources(_: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> list[Source]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, source_type, protocol, endpoint_url, codec, tags, health_status FROM sources ORDER BY id")
    return [Source(**dict(r)) for r in rows]


@app.post("/api/v1/sources", response_model=Source, status_code=201)
async def create_source(payload: SourceIn, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> Source:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sources (name, source_type, protocol, endpoint_url, codec, tags, health_status)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id, name, source_type, protocol, endpoint_url, codec, tags, health_status
            """,
            payload.name, payload.source_type, payload.protocol, payload.endpoint_url, payload.codec, payload.tags, payload.health_status
        )
    s = Source(**dict(row))
    await append_audit_event(action="sources.create", actor=actor, object_type="source", object_id=str(s.id), details=s.model_dump())
    return s


@app.get("/api/v1/sources/{source_id}", response_model=Source)
async def get_source(source_id: int, _: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> Source:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, source_type, protocol, endpoint_url, codec, tags, health_status FROM sources WHERE id=$1",
            source_id
        )
    if not row:
        raise KeyError("source_not_found")
    return Source(**dict(row))


@app.put("/api/v1/sources/{source_id}", response_model=Source)
async def update_source(source_id: int, payload: SourceIn, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> Source:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE sources
            SET name=$2, source_type=$3, protocol=$4, endpoint_url=$5, codec=$6, tags=$7, health_status=$8, updated_at=NOW()
            WHERE id=$1
            RETURNING id, name, source_type, protocol, endpoint_url, codec, tags, health_status
            """,
            source_id, payload.name, payload.source_type, payload.protocol, payload.endpoint_url, payload.codec, payload.tags, payload.health_status
        )
    if not row:
        raise KeyError("source_not_found")
    s = Source(**dict(row))
    await append_audit_event(action="sources.update", actor=actor, object_type="source", object_id=str(s.id), details=s.model_dump())
    return s


@app.delete("/api/v1/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, user: dict[str, Any] = Depends(require_role("admin"))) -> None:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM sources WHERE id=$1", source_id)
    if res.endswith("0"):
        raise KeyError("source_not_found")
    await append_audit_event(action="sources.delete", actor=actor, object_type="source", object_id=str(source_id), details={})
    return None


# ---- Layouts ----

@app.get("/api/v1/layouts", response_model=list[Layout])
async def list_layouts(_: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> list[Layout]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, wall_id, name, version, grid_config, preset_name, is_active, created_by, created_at
            FROM layouts
            ORDER BY wall_id, version DESC
            """
        )
    out: list[Layout] = []
    for r in rows:
        d = dict(r)
        d["grid_config"] = dict(d["grid_config"])
        d["created_at"] = d["created_at"].isoformat()
        out.append(Layout(**d))
    return out


@app.post("/api/v1/layouts", response_model=Layout, status_code=201)
async def create_layout(payload: LayoutIn, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> Layout:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        async with conn.transaction():
            version = await ensure_layout_version(conn, payload.wall_id)
            row = await conn.fetchrow(
                """
                INSERT INTO layouts (wall_id, name, version, grid_config, preset_name, is_active, created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                RETURNING id, wall_id, name, version, grid_config, preset_name, is_active, created_by, created_at
                """,
                payload.wall_id, payload.name, version, json.dumps(payload.grid_config), payload.preset_name, payload.is_active, actor
            )
            if payload.is_active:
                await conn.execute("UPDATE layouts SET is_active=FALSE WHERE wall_id=$1 AND id<>$2", payload.wall_id, row["id"])
    d = dict(row)
    d["grid_config"] = dict(d["grid_config"])
    d["created_at"] = d["created_at"].isoformat()
    layout = Layout(**d)
    await append_audit_event(action="layouts.create", actor=actor, object_type="layout", object_id=str(layout.id), details=layout.model_dump())
    return layout


@app.get("/api/v1/layouts/{layout_id}", response_model=Layout)
async def get_layout(layout_id: int, _: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> Layout:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, wall_id, name, version, grid_config, preset_name, is_active, created_by, created_at
            FROM layouts WHERE id=$1
            """,
            layout_id
        )
    if not row:
        raise KeyError("layout_not_found")
    d = dict(row)
    d["grid_config"] = dict(d["grid_config"])
    d["created_at"] = d["created_at"].isoformat()
    return Layout(**d)


@app.put("/api/v1/layouts/{layout_id}", response_model=Layout)
async def update_layout(layout_id: int, payload: LayoutIn, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> Layout:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row0 = await conn.fetchrow("SELECT created_by, created_at, version FROM layouts WHERE id=$1", layout_id)
            if not row0:
                raise KeyError("layout_not_found")
            created_by = str(row0["created_by"])
            created_at = row0["created_at"]
            version = int(row0["version"])

            row = await conn.fetchrow(
                """
                UPDATE layouts
                SET wall_id=$2, name=$3, grid_config=$4, preset_name=$5, is_active=$6
                WHERE id=$1
                RETURNING id, wall_id, name, version, grid_config, preset_name, is_active
                """,
                layout_id, payload.wall_id, payload.name, json.dumps(payload.grid_config), payload.preset_name, payload.is_active
            )
            if payload.is_active:
                await conn.execute("UPDATE layouts SET is_active=FALSE WHERE wall_id=$1 AND id<>$2", payload.wall_id, layout_id)

    d = dict(row)
    d["grid_config"] = dict(d["grid_config"])
    d["created_by"] = created_by
    d["created_at"] = created_at.isoformat()
    d["version"] = version
    layout = Layout(**d)
    await append_audit_event(action="layouts.update", actor=actor, object_type="layout", object_id=str(layout.id), details=layout.model_dump())
    return layout


@app.delete("/api/v1/layouts/{layout_id}", status_code=204)
async def delete_layout(layout_id: int, user: dict[str, Any] = Depends(require_role("admin"))) -> None:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM layouts WHERE id=$1", layout_id)
    if res.endswith("0"):
        raise KeyError("layout_not_found")
    await append_audit_event(action="layouts.delete", actor=actor, object_type="layout", object_id=str(layout_id), details={})
    return None


@app.put("/api/v1/layouts/{layout_id}/activate")
async def activate(layout_id: int, user: dict[str, Any] = Depends(require_role("operator", "admin"))) -> dict[str, Any]:
    pool = await get_pool()
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await activate_layout(conn, layout_id)
    await append_audit_event(action="layouts.activate", actor=actor, object_type="layout", object_id=str(layout_id), details=updated)
    return {"activated": True, "layout": updated}


# ---- Policy proxy + token ----

async def _policy_evaluate(req: PolicyEvalRequest) -> PolicyEvalResponse:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(f"{settings.policy_url}/evaluate", json=req.model_dump())
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="policy_service_error")
    return PolicyEvalResponse(**r.json())


@app.post("/api/v1/policy/evaluate", response_model=PolicyEvalResponse)
async def policy_evaluate(payload: dict[str, Any], user: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> PolicyEvalResponse:
    roles = list(user.get("_roles") or [])
    operator_id = str(user.get("sub") or "")
    operator_tags = list(user.get("tags") or user.get("groups") or [])
    req = PolicyEvalRequest(
        wall_id=int(payload["wall_id"]),
        source_id=int(payload["source_id"]),
        operator_id=operator_id,
        operator_roles=roles,
        operator_tags=[str(x) for x in operator_tags],
    )
    result = await _policy_evaluate(req)
    await append_audit_event(
        action="policy.evaluate",
        actor=operator_id,
        object_type="policy",
        object_id=f"{req.wall_id}:{req.source_id}",
        details={"allowed": result.allowed, "reason": result.reason},
    )
    return result


def _mint_stream_token(*, sub: str, wall_id: int, source_id: int, tile_id: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=int(settings.stream_token_ttl_seconds))
    claims = {
        "sub": sub,
        "wall_id": wall_id,
        "source_id": source_id,
        "tile_id": tile_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "vw-stream",
    }
    return jwt.encode(claims, settings.stream_token_secret, algorithm=Algorithms.HS256)


@app.post("/api/v1/tokens/subscribe", response_model=TokenSubscribeResponse)
async def tokens_subscribe(payload: TokenSubscribeRequest, user: dict[str, Any] = Depends(require_role("viewer", "operator", "admin"))) -> TokenSubscribeResponse:
    roles = list(user.get("_roles") or [])
    operator_id = str(user.get("sub") or "")
    operator_tags = list(user.get("tags") or user.get("groups") or [])
    preq = PolicyEvalRequest(
        wall_id=payload.wall_id,
        source_id=payload.source_id,
        operator_id=operator_id,
        operator_roles=roles,
        operator_tags=[str(x) for x in operator_tags],
    )
    presp = await _policy_evaluate(preq)
    if not presp.allowed:
        await append_audit_event(
            action="tokens.subscribe.deny",
            actor=operator_id,
            object_type="token",
            object_id=f"{payload.wall_id}:{payload.source_id}:{payload.tile_id}",
            details={"reason": presp.reason, "wall_id": payload.wall_id, "source_id": payload.source_id},
        )
        return TokenSubscribeResponse(allowed=False, reason=presp.reason, token=None)
    token = _mint_stream_token(sub=operator_id, wall_id=payload.wall_id, source_id=payload.source_id, tile_id=payload.tile_id)
    await append_audit_event(
        action="tokens.subscribe.allow",
        actor=operator_id,
        object_type="token",
        object_id=f"{payload.wall_id}:{payload.source_id}:{payload.tile_id}",
        details={"wall_id": payload.wall_id, "source_id": payload.source_id, "tile_id": payload.tile_id},
    )
    return TokenSubscribeResponse(allowed=True, reason="allowed", token=token)


# ---- Bundles ----

@app.post("/api/v1/bundles/export", response_model=BundleExport)
async def bundles_export(_: dict[str, Any] = Depends(require_role("admin"))) -> BundleExport:
    pool = await get_pool()
    async with pool.acquire() as conn:
        walls = [dict(r) for r in await conn.fetch("SELECT id, name, wall_type, tile_count, resolution, tags FROM walls ORDER BY id")]
        sources = [dict(r) for r in await conn.fetch("SELECT id, name, source_type, protocol, endpoint_url, codec, tags, health_status FROM sources ORDER BY id")]
        active_layouts = []
        rows = await conn.fetch(
            """
            SELECT id, wall_id, name, version, grid_config, preset_name, is_active, created_by, created_at
            FROM layouts WHERE is_active=TRUE
            ORDER BY wall_id
            """
        )
        for r in rows:
            d = dict(r)
            d["grid_config"] = dict(d["grid_config"])
            d["created_at"] = d["created_at"].isoformat()
            active_layouts.append(d)
    return BundleExport(walls=walls, sources=sources, active_layouts=active_layouts)


def _hmac_hex(secret: str, payload: dict[str, Any]) -> str:
    mac = hmac.new(secret.encode("utf-8"), _json(payload).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


@app.post("/api/v1/bundles/import")
async def bundles_import(req: BundleImportRequest, user: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    actor = str(user.get("preferred_username") or user.get("sub") or "unknown")
    if settings.bundle_hmac_secret.strip():
        if not req.hmac_hex:
            raise HTTPException(status_code=400, detail="missing_hmac")
        expected = _hmac_hex(settings.bundle_hmac_secret.strip(), req.payload)
        if not hmac.compare_digest(expected, req.hmac_hex.lower()):
            raise HTTPException(status_code=400, detail="invalid_hmac")

    await append_audit_event(
        action="bundles.import.stage",
        actor=actor,
        object_type="bundle",
        object_id=req.ring,
        details={"ring": req.ring, "payload": req.payload},
    )
    return {"staged": True, "ring": req.ring}


# ---- Audit query ----

@app.get("/api/v1/audit/query", response_model=list[AuditEventOut])
async def audit_query(
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    limit: int = 200,
    _: dict[str, Any] = Depends(require_role("admin")),
) -> list[AuditEventOut]:
    limit = max(1, min(limit, 1000))
    pool = await get_pool()

    clauses = ["chain_id=$1"]
    args: list[Any] = [settings.audit_chain_id]
    idx = 2

    if action:
        clauses.append(f"action=${idx}")
        args.append(action)
        idx += 1
    if actor:
        clauses.append(f"actor=${idx}")
        args.append(actor)
        idx += 1
    if since:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid_since:{type(e).__name__}") from e
        clauses.append(f"ts>=${idx}")
        args.append(dt)
        idx += 1

    where = " AND ".join(clauses)
    q = f"""
        SELECT id, ts, action, actor, object_type, object_id, details, prev_hash, hash
        FROM audit_events
        WHERE {where}
        ORDER BY id DESC
        LIMIT {limit}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *args)

    out: list[AuditEventOut] = []
    for r in rows:
        d = dict(r)
        d["ts"] = d["ts"].isoformat()
        if isinstance(d["details"], str):
            d["details"] = json.loads(d["details"])
        out.append(AuditEventOut(**d))
    return out


# ---- Audit verify / export proxies ----

@app.get("/api/v1/audit/verify")
async def audit_verify(last_n: int = 1000, _: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    """Proxy to vw-audit /verify endpoint — walks the hash chain and reports integrity."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{settings.audit_url}/verify", params={"last_n": last_n})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="audit_service_error")
    return r.json()


@app.get("/api/v1/audit/export")
async def audit_export(
    since: str | None = None,
    until: str | None = None,
    _: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Proxy to vw-audit /export endpoint — returns signed JSONL."""
    params: dict[str, str] = {}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{settings.audit_url}/export", params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="audit_service_error")
    return r.json()


# ---- Gateway probe proxy ----

@app.post("/api/v1/gateway/probe")
async def gateway_probe(payload: dict[str, Any], _: dict[str, Any] = Depends(require_role("operator", "admin"))) -> dict[str, Any]:
    """Proxy probe request to vw-gateway for source onboarding validation."""
    gw_url = settings.health_url.replace("vw-health", "vw-gw").replace(":8003", ":8004")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{gw_url}/probe", json=payload)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="gateway_probe_error")
    return r.json()


# ---- Config reconciliation ----

@app.post("/api/v1/config/reconcile")
async def config_reconcile(_: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    """Manually trigger config reconciliation from vw-config into DB."""
    result = await reconcile_once()
    return {"reconciled": True, **result}
