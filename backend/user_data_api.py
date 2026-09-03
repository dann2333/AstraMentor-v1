"""Learning-session storage scoped to the signed-in account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.dependencies import get_current_user
from backend.models import UserSessionSnapshotRequest
from services.account_service import User
from services.session_repository import InvalidSessionId, SessionNotFound
from services.user_data_repository import (
    SnapshotTooLarge,
    UserDataRepository,
    user_data_repository,
)


user_data_router = APIRouter(prefix="/me", tags=["user-data"])


def get_user_data_repository() -> UserDataRepository:
    """Indirection point so tests can override the backing repository."""
    return user_data_repository


@user_data_router.get("/sessions")
def list_sessions(
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(get_current_user),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    return {"sessions": repository.list(user.id, limit=limit)}


@user_data_router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    try:
        return repository.get(user.id, session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="learning session not found") from exc


@user_data_router.put("/sessions/{session_id}")
def save_session(
    session_id: str,
    request: UserSessionSnapshotRequest,
    user: User = Depends(get_current_user),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    if request.session_id != session_id:
        raise HTTPException(
            status_code=422, detail="path and payload session ids must match"
        )
    try:
        return repository.save(user.id, session_id, request.model_dump(mode="json"))
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SnapshotTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


@user_data_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> None:
    try:
        repository.delete(user.id, session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="learning session not found") from exc
