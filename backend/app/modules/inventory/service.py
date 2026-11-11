"""F10 人才盘点服务：复核、人才池、异议、发展计划、汇总。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.inventory.models import (
    InventoryDevelopmentPlan,
    InventoryDispute,
    InventoryEvent,
    InventoryGridConfig,
    InventoryReview,
    InventoryTalentPool,
)
from app.modules.inventory.scoring import grid_cell, public_safe_review, visible_summary

ADMIN_ROLES = {"admin", "hr"}  # 历史行级「自/他 + 管理」判定保留；纯管理门改走 A06 角色门禁


def _require_admin(user) -> None:
    """管理侧门禁（A06 统一口径：system_admin/hr）；替代原 `user.role in ADMIN_ROLES`。

    修复 system_admin 因 users.role 映射被误拒；dept_manager/trainer/employee/
    candidate/personal 一律非管理侧。行级「本人 or 管理」判定仍在各调用点保留。
    """
    from app.core.auth.scope import require_management_guard  # noqa: PLC0415 - 避免环

    require_management_guard(user)


def _event(db: Session, *, review_id: int | None, event_type: str, operator_id: int | None, note: str | None = None, payload: dict | None = None) -> None:
    db.add(InventoryEvent(review_id=review_id, event_type=event_type, operator_id=operator_id, note=note, payload_json=payload or {}))


def _active_grid_config(db: Session) -> InventoryGridConfig | None:
    """当前生效的九宫格标准 = 版本最大的 approved 配置；无批准配置返回 None。"""
    return db.scalar(
        select(InventoryGridConfig)
        .where(InventoryGridConfig.status == "approved")
        .order_by(InventoryGridConfig.version.desc(), InventoryGridConfig.id.desc())
    )


def _config_bands(cfg: InventoryGridConfig) -> dict[str, dict[str, int]]:
    return {
        "performance": dict(cfg.performance_bands_json or {}),
        "potential": dict(cfg.potential_bands_json or {}),
    }


def _provisional_grid(db: Session, review: InventoryReview) -> str | None:
    """草稿/提交阶段的暂定九宫格：缺经批准配置或绩效/潜力不全时返回 None（待评估）。"""
    cfg = _active_grid_config(db)
    if cfg is None or not review.performance_score or not review.potential_hr_score:
        return None
    return grid_cell(review.performance_score, review.potential_hr_score, _config_bands(cfg))



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
    review.grid_cell = _provisional_grid(db, review)
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
    review.grid_cell = _provisional_grid(db, review)
    _event(db, review_id=review.id, event_type="updated", operator_id=user.id, payload={"fields": list(data)})
    return review


def submit_review(db: Session, review_id: int, user) -> InventoryReview:
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if review.status == "confirmed":
        raise BizError(400, "已确认复核不可再提交，请新建复核")
    if not review.performance_score:
        raise BizError(400, "提交前需填齐绩效分")
    if not review.potential_manager_score:
        raise BizError(400, "提交前需完成经理初评")
    if not review.potential_hr_score:
        raise BizError(400, "提交前需填齐 HR 潜力分")
    review.grid_cell = _provisional_grid(db, review)
    review.status = "submitted"
    _event(db, review_id=review.id, event_type="submitted", operator_id=user.id)
    return review


def confirm_review(db: Session, review_id: int, user) -> InventoryReview:
    """HR 批准确认：强制「已提交 + 完整绩效 + 经理初评 + HR 潜力 + 经批准标准配置」状态链。

    九宫格按当前经 HR 批准的配置版本重算，并把所依据的 `grid_config_version` 落到
    复核记录；无经批准配置时拒绝产出正式评级（F05a：未批准不套示意数值）。缺绩效
    时不得入格（保持待评估，确认被拒）。确认人记入 reviewer_hr_id，已确认不可重复
    确认，历史评级不随后续配置改动重算。
    """
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if review.status == "confirmed":
        raise BizError(400, "复核已确认，不可重复确认")
    if review.status != "submitted":
        raise BizError(400, "仅已提交的复核可确认，请先提交")
    if not review.performance_score:
        raise BizError(400, "确认前需填齐绩效分（缺绩效不得入格，保持待评估）")
    if not review.potential_manager_score:
        raise BizError(400, "确认前需完成经理初评")
    if not review.potential_hr_score:
        raise BizError(400, "确认前需填齐 HR 潜力分")
    cfg = _active_grid_config(db)
    if cfg is None:
        raise BizError(400, "缺少经 HR 批准的九宫格标准配置，不能产出正式盘点评级")
    review.grid_cell = grid_cell(review.performance_score, review.potential_hr_score, _config_bands(cfg))
    review.grid_config_version = cfg.version
    review.status = "confirmed"
    review.confirmed_at = datetime.now()
    review.reviewer_hr_id = user.id
    _event(
        db,
        review_id=review.id,
        event_type="confirmed",
        operator_id=user.id,
        payload={"grid_cell": review.grid_cell, "config_version": cfg.version},
    )
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


# ---------------------------------------------------------------- 九宫格标准配置版本（F05a）


def _validate_bands(bands: dict[str, Any], label: str) -> None:
    hi = bands.get("hi_min")
    mid = bands.get("mid_min")
    if not isinstance(hi, int) or not isinstance(mid, int):
        raise BizError(400, f"{label}阈值需为整数 hi_min/mid_min")
    if not (1 <= mid < hi <= 5):
        raise BizError(400, f"{label}阈值需满足 1 ≤ mid_min < hi_min ≤ 5")


def create_grid_config(db: Session, payload, user) -> InventoryGridConfig:
    """HR/管理新建九宫格标准草稿；阈值不在代码里硬编码，须显式给出。"""
    _require_admin(user)
    _validate_bands(payload.performance_bands, "绩效")
    _validate_bands(payload.potential_bands, "潜力")
    max_version = db.scalar(select(func.max(InventoryGridConfig.version))) or 0
    cfg = InventoryGridConfig(
        version=max_version + 1,
        status="draft",
        job_family=(payload.job_family or None),
        performance_bands_json=dict(payload.performance_bands),
        potential_bands_json=dict(payload.potential_bands),
        labels_json=dict(payload.labels or {}),
        note=payload.note,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(cfg)
    db.flush()
    _event(
        db,
        review_id=None,
        event_type="grid_config_created",
        operator_id=user.id,
        payload={"version": cfg.version, "job_family": cfg.job_family},
    )
    return cfg


def approve_grid_config(db: Session, config_id: int, user) -> InventoryGridConfig:
    """HR 批准某版本 → 成为当前生效标准；批准前须已满足阈值合法性。"""
    _require_admin(user)
    cfg = db.get(InventoryGridConfig, config_id)
    if cfg is None:
        raise BizError(404, "配置不存在")
    if cfg.status == "approved":
        raise BizError(400, "配置已批准，无需重复批准")
    _validate_bands(cfg.performance_bands_json, "绩效")
    _validate_bands(cfg.potential_bands_json, "潜力")
    cfg.status = "approved"
    cfg.approved_by = user.id
    cfg.approved_at = datetime.now()
    cfg.updated_by = user.id
    _event(
        db,
        review_id=None,
        event_type="grid_config_approved",
        operator_id=user.id,
        payload={"version": cfg.version},
    )
    return cfg


def list_grid_configs(db: Session, user) -> list[dict[str, Any]]:
    _require_admin(user)
    rows = db.scalars(
        select(InventoryGridConfig).order_by(InventoryGridConfig.version.desc())
    ).all()
    return [
        {
            "id": c.id,
            "version": c.version,
            "status": c.status,
            "job_family": c.job_family,
            "performance_bands": c.performance_bands_json,
            "potential_bands": c.potential_bands_json,
            "labels": c.labels_json,
            "note": c.note,
            "approved_by": c.approved_by,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        }
        for c in rows
    ]

