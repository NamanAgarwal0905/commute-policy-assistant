"""JWT creation and verification.

The company comes ONLY from a verified token (and is cross-checked against the user
database). Nothing in a request body, query string or question text can change it.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.container import Container, get_container

_bearer = HTTPBearer(auto_error=False)  # we raise our own 401 instead of FastAPI's default 403
REQUIRED_CLAIMS = ["sub", "company_id", "role", "iat", "exp"]


@dataclass(frozen=True)
class CurrentUser:
    username: str
    company_id: str
    role: str


def create_token(settings, username: str, company_id: str, role: str):
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.token_ttl_minutes)
    claims = {"sub": username, "company_id": company_id, "role": role, "iat": now, "exp": exp}
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, settings.token_ttl_minutes * 60


def _unauthorized(detail: str = "Invalid or missing token") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    container: Container = Depends(get_container),
) -> CurrentUser:
    if creds is None:
        raise _unauthorized()
    s = container.settings
    try:
        # Pin the algorithm list (never trust the token's own "alg" header) and require claims.
        claims = jwt.decode(
            creds.credentials,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError:
        raise _unauthorized()

    # The token must match the identity store: same user, same company, same role.
    user = container.db.get_user(str(claims["sub"]))
    if (
        user is None
        or user["company_id"] != claims["company_id"]
        or user["role"] != claims["role"]
        or not container.db.company_exists(user["company_id"])
    ):
        raise _unauthorized()
    return CurrentUser(user["username"], user["company_id"], user["role"])


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
