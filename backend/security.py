"""Supabase Auth JWT verification and role checks."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .database import Row, Store, get_store

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    email: str | None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        if s.supabase_jwt_secret:
            return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience=s.jwt_audience)
        if s.supabase_url:
            key = _jwks_client(f"{s.supabase_url}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token)
            return jwt.decode(token, key.key, algorithms=["ES256", "RS256"], audience=s.jwt_audience)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc
    raise HTTPException(503, "auth is not configured (set SUPABASE_JWT_SECRET or SUPABASE_URL)")


def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> AuthUser | None:
    if creds is None:
        return None
    claims = decode_token(creds.credentials)
    try:
        return AuthUser(id=UUID(claims["sub"]), email=claims.get("email"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "token has no valid subject") from exc


def current_user(user: AuthUser | None = Depends(optional_user)) -> AuthUser:
    if user is None:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return user


def current_profile(user: AuthUser = Depends(current_user), store: Store = Depends(get_store)) -> Row:
    """Authenticated user that has also created a game profile (username)."""
    profile = store.get_user(user.id)
    if profile is None:
        raise HTTPException(403, "create a profile first: POST /api/users/me")
    return profile


def require_admin(profile: Row = Depends(current_profile)) -> Row:
    if profile["role"] != "admin":
        raise HTTPException(403, "admin only")
    return profile
