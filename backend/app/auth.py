import asyncio
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(
        f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json",
        timeout=5,
        cache_jwk_set=True,
        lifespan=300,
    )


def _verify_sync(token: str, url: str) -> dict[str, Any]:
    client = _jwks_client(url)
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256"],
        audience="authenticated",
        issuer=f"{url.rstrip('/')}/auth/v1",
        options={"require": ["exp", "iat", "sub"]},
    )


async def get_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return credentials.credentials


async def get_current_user(token: str = Depends(get_access_token)) -> dict[str, Any]:

    url = get_settings().supabase_url
    if not url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured")

    try:
        claims = await asyncio.to_thread(_verify_sync, token, url)
    except (jwt.PyJWTError, ValueError, OSError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from None

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    return claims
