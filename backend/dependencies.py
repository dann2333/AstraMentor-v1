"""Shared FastAPI dependencies for authenticating requests."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.account_service import (
    AccountDisabled,
    AccountService,
    InvalidToken,
    User,
    account_service,
)


bearer_scheme = HTTPBearer(auto_error=False)


def get_account_service() -> AccountService:
    """Indirection point so tests can override the backing service."""
    return account_service


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AccountService = Depends(get_account_service),
) -> User:
    """Resolve the ``Authorization: Bearer <token>`` header into an account."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("authentication required")
    try:
        user = service.resolve_token(credentials.credentials)
    except InvalidToken as exc:
        raise _unauthorized(str(exc)) from exc
    except AccountDisabled as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    # NOTE: 保存到 request.state，便于日志与后续依赖复用当前身份。
    request.state.current_user = user
    return user


def get_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Return the raw bearer token so endpoints can revoke the current session."""
    return credentials.credentials if credentials else ""
