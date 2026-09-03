"""Learning-session endpoints for the homepage history rail.

会话按归属账号隔离：登录用户看到自己的历史，未登录访客共用预留的访客账号。
需要强制登录时把 ``ASTRA_ALLOW_ANONYMOUS`` 设为 ``false``，同一批接口即会要求令牌。
需要显式的"必须登录"语义时用 ``/api/me/sessions``（见 ``user_data_api``）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.dependencies import get_owner_id
from backend.models import SessionSnapshotRequest
from backend.user_data_api import get_user_data_repository
from services.user_data_repository import (
    InvalidSessionId,
    SessionNotFound,
    SnapshotTooLarge,
    UserDataRepository,
)


session_router = APIRouter(prefix="/sessions", tags=["sessions"])


@session_router.get("")
async def list_sessions(
    limit: int = Query(default=6, ge=1, le=100),
    owner_id: str = Depends(get_owner_id),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    return {"sessions": repository.list(owner_id, limit=limit)}


@session_router.get("/{session_id}")
async def get_session(
    session_id: str,
    owner_id: str = Depends(get_owner_id),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    try:
        return repository.get(owner_id, session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(
            status_code=404, detail="learning session not found"
        ) from exc


@session_router.put("/{session_id}")
async def save_session(
    session_id: str,
    request: SessionSnapshotRequest,
    owner_id: str = Depends(get_owner_id),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> dict:
    if request.session_id != session_id:
        raise HTTPException(
            status_code=422, detail="path and payload session ids must match"
        )
    try:
        return repository.save(owner_id, session_id, request.model_dump(mode="json"))
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SnapshotTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


@session_router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    owner_id: str = Depends(get_owner_id),
    repository: UserDataRepository = Depends(get_user_data_repository),
) -> None:
    try:
        repository.delete(owner_id, session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(
            status_code=404, detail="learning session not found"
        ) from exc
