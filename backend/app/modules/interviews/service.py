"""C 人工面试域服务（契约 §4）。

- 管理员手动新增/改期/取消轮次，变更写 `interview_round_events`。
- 反馈只在轮次 `completed` 后保存，每轮一条，**不计算面试总分**。
- 可见范围：管理员全部；面试人仅被安排的轮次与内部反馈；候选人仅本人安排，不含内部备注与反馈。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import User
from app.modules.assessments.service import is_admin, require_admin
from app.modules.interviews.models import InterviewFeedback, InterviewRound, InterviewRoundEvent
from app.modules.interviews.schemas import FeedbackSaveRequest, RoundCreateRequest, RoundUpdateRequest


def _is_interviewer(round_row: InterviewRound, user: User) -> bool:
    return user.id in {int(item) for item in (round_row.interviewer_user_ids_json or [])}


def can_view_internal(round_row: InterviewRound, user: User) -> bool:
    return is_admin(user) or _is_interviewer(round_row, user)


def _require_visible(round_row: InterviewRound, user: User) -> None:
    if can_view_internal(round_row, user):
        return
    if round_row.candidate_user_id == user.id:
        return
    raise BizError(403, "无权查看该面试轮次")


def _event(
    db: Session,
    round_row: InterviewRound,
    event_type: str,
    *,
    operator_id: int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    db.add(
        InterviewRoundEvent(
            round_id=round_row.id,
            event_type=event_type,
            before_json=before,
            after_json=after,
            operator_id=operator_id,
            note=note,
        )
    )


def create_round(db: Session, payload: RoundCreateRequest, *, admin: User) -> InterviewRound:
    require_admin(admin)
    if payload.candidate_user_id is None and payload.talent_profile_id is None:
        raise BizError(400, "新增面试轮次至少需要关联候选人账号或人才主档")
    row = InterviewRound(
        candidate_user_id=payload.candidate_user_id,
        talent_profile_id=payload.talent_profile_id,
        job_id=payload.job_id,
        job_title=(payload.job_title or "").strip() or None,
        round_no=payload.round_no,
        round_name=payload.round_name.strip(),
        scheduled_at=payload.scheduled_at,
        mode=payload.mode,
        location_or_link=(payload.location_or_link or "").strip() or None,
        interviewer_user_ids_json=sorted({int(item) for item in payload.interviewer_user_ids}),
        status="pending",
        internal_note=payload.internal_note,
        version=1,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        row,
        "created",
        operator_id=admin.id,
        after={
            "round_no": row.round_no,
            "round_name": row.round_name,
            "scheduled_at": str(row.scheduled_at) if row.scheduled_at else None,
            "status": row.status,
        },
    )
    db.commit()
    db.refresh(row)
    return row


def get_round(db: Session, round_id: int) -> InterviewRound:
    row = db.get(InterviewRound, round_id)
    if row is None:
        raise BizError(404, "面试轮次不存在")
    return row


def get_round_for_user(db: Session, round_id: int, user: User) -> InterviewRound:
    row = get_round(db, round_id)
    _require_visible(row, user)
    return row


def update_round(
    db: Session, round_id: int, payload: RoundUpdateRequest, *, admin: User
) -> InterviewRound:
    require_admin(admin)
    row = get_round(db, round_id)
    if row.status == "cancelled":
        raise BizError(400, "已取消的面试轮次不能修改")
    before = {
        "round_name": row.round_name,
        "scheduled_at": str(row.scheduled_at) if row.scheduled_at else None,
        "mode": row.mode,
        "location_or_link": row.location_or_link,
        "interviewer_user_ids": list(row.interviewer_user_ids_json or []),
        "status": row.status,
    }
    fields = payload.model_dump(exclude_unset=True)
    rescheduled = False
    for key, value in fields.items():
        if key == "interviewer_user_ids":
            setattr(row, "interviewer_user_ids_json", sorted({int(item) for item in value or []}))
        elif key == "round_name":
            row.round_name = str(value).strip()
        elif key == "location_or_link":
            row.location_or_link = (value or "").strip() or None
        else:
            setattr(row, key, value)
        if key in {"scheduled_at", "mode", "location_or_link"}:
            rescheduled = True
    if payload.status == "completed" and row.status != "completed":
        row.status = "completed"
    row.version += 1
    row.updated_by = admin.id
    after = {
        "round_name": row.round_name,
        "scheduled_at": str(row.scheduled_at) if row.scheduled_at else None,
        "mode": row.mode,
        "location_or_link": row.location_or_link,
        "interviewer_user_ids": list(row.interviewer_user_ids_json or []),
        "status": row.status,
    }
    _event(
        db,
        row,
        "completed" if after["status"] == "completed" and before["status"] != "completed" else ("rescheduled" if rescheduled else "updated"),
        operator_id=admin.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(row)
    return row


def cancel_round(db: Session, round_id: int, *, admin: User, note: str | None = None) -> InterviewRound:
    require_admin(admin)
    row = get_round(db, round_id)
    if row.status == "cancelled":
        return row
    before = {"status": row.status}
    row.status = "cancelled"
    row.version += 1
    row.updated_by = admin.id
    _event(db, row, "cancelled", operator_id=admin.id, before=before, after={"status": "cancelled"}, note=note)
    db.commit()
    db.refresh(row)
    return row


def list_rounds(
    db: Session,
    *,
    status: str | None = None,
    candidate_user_id: int | None = None,
    job_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[InterviewRound], int]:
    stmt = select(InterviewRound)
    if status:
        stmt = stmt.where(InterviewRound.status == status)
    if candidate_user_id is not None:
        stmt = stmt.where(InterviewRound.candidate_user_id == candidate_user_id)
    if job_id is not None:
        stmt = stmt.where(InterviewRound.job_id == job_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(InterviewRound.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def my_rounds(db: Session, user: User) -> list[tuple[InterviewRound, str]]:
    """返回 (轮次, 视角)：候选人视角 `candidate`，面试人视角 `interviewer`。"""
    rows = list(
        db.scalars(select(InterviewRound).order_by(InterviewRound.id.desc()))
    )
    result: list[tuple[InterviewRound, str]] = []
    for row in rows:
        if row.candidate_user_id == user.id:
            result.append((row, "candidate"))
        elif _is_interviewer(row, user):
            result.append((row, "interviewer"))
    return result


def save_feedback(
    db: Session, round_id: int, payload: FeedbackSaveRequest, *, user: User
) -> InterviewFeedback:
    row = get_round(db, round_id)
    if not can_view_internal(row, user):
        raise BizError(403, "仅管理员或获授权面试人可填写反馈")
    if row.status != "completed":
        raise BizError(400, "面试尚未完成，不能填写反馈")
    feedback = db.scalar(select(InterviewFeedback).where(InterviewFeedback.round_id == round_id))
    if feedback is None:
        feedback = InterviewFeedback(
            round_id=round_id,
            interviewer_user_id=user.id,
            evaluation=payload.evaluation,
            evidence=payload.evidence,
            strengths=payload.strengths,
            weaknesses=payload.weaknesses,
            recommendation=payload.recommendation,
            version=1,
        )
        db.add(feedback)
    else:
        feedback.interviewer_user_id = user.id
        feedback.evaluation = payload.evaluation
        feedback.evidence = payload.evidence
        feedback.strengths = payload.strengths
        feedback.weaknesses = payload.weaknesses
        feedback.recommendation = payload.recommendation
        feedback.version += 1
    db.flush()
    _event(
        db,
        row,
        "feedback_saved",
        operator_id=user.id,
        after={"round_id": round_id, "recommendation": payload.recommendation},
    )
    db.commit()
    db.refresh(feedback)
    return feedback


def get_feedback(db: Session, round_id: int, *, user: User) -> InterviewFeedback:
    row = get_round(db, round_id)
    if not can_view_internal(row, user):
        raise BizError(403, "面试反馈仅管理员及获授权面试人可见")
    feedback = db.scalar(select(InterviewFeedback).where(InterviewFeedback.round_id == round_id))
    if feedback is None:
        raise BizError(404, "该轮尚未填写反馈")
    return feedback


def list_events(db: Session, round_id: int, user: User) -> list[InterviewRoundEvent]:
    row = get_round(db, round_id)
    if not can_view_internal(row, user):
        raise BizError(403, "面试变更记录仅管理员及获授权面试人可见")
    return list(
        db.scalars(
            select(InterviewRoundEvent)
            .where(InterviewRoundEvent.round_id == round_id)
            .order_by(InterviewRoundEvent.id.asc())
        )
    )
