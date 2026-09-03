"""作业接口：老师布置与批改，学生查看与提交。

与班级接口一样，这一组**始终**要求登录。老师侧与学生侧走两条独立的路径，
请求体也是两套模型：学生的提交体里根本没有评分字段，改不了自己的分数。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.classroom_api import get_assignment_service
from backend.dependencies import get_current_user, require_teacher
from backend.models import (
    CreateAssignmentRequest,
    GradeSubmissionRequest,
    SubmitAssignmentRequest,
    UpdateAssignmentRequest,
)
from services.account_service import User
from services.assignment_service import (
    AssignmentNotFound,
    AssignmentService,
    SubmissionNotFound,
    ValidationError,
)
from services.classroom_service import ClassroomNotFound


assignment_router = APIRouter(tags=["assignments"])


def _assignment_not_found() -> HTTPException:
    # 不存在、未发布、不是你班上的 —— 对调用方一律是 404。
    return HTTPException(status_code=404, detail="assignment not found")


# ----------------------------------------------------------------------
# 老师：布置与维护
# ----------------------------------------------------------------------
@assignment_router.post(
    "/classrooms/{classroom_id}/assignments", status_code=status.HTTP_201_CREATED
)
def create_assignment(
    classroom_id: str,
    request: CreateAssignmentRequest,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    try:
        assignment = service.create_assignment(
            classroom_id,
            teacher.id,
            is_admin=teacher.is_admin,
            title=request.title,
            instructions=request.instructions,
            target_kind=request.target_kind,
            target_topic=request.target_topic,
            target_course_id=request.target_course_id,
            target_node=request.target_node,
            due_at=request.due_at,
            max_score=request.max_score,
            is_published=request.is_published,
        )
    except ClassroomNotFound as exc:
        raise HTTPException(status_code=404, detail="classroom not found") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return assignment.to_dict(include_teacher_stats=True)


@assignment_router.get("/classrooms/{classroom_id}/assignments")
def list_classroom_assignments(
    classroom_id: str,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    """老师视角：含未发布的草稿与提交统计。"""
    try:
        items = service.list_for_teacher(
            classroom_id, teacher.id, is_admin=teacher.is_admin
        )
    except ClassroomNotFound as exc:
        raise HTTPException(status_code=404, detail="classroom not found") from exc
    return {
        "assignments": [item.to_dict(include_teacher_stats=True) for item in items]
    }


@assignment_router.patch("/assignments/{assignment_id}")
def update_assignment(
    assignment_id: str,
    request: UpdateAssignmentRequest,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    try:
        assignment = service.update_assignment(
            assignment_id,
            teacher.id,
            is_admin=teacher.is_admin,
            **request.model_dump(),
        )
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return assignment.to_dict(include_teacher_stats=True)


@assignment_router.delete(
    "/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_assignment(
    assignment_id: str,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> None:
    try:
        service.delete_assignment(assignment_id, teacher.id, is_admin=teacher.is_admin)
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc


@assignment_router.get("/assignments/{assignment_id}/submissions")
def list_submissions(
    assignment_id: str,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    """全班提交清单，只有本班老师看得到。"""
    try:
        items = service.list_submissions(
            assignment_id, teacher.id, is_admin=teacher.is_admin
        )
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc
    return {"submissions": [item.to_dict(include_student=True) for item in items]}


@assignment_router.put("/assignments/{assignment_id}/submissions/{student_id}/grade")
def grade_submission(
    assignment_id: str,
    student_id: str,
    request: GradeSubmissionRequest,
    teacher: User = Depends(require_teacher),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    try:
        submission = service.grade(
            assignment_id,
            student_id,
            teacher.id,
            is_admin=teacher.is_admin,
            score=request.score,
            feedback=request.feedback,
        )
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc
    except SubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="submission not found") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return submission.to_dict(include_student=True)


# ----------------------------------------------------------------------
# 学生：查看与提交
# ----------------------------------------------------------------------
@assignment_router.get("/me/assignments")
def list_my_assignments(
    user: User = Depends(get_current_user),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    """自己所有在读班级里已发布的作业，附带自己的提交状态。"""
    return {"assignments": service.list_for_student(user.id)}


@assignment_router.get("/me/assignments/{assignment_id}")
def get_my_assignment(
    assignment_id: str,
    user: User = Depends(get_current_user),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    try:
        return service.get_for_student(assignment_id, user.id)
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc


@assignment_router.put("/me/assignments/{assignment_id}/submission")
def submit_assignment(
    assignment_id: str,
    request: SubmitAssignmentRequest,
    user: User = Depends(get_current_user),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    """提交或重交。重交会清空既有分数——那份评分针对的是旧答案。"""
    try:
        submission = service.submit(
            assignment_id,
            user.id,
            content=request.content,
            session_id=request.session_id,
        )
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return submission.to_dict()


@assignment_router.get("/me/assignments/{assignment_id}/submission")
def get_my_submission(
    assignment_id: str,
    user: User = Depends(get_current_user),
    service: AssignmentService = Depends(get_assignment_service),
) -> dict:
    try:
        return service.get_my_submission(assignment_id, user.id).to_dict()
    except AssignmentNotFound as exc:
        raise _assignment_not_found() from exc
    except SubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="submission not found") from exc
