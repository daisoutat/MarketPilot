"""Cognito JWT verification (JWKS) + FastAPI auth dependency.

`AUTH_MODE=dev` (default locally) bypasses verification; `AUTH_MODE=cognito`
validates RS256 tokens against the user pool's well-known JWKS URL.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.config import get_settings

JWKS_TTL = 300  # seconds
_jwks_cache = {"at": 0.0, "keys": None}


def _jwks_url(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"


async def _get_keys() -> dict[str, Any]:
    settings = get_settings()
    now = time.time()
    if _jwks_cache["keys"] is not None and now - _jwks_cache["at"] < JWKS_TTL:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(_jwks_url(settings.cognito_region, settings.cognito_user_pool_id))
        resp.raise_for_status()
        keys = {k["kid"]: k for k in resp.json().get("keys", [])}
    _jwks_cache["at"] = now
    _jwks_cache["keys"] = keys
    return keys


async def _verify_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    keys = await _get_keys()
    unverified = jwt.get_unverified_header(token)
    kid = unverified.get("kid")
    key = keys.get(kid)
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown key id")
    issuer = _jwks_url(settings.cognito_region, settings.cognito_user_pool_id)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    aud = payload.get("aud") or payload.get("client_id") or ""
    if settings.cognito_client_id and aud != settings.cognito_client_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong audience")
    return payload


def _dev_user() -> dict[str, Any]:
    parts = get_settings().dev_user.split("|") + ["", "", ""]
    email, name, role = parts[:3]
    return {
        "sub": "dev-user",
        "username": email.split("@")[0],
        "email": email,
        "display_name": name or email,
        "groups": [role] if role else ["viewer"],
    }


bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    settings = get_settings()
    if settings.auth_mode != "cognito":
        return _dev_user()
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    payload = await _verify_token(credentials.credentials)
    groups = payload.get("cognito:groups") or []
    return {
        "sub": payload.get("sub"),
        "username": payload.get("username") or payload.get("cognito:username"),
        "email": payload.get("email") or "",
        "display_name": payload.get("name") or payload.get("email") or "",
        "groups": groups,
    }


def require_role(*allowed: str):
    async def checker(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        groups = {g.lower() for g in user.get("groups", [])}
        got = groups or {"viewer"}
        if not (got & {g.lower() for g in allowed}):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return checker