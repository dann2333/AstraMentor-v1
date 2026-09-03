"""Login and account-management endpoints backed by the SQLite account store."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.dependencies import get_access_token, get_account_service, get_current_user
from backend.models import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    TokenSummary,
    UpdateProfileRequest,
    UserResponse,
)
from services.account_service import (
    AccountDisabled,
    AccountLocked,
    AccountService,
    EmailTaken,
    InvalidCredentials,
    User,
    UsernameTaken,
    UserNotFound,
    ValidationError,
)


auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(issued) -> TokenResponse:
    return TokenResponse(
        access_token=issued.token,
        expires_at=issued.expires_at,
        user=UserResponse(**issued.user.to_dict()),
    )


@auth_router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
def register(
    request: RegisterRequest,
    service: AccountService = Depends(get_account_service),
) -> TokenResponse:
    """Create an account and sign the caller in with a fresh token."""
    try:
        user = service.register(
            request.username,
            request.password,
            email=request.email,
            display_name=request.display_name,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UsernameTaken, EmailTaken) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _token_response(service.issue_token(user.id, label="registration"))


@auth_router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    service: AccountService = Depends(get_account_service),
) -> TokenResponse:
    """Exchange username (or email) and password for a bearer token."""
    try:
        issued = service.login(request.username, request.password, label=request.label)
    except AccountLocked as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AccountDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return _token_response(issued)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    _user: User = Depends(get_current_user),
    token: str = Depends(get_access_token),
    service: AccountService = Depends(get_account_service),
) -> None:
    """Revoke only the token that authenticated this request."""
    service.revoke_token(token)


@auth_router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    user: User = Depends(get_current_user),
    token: str = Depends(get_access_token),
    service: AccountService = Depends(get_account_service),
) -> None:
    """Revoke every other session, keeping the current token usable."""
    service.revoke_all_tokens(user.id, keep_token=token)


@auth_router.get("/me", response_model=UserResponse)
def read_profile(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(**user.to_dict())


@auth_router.patch("/me", response_model=UserResponse)
def update_profile(
    request: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> UserResponse:
    """Update the display name and/or email of the signed-in account."""
    try:
        updated = service.update_profile(
            user.id,
            display_name=request.display_name,
            email=request.email,
            clear_email=request.clear_email,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmailTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UserNotFound as exc:
        raise HTTPException(status_code=404, detail="account not found") from exc
    return UserResponse(**updated.to_dict())


@auth_router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    request: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> None:
    """Change the password; every existing token is revoked on success."""
    try:
        service.change_password(
            user.id, request.current_password, request.new_password
        )
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UserNotFound as exc:
        raise HTTPException(status_code=404, detail="account not found") from exc


@auth_router.get("/me/tokens", response_model=list[TokenSummary])
def list_tokens(
    active_only: bool = Query(default=True),
    user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> list[TokenSummary]:
    """List the account's sessions so a stale one can be spotted and revoked."""
    return [TokenSummary(**item) for item in service.list_tokens(user.id, active_only=active_only)]


@auth_router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    request: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> None:
    """Delete the account and every row that belongs to it, after re-auth."""
    try:
        service.authenticate(user.username, request.password)
    except AccountLocked as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (InvalidCredentials, AccountDisabled) as exc:
        raise HTTPException(status_code=401, detail="password is incorrect") from exc
    try:
        service.delete_user(user.id)
    except UserNotFound as exc:
        raise HTTPException(status_code=404, detail="account not found") from exc
