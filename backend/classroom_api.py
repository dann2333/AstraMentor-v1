"""班级与师生关系接口。

这一组接口**始终**要求登录，不受 ``ASTRA_ALLOW_ANONYMOUS`` 影响：班级成员关系
必须挂在真实账号上，访客身份是共享的，放进来会让所有访客变成同一个"学生"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.dependencies import get_current_user, require_admin, require_teacher
from backend.models import (
    CreateClassroomRequest,
    JoinClassroomRequest,
    SetUserRoleRequest,
    UpdateClassroomRequest,
    UserResponse,
)
from services.account_service import (
    AccountService,
    SystemAccountProtected,
    User,
    UserNotFound,
    ValidationError as AccountValidationError,
)
from services.assignment_service import AssignmentService, assignment_service
from services.classroom_service import (
    AlreadyEnrolled,
    CannotEnrollSelf,
    ClassroomArchived,
    ClassroomNotFound,
    ClassroomService,
    InvalidJoinCode,
    NotEnrolled,
    TooManyJoinAttempts,
    ValidationError,
    classroom_service,
)
from backend.dependencies import get_account_service


classroom_router = APIRouter(prefix="/classrooms", tags=["classrooms"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_classroom_service() -> ClassroomService:
    """Indirection point so tests can override the backing service."""
    return classroom_service


def get_assignment_service() -> AssignmentService:
    """Indirection point so tests can override the backing service."""
    return assignment_service


def _not_found() -> HTTPException:
    # 不存在与无权访问返回同一个 404：否则调用方能靠状态码探测别人的班级。
    return HTTPException(status_code=404, detail="classroom not found")


# ----------------------------------------------------------------------
# 老师视角
# ----------------------------------------------------------------------
@classroom_router.post("", status_code=status.HTTP_201_CREATED)
def create_classroom(
    request: CreateClassroomRequest,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    try:
        classroom = service.create_classroom(
            teacher.id, request.name, request.description
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return classroom.to_dict(include_join_code=True)


@classroom_router.get("/taught")
def list_taught_classrooms(
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    return {
        "classrooms": [
            item.to_dict(include_join_code=True)
            for item in service.list_taught(teacher.id)
        ]
    }


@classroom_router.patch("/{classroom_id}")
def update_classroom(
    classroom_id: str,
    request: UpdateClassroomRequest,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    try:
        classroom = service.update_classroom(
            classroom_id,
            teacher.id,
            is_admin=teacher.is_admin,
            name=request.name,
            description=request.description,
            is_archived=request.is_archived,
        )
    except ClassroomNotFound as exc:
        raise _not_found() from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return classroom.to_dict(include_join_code=True)


@classroom_router.post("/{classroom_id}/join-code/rotate")
def rotate_join_code(
    classroom_id: str,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    """换一张邀请码，旧码立即失效。"""
    try:
        classroom = service.rotate_join_code(
            classroom_id, teacher.id, is_admin=teacher.is_admin
        )
    except ClassroomNotFound as exc:
        raise _not_found() from exc
    return classroom.to_dict(include_join_code=True)


@classroom_router.delete("/{classroom_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_classroom(
    classroom_id: str,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> None:
    try:
        service.delete_classroom(classroom_id, teacher.id, is_admin=teacher.is_admin)
    except ClassroomNotFound as exc:
        raise _not_found() from exc


@classroom_router.get("/{classroom_id}/members")
def list_members(
    classroom_id: str,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    """完整成员名单只对本班老师开放。"""
    try:
        members = service.list_members(
            classroom_id, teacher.id, is_admin=teacher.is_admin
        )
    except ClassroomNotFound as exc:
        raise _not_found() from exc
    return {"members": [member.to_dict() for member in members]}


@classroom_router.get("/{classroom_id}/progress")
def classroom_progress(
    classroom_id: str,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    """本班每位学生的作业完成度，供老师面板使用。"""
    try:
        return {
            "students": service.classroom_progress(
                classroom_id, teacher.id, is_admin=teacher.is_admin
            )
        }
    except ClassroomNotFound as exc:
        raise _not_found() from exc


@classroom_router.delete(
    "/{classroom_id}/members/{student_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(
    classroom_id: str,
    student_id: str,
    teacher: User = Depends(require_teacher),
    service: ClassroomService = Depends(get_classroom_service),
) -> None:
    try:
        service.remove_member(
            classroom_id, student_id, teacher.id, is_admin=teacher.is_admin
        )
    except ClassroomNotFound as exc:
        raise _not_found() from exc
    except NotEnrolled as exc:
        raise HTTPException(status_code=404, detail="student is not a member") from exc


# ----------------------------------------------------------------------
# 学生视角
# ----------------------------------------------------------------------
@classroom_router.get("/enrolled")
def list_enrolled_classrooms(
    user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    return {
        "classrooms": [
            item.to_dict(include_join_code=False)
            for item in service.list_enrolled(user.id)
        ]
    }


@classroom_router.post("/join")
def join_classroom(
    request: JoinClassroomRequest,
    user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    try:
        classroom = service.join_by_code(user.id, request.join_code)
    except TooManyJoinAttempts as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except InvalidJoinCode as exc:
        raise HTTPException(status_code=404, detail="join code is not valid") from exc
    except ClassroomArchived as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CannotEnrollSelf as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AlreadyEnrolled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return classroom.to_dict(include_join_code=False)


@classroom_router.get("/{classroom_id}")
def get_classroom(
    classroom_id: str,
    user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> dict:
    """老师和在册学生都能看班级详情；邀请码只给老师。"""
    try:
        classroom = service.get_for_view(classroom_id, user.id, is_admin=user.is_admin)
    except ClassroomNotFound as exc:
        raise _not_found() from exc
    manages = user.is_admin or classroom.teacher_id == user.id
    return classroom.to_dict(include_join_code=manages)


@classroom_router.post(
    "/{classroom_id}/leave", status_code=status.HTTP_204_NO_CONTENT
)
def leave_classroom(
    classroom_id: str,
    user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> None:
    try:
        service.leave_classroom(classroom_id, user.id)
    except NotEnrolled as exc:
        raise HTTPException(
            status_code=404, detail="not a member of this classroom"
        ) from exc


# ----------------------------------------------------------------------
# 管理员
# ----------------------------------------------------------------------
@admin_router.get("/users", response_model=list[UserResponse])
def list_users(
    limit: int = 50,
    offset: int = 0,
    _admin: User = Depends(require_admin),
    service: AccountService = Depends(get_account_service),
) -> list[UserResponse]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return [
        UserResponse(**item.to_dict())
        for item in service.list_users(limit=limit, offset=offset)
    ]


@admin_router.put("/users/{user_id}/role", response_model=UserResponse)
def set_user_role(
    user_id: str,
    request: SetUserRoleRequest,
    _admin: User = Depends(require_admin),
    service: AccountService = Depends(get_account_service),
) -> UserResponse:
    """授予或收回角色。这是 admin 唯一的产生途径。"""
    try:
        updated = service.set_role(user_id, request.role)
    except SystemAccountProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UserNotFound as exc:
        raise HTTPException(status_code=404, detail="account not found") from exc
    except AccountValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return UserResponse(**updated.to_dict())
