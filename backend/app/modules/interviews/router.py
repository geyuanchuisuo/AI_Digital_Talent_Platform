"""C 人工面试域路由（prefix `/api/interviews`）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.response import ok, paginated
from app.database import get_db
from app.modules.interviews import service
from app.modules.interviews.models import InterviewFeedback, InterviewRound
from app.modules.interviews.schemas import FeedbackSaveRequest, RoundCreateRequest, RoundUpdateRequest

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


def _round_out(row: InterviewRound, user) -> dict[str, Any]:
    internal = service.can_view_internal(row, user)
    data: dict[str, Any] = {
        "id": row.id,
        "candidate_user_id": row.candidate_user_id,
        "talent_profile_id": row.talent_profile_id,
        "job_id": row.job_id,
        "job_title": row.job_title,
        "round_no": row.round_no,
        "round_name": row.round_name,
        "scheduled_at": row.scheduled_at,
        "mode": row.mode,
        "location_or_link": row.location_or_link,
        "interviewer_user_ids": list(row.interviewer_user_ids_json or []),
        "status": row.status,
        "client_request_id": row.client_request_id,
        "version": row.version,
        "created_at": row.created_at,
    }
    if internal:
        data["internal_note"] = row.internal_note
    return data


def _feedback_out(row: InterviewFeedback) -> dict[str, Any]:
    return {
        "round_id": row.round_id,
        "interviewer_user_id": row.interviewer_user_id,
        "evaluation": row.evaluation,
        "evidence": row.evidence,
        "strengths": row.strengths,
        "weaknesses": row.weaknesses,
        "recommendation": row.recommendation,
        "version": row.version,
        "updated_at": row.updated_at,
    }


@router.post("/rounds", summary="新增面试轮次（管理员）")
def create_round(
    payload: RoundCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.create_round(db, payload, admin=user)
    return ok(_round_out(row, user))


@router.get("/rounds", summary="面试轮次列表（管理员）")
def list_rounds(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    status: str | None = Query(None),
    candidate_user_id: int | None = Query(None),
    job_id: int | None = Query(None),
):
    service.require_admin(user)
    rows, total = service.list_rounds(
        db,
        status=status,
        candidate_user_id=candidate_user_id,
        job_id=job_id,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated([_round_out(row, user) for row in rows], total, page.page, page.page_size)


@router.get("/my/rounds", summary="我的面试安排（候选人/获授权面试人）")
def my_rounds(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.my_rounds(db, user)
    return ok(
        {
            "items": [{**_round_out(row, user), "view": view} for row, view in rows],
            "total": len(rows),
        }
    )


@router.get("/rounds/{round_id}", summary="面试轮次详情（按角色裁剪）")
def get_round(
    round_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.get_round_for_user(db, round_id, user)
    data: dict[str, Any] = _round_out(row, user)
    if service.can_view_internal(row, user):
        data["events"] = [
            {
                "event_type": event.event_type,
                "before": dict(event.before_json or {}) if event.before_json else None,
                "after": dict(event.after_json or {}) if event.after_json else None,
                "operator_id": event.operator_id,
                "note": event.note,
                "created_at": event.created_at,
            }
            for event in service.list_events(db, round_id, user)
        ]
        feedback = db.query(InterviewFeedback).filter(InterviewFeedback.round_id == round_id).first()
        data["feedback"] = _feedback_out(feedback) if feedback else None
    return ok(data)


@router.patch("/rounds/{round_id}", summary="修改/改期/标记完成（管理员）")
def update_round(
    round_id: int,
    payload: RoundUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.update_round(db, round_id, payload, admin=user)
    return ok(_round_out(row, user))


@router.post("/rounds/{round_id}/cancel", summary="取消面试轮次（管理员）")
def cancel_round(
    round_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    note: str | None = Query(None, max_length=200),
):
    row = service.cancel_round(db, round_id, admin=user, note=note)
    return ok(_round_out(row, user))


@router.put("/rounds/{round_id}/feedback", summary="填写结构化反馈（管理员/获授权面试人）")
def save_feedback(
    round_id: int,
    payload: FeedbackSaveRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    feedback = service.save_feedback(db, round_id, payload, user=user)
    return ok(_feedback_out(feedback))


@router.get("/rounds/{round_id}/feedback", summary="查看结构化反馈（内部授权）")
def get_feedback(
    round_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    feedback = service.get_feedback(db, round_id, user=user)
    return ok(_feedback_out(feedback))
