"""Shared FastAPI dependencies for authenticating and authorising requests."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_config
from services.account_service import (
    AccountDisabled,
    AccountService,
    InvalidToken,
    User,
    account_service,
)
from services.database import ANONYMOUS_OWNER_ID


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


def _resolve(service: AccountService, token: str) -> User:
    try:
        return service.resolve_token(token)
    except InvalidToken as exc:
        raise _unauthorized(str(exc)) from exc
    except AccountDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AccountService = Depends(get_account_service),
) -> User:
    """Resolve the ``Authorization: Bearer <token>`` header into an account."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("authentication required")
    user = _resolve(service, credentials.credentials)
    # NOTE: 保存到 request.state，便于日志与后续依赖复用当前身份。
    request.state.current_user = user
    return user


def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AccountService = Depends(get_account_service),
) -> User | None:
    """Resolve the caller when a token is present, otherwise return ``None``.

    这条依赖只用于"学习"这类允许访客使用的接口。注意它对**无效**令牌仍然报错：
    只有完全没带令牌才算访客，否则一个过期令牌会静默降级成访客身份，
    用户看到的会是别人的（访客的）数据，而不是一个明确的 401。
    """
    if credentials is None or not credentials.credentials:
        if not get_config().auth.allow_anonymous:
            raise _unauthorized("authentication required")
        return None
    user = _resolve(service, credentials.credentials)
    request.state.current_user = user
    return user


def get_owner_id(user: User | None = Depends(get_optional_user)) -> str:
    """The row-ownership key for the caller: their account id, or the guest id."""
    return user.id if user is not None else ANONYMOUS_OWNER_ID


def require_teacher(user: User = Depends(get_current_user)) -> User:
    """Gate teacher-only endpoints; admins are allowed through as well."""
    if not user.is_teacher:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this action requires a teacher account",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this action requires an administrator account",
        )
    return user


def get_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Return the raw bearer token so endpoints can revoke the current session."""
    return credentials.credentials if credentials else ""
