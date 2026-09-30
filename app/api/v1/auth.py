"""Authentication dependencies.

Astrocoda issues two interchangeable credentials for every consumer:

* a long lived **API key** (``X-API-Key`` header) for server to server calls,
* a short lived **JWT access token** (``Authorization: Bearer ...``) minted for
  browser dashboards and single page applications.

Both resolve to the same :class:`~app.database.db.User` object, so downstream
routes only ever see a resolved user - never a raw secret.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.database.db import SessionDependency, User, get_user_by_api_key, get_user_by_id

logger = logging.getLogger(__name__)

__all__ = [
    "API_KEY_HEADER",
    "CurrentUser",
    "create_access_token",
    "decode_access_token",
    "get_api_key",
    "get_bearer_token",
    "get_current_user",
    "require_active_user",
]

API_KEY_HEADER: str = "X-API-Key"
BEARER_SCHEME: str = "Bearer"
TOKEN_ISSUER: str = "astrocoda"

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token minted with create_access_token().",
)


def _unauthorized(detail: str) -> HTTPException:
    """Build a 401 with the correct ``WWW-Authenticate`` header attached."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": BEARER_SCHEME},
    )


# ---------------------------------------------------------------------------
# Credential extraction
# ---------------------------------------------------------------------------
async def get_api_key(
    x_api_key: Annotated[
        str | None,
        Header(
            alias=API_KEY_HEADER,
            description="Astrocoda consumer API key, e.g. astro_<random>.",
        ),
    ] = None,
) -> str:
    """Pull the API key out of the ``X-API-Key`` header."""
    if x_api_key is None or not x_api_key.strip():
        raise _unauthorized(f"Missing {API_KEY_HEADER} header.")
    return x_api_key.strip()


async def get_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_bearer_scheme),
    ] = None,
) -> str | None:
    """Pull a JWT out of the ``Authorization`` header when one is present."""
    if credentials is None or not credentials.credentials:
        return None
    if credentials.scheme.lower() != BEARER_SCHEME.lower():
        raise _unauthorized("Unsupported authorization scheme. Expected 'Bearer'.")
    return credentials.credentials


# ---------------------------------------------------------------------------
# JWT primitives
# ---------------------------------------------------------------------------
def create_access_token(
    user: User,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """Mint a signed access token for ``user``.

    Returns:
        ``(token, expires_in_seconds)`` - ready to be handed straight to a
        single page application.
    """
    now = datetime.now(timezone.utc)
    lifetime = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "is_active": user.is_active,
        "jti": secrets.token_hex(16),
        "iss": TOKEN_ISSUER,
        "typ": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
    }
    token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode a token, converting every failure into a ``401``."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=TOKEN_ISSUER,
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Access token is malformed or badly signed.") from exc

    if claims.get("typ") != "access":
        raise _unauthorized("Unsupported token type.")
    return claims


# ---------------------------------------------------------------------------
# Route dependencies
# ---------------------------------------------------------------------------
async def get_current_user(
    session: SessionDependency,
    api_key: Annotated[str, Depends(get_api_key)],
    bearer_token: Annotated[str | None, Security(get_bearer_token)] = None,
) -> User:
    """Resolve the calling consumer from an API key or a bearer token.

    The API key is always required and is the primary credential.  A bearer
    token, when supplied, takes precedence for identifying the user, which
    lets a browser client rotate through short lived tokens while still
    sending the account key for auditing.
    """
    user: User | None = None

    if bearer_token is not None:
        claims = decode_access_token(bearer_token)
        subject = claims.get("sub")
        try:
            user = await get_user_by_id(session, UUID(str(subject)))
        except (TypeError, ValueError) as exc:
            raise _unauthorized("Access token subject is not a valid user id.") from exc

    if user is None:
        user = await get_user_by_api_key(session, api_key)

    if user is None:
        raise _unauthorized("Credentials are not associated with an Astrocoda account.")
    return user


async def require_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency variant that also enforces the Stripe subscription gate.

    Raises ``403`` with the message below when billing is not in good standing.
    """
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive subscription. Please activate billing.",
        )
    return user


#: Typed alias for routes that only need a resolved user.
CurrentUser = Annotated[User, Depends(get_current_user)]
