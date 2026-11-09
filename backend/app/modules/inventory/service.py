"""F10 人才盘点服务：复核、人才池、异议、发展计划、汇总。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.inventory.models import (
    InventoryDevelopmentPlan,
    InventoryDispute,
    InventoryEvent,
    InventoryReview,
    InventoryTalentPool,
)
from app.modules.inventory.scoring import grid_cell, public_safe_review, visible_summary

ADMIN_ROLES = {"admin", "hr"}


def _require_admin(user) -> None:
    if user.role not in ADMIN_ROLES:
        raise BizError(403, "仅管理员可执行该操作")


def _event(db: Session, *, review_id: int | None, event_type: str, operator_id: int | None, note: str | None = None, payload: dict | None = None) -> None:
    db.add(InventoryEvent(review_id=review_id, event_type=event_type, operator_id=operator_id, note=note, payload_json=payload or {}))


def create_review(db: Session, payload, user) -> InventoryReview:
    _require_admin(user)
    review = InventoryReview(
        employee_user_id=payload.employee_user_id,
        talent_profile_id=payload.talent_profile_id,
        cycle_name=payload.cycle_name,
        review_type=payload.review_type,
        performance_score=payload.performance_score,
        performance_source=payload.performance_source,
        potential_manager_score=payload.potential_manager_score,
        potential_hr_score=payload.potential_hr_score,
        reviewer_manager_id=payload.reviewer_manager_id,
        reviewer_hr_id=payload.reviewer_hr_id,
        summary=payload.summary,
    )
    if review.performance_score and review.potential_hr_score:
        review.grid_cell = grid_cell(review.performance_score, review.potential_hr_score)
    db.add(review)
    db.flush()
    _event(db, review_id=review.id, event_type="created", operator_id=user.id, payload={"cycle": payload.cycle_name})
    return review


def update_review(db: Session, review_id: int, payload, user) -> InventoryReview:
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if review.status == "confirmed":
        raise BizError(400, "已确认复核不可修改，请新建复核")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(review, field, value)
    if review.performance_score and review.potential_hr_score:
        review.grid_cell = grid_cell(review.performance_score, review.potential_hr_score)
    _event(db, review_id=review.id, event_type="updated", operator_id=user.id, payload={"fields": list(data)})
    return review


def submit_review(db: Session, review_id: int, user) -> InventoryReview:
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if not (review.performance_score and review.potential_hr_score):
        raise BizError(400, "提交前需填齐绩效分与 HR 潜力分")
    review.grid_cell = grid_cell(review.performance_score, review.potential_hr_score)
    review.status = "submitted"
    _event(db, review_id=review.id, event_type="submitted", operator_id=user.id)
    return review


def confirm_review(db: Session, review_id: int, user) -> InventoryReview:
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    review.status = "confirmed"
    review.confirmed_at = datetime.now()
    _event(db, review_id=review.id, event_type="confirmed", operator_id=user.id)
    return review


def list_reviews(db: Session, user, *, employee_user_id: int | None = None) -> list[dict[str, Any]]:
    stmt = select(InventoryReview).order_by(InventoryReview.id.desc())
    if user.role not in ADMIN_ROLES:
        stmt = stmt.where(InventoryReview.employee_user_id == user.id)
    elif employee_user_id is not None:
        stmt = stmt.where(InventoryReview.employee_user_id == employee_user_id)
    rows = db.execute(stmt).scalars().all()
    return [public_safe_review(_review_dict(r), viewer_role=user.role) for r in rows]


def _review_dict(r: InventoryReview) -> dict[str, Any]:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}


def get_review(db: Session, review_id: int, user) -> dict[str, Any]:
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if user.role not in ADMIN_ROLES and review.employee_user_id != user.id:
        raise BizError(403, "无权查看他人复核")
    return public_safe_review(_review_dict(review), viewer_role=user.role)


def nominate_pool(db: Session, payload, user) -> InventoryTalentPool:
    _require_admin(user)
    pool = InventoryTalentPool(
        employee_user_id=payload.employee_user_id,
        pool_type=payload.pool_type,
        note=payload.note,
        nominated_by=user.id,
    )
    db.add(pool)
    db.flush()
    _event(db, review_id=None, event_type="pool_nominated", operator_id=user.id, payload={"pool_type": payload.pool_type})
    return pool


def decide_pool(db: Session, pool_id: int, payload, user) -> InventoryTalentPool:
    _require_admin(user)
    pool = db.get(InventoryTalentPool, pool_id)
    if not pool:
        raise BizError(404, "人才池记录不存在")
    pool.status = payload.status
    pool.decided_by = user.id
    pool.decided_at = datetime.now()
    if payload.note is not None:
        pool.note = payload.note
    return pool


def list_pools(db: Session, user) -> list[InventoryTalentPool]:
    if user.role in ADMIN_ROLES:
        return db.execute(select(InventoryTalentPool).order_by(InventoryTalentPool.id.desc())).scalars().all()
    raise BizError(403, "仅管理员可查看人才池")


def create_dispute(db: Session, review_id: int, payload, user) -> InventoryDispute:
    review = db.get(InventoryReview, review_id)
    if not review or review.employee_user_id != user.id:
        raise BizError(403, "只能对本人复核提出异议")
    dispute = InventoryDispute(review_id=review_id, employee_user_id=user.id, content=payload.content)
    db.add(dispute)
    return dispute


def respond_dispute(db: Session, dispute_id: int, payload, user) -> InventoryDispute:
    _require_admin(user)
    dispute = db.get(InventoryDispute, dispute_id)
    if not dispute:
        raise BizError(404, "异议不存在")
    dispute.status = payload.status
    dispute.response = payload.response
    dispute.responded_by = user.id
    return dispute


def upsert_idp(db: Session, review_id: int, payload, user) -> InventoryDevelopmentPlan:
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if user.role not in ADMIN_ROLES and review.employee_user_id != user.id:
        raise BizError(403, "无权操作他人发展计划")
    existing = db.execute(
        select(InventoryDevelopmentPlan).where(InventoryDevelopmentPlan.review_id == review_id)
    ).scalar_one_or_none()
    if existing:
        existing.content_json = payload.content_json
        existing.status = payload.status
        return existing
    plan = InventoryDevelopmentPlan(
        review_id=review_id, employee_user_id=review.employee_user_id,
        content_json=payload.content_json, status=payload.status,
    )
    db.add(plan)
    return plan


def department_summary(db: Session, user) -> dict[str, Any]:
    rows = db.execute(
        select(InventoryReview).where(InventoryReview.status == "confirmed")
    ).scalars().all()
    return visible_summary([_review_dict(r) for r in rows], viewer_role=user.role)


def export_summary(db: Session, *, user, cycle_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """导出匿名汇总 CSV 并写审计事件（F10：导出审计）。

    复用 `visible_summary` 的门槛裁剪：<5 的档位只给合并计数，导出文件同样不含个人身份。
    """
    _require_admin(user)
    stmt = select(InventoryReview).where(InventoryReview.status == "confirmed")
    if cycle_name:
        stmt = stmt.where(InventoryReview.cycle_name == cycle_name)
    rows = db.execute(stmt).scalars().all()
    summary = visible_summary([_review_dict(r) for r in rows], viewer_role=user.role)
    lines = ["grid_cell,label,count"]
    for cell, count in summary["cells"].items():
        label = summary["labels"].get(cell) or "低于门槛合并"
        lines.append(f"{cell},{label},{count}")
    lines.append(f"total,,{summary['total']}")
    text = "\n".join(lines) + "\n"
    _event(
        db,
        review_id=None,
        event_type="summary_exported",
        operator_id=user.id,
        note=cycle_name,
        payload={"cycle_name": cycle_name, "cells": summary["cells"], "total": summary["total"]},
    )
    db.commit()
    return text, summary
