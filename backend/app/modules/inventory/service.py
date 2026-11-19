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
from app.modules.training import authz

def _viewer_role(db: Session, user) -> str:
    """字段级可见性等价角色：管理侧/经理按内部可见，员工按公开视图（F10 矩阵）。"""
    tier = authz.viewer_tier(db, user)
    if tier == authz.MANAGEMENT:
        return "hr"
    if tier == authz.MANAGER:
        return "dept_manager"
    return "employee"


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
    """发起盘点复核：管理侧或负责部门经理（F10 §4）；员工不可发起。"""
    tier = authz.viewer_tier(db, user)
    if tier == authz.EMPLOYEE:
        raise BizError(403, "仅管理侧或部门经理可发起盘点复核")
    authz.require_employee_in_scope(db, user, payload.employee_user_id)
    review = InventoryReview(
        employee_user_id=payload.employee_user_id,
        talent_profile_id=payload.talent_profile_id,
        cycle_name=payload.cycle_name,
        review_type=payload.review_type,
        performance_score=payload.performance_score,
        performance_source=payload.performance_source,
        potential_manager_score=payload.potential_manager_score,
        potential_hr_score=payload.potential_hr_score,
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
    authz.require_employee_in_scope(db, user, review.employee_user_id)
    if review.status == "confirmed":
        raise BizError(400, "已确认复核不可修改，请新建复核")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(review, field, value)
    review.grid_cell = _provisional_grid(db, review)
    _event(db, review_id=review.id, event_type="updated", operator_id=user.id, payload={"fields": list(data)})
    return review


def manager_initial_review(db: Session, review_id: int, payload, user) -> InventoryReview:
    """部门经理本人提交潜力初评（REQ-INV-002）：仅草稿阶段、仅负责部门员工。

    `reviewer_manager_id` 强制记为初评经理本人，管理侧代填不能替代经理本人初评；
    已提交/已确认的复核拒绝修改，须走新版本复核。
    """
    if authz.viewer_tier(db, user) != authz.MANAGER:
        raise BizError(403, "仅部门经理可提交经理本人初评")
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    authz.require_employee_in_scope(db, user, review.employee_user_id)
    if review.status != "draft":
        raise BizError(400, "仅草稿阶段可提交经理初评，已提交/确认的复核不可修改")
    review.potential_manager_score = payload.potential_manager_score
    review.reviewer_manager_id = user.id
    review.manager_comment = payload.comment.strip() if payload.comment else None
    review.grid_cell = _provisional_grid(db, review)
    _event(
        db,
        review_id=review.id,
        event_type="manager_initial_reviewed",
        operator_id=user.id,
        payload={"potential_manager_score": payload.potential_manager_score},
    )
    return review


def submit_review(db: Session, review_id: int, user) -> InventoryReview:
    _require_admin(user)
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    authz.require_employee_in_scope(db, user, review.employee_user_id)
    if review.status == "confirmed":
        raise BizError(400, "已确认复核不可再提交，请新建复核")
    if not review.performance_score:
        raise BizError(400, "提交前需填齐绩效分")
    if not review.potential_manager_score or not review.reviewer_manager_id:
        raise BizError(400, "提交前需完成经理本人初评（经理通过初评入口提交，管理侧代填不计）")
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
    authz.require_employee_in_scope(db, user, review.employee_user_id)
    if review.status == "confirmed":
        raise BizError(400, "复核已确认，不可重复确认")
    if review.status != "submitted":
        raise BizError(400, "仅已提交的复核可确认，请先提交")
    if not review.performance_score:
        raise BizError(400, "确认前需填齐绩效分（缺绩效不得入格，保持待评估）")
    if not review.potential_manager_score or not review.reviewer_manager_id:
        raise BizError(400, "确认前需完成经理本人初评")
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
    tier = authz.viewer_tier(db, user)
    stmt = select(InventoryReview).order_by(InventoryReview.id.desc())
    if tier == authz.EMPLOYEE:
        stmt = stmt.where(InventoryReview.employee_user_id == user.id)
    elif employee_user_id is not None:
        authz.require_employee_in_scope(db, user, employee_user_id)
        stmt = stmt.where(InventoryReview.employee_user_id == employee_user_id)
    else:
        scoped = authz.scoped_employee_ids(db, user)
        if scoped is not None:
            stmt = stmt.where(InventoryReview.employee_user_id.in_(scoped))
    rows = db.execute(stmt).scalars().all()
    viewer = _viewer_role(db, user)
    return [public_safe_review(_review_dict(r), viewer_role=viewer) for r in rows]


def _review_dict(r: InventoryReview) -> dict[str, Any]:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}


def get_review(db: Session, review_id: int, user) -> dict[str, Any]:
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    tier = authz.viewer_tier(db, user)
    if tier == authz.EMPLOYEE:
        if review.employee_user_id != user.id:
            raise BizError(403, "无权查看他人复核")
    else:
        authz.require_employee_in_scope(db, user, review.employee_user_id)
    return public_safe_review(_review_dict(review), viewer_role=_viewer_role(db, user))


def nominate_pool(db: Session, payload, user) -> InventoryTalentPool:
    _require_admin(user)
    authz.require_employee_in_scope(db, user, payload.employee_user_id)
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
    authz.require_employee_in_scope(db, user, pool.employee_user_id)
    pool.status = payload.status
    pool.decided_by = user.id
    pool.decided_at = datetime.now()
    if payload.note is not None:
        pool.note = payload.note
    return pool


def list_pools(db: Session, user) -> list[InventoryTalentPool]:
    _require_admin(user)
    stmt = select(InventoryTalentPool).order_by(InventoryTalentPool.id.desc())
    scoped = authz.scoped_employee_ids(db, user)
    if scoped is not None:
        stmt = stmt.where(InventoryTalentPool.employee_user_id.in_(scoped))
    return db.execute(stmt).scalars().all()


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
    authz.require_employee_in_scope(db, user, dispute.employee_user_id)
    dispute.status = payload.status
    dispute.response = payload.response
    dispute.responded_by = user.id
    return dispute


def upsert_idp(db: Session, review_id: int, payload, user) -> InventoryDevelopmentPlan:
    review = db.get(InventoryReview, review_id)
    if not review:
        raise BizError(404, "复核不存在")
    if review.employee_user_id != user.id:
        _require_admin(user)
        authz.require_employee_in_scope(db, user, review.employee_user_id)
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
    """匿名汇总：管理侧全量，经理仅负责部门（F10）；员工拒绝。样本 <5 隐藏。"""
    tier = authz.viewer_tier(db, user)
    if tier == authz.EMPLOYEE:
        raise BizError(403, "仅管理员可查看汇总")
    stmt = select(InventoryReview).where(InventoryReview.status == "confirmed")
    scoped = authz.scoped_employee_ids(db, user)
    if scoped is not None:
        stmt = stmt.where(InventoryReview.employee_user_id.in_(scoped))
    rows = db.execute(stmt).scalars().all()
    return visible_summary([_review_dict(r) for r in rows], viewer_role=_viewer_role(db, user))


def export_summary(db: Session, *, user, cycle_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """导出匿名汇总 CSV 并写审计事件（F10：导出审计）。

    复用 `visible_summary` 的门槛裁剪：<5 的档位只给合并计数，导出文件同样不含个人身份。
    """
    _require_admin(user)
    stmt = select(InventoryReview).where(InventoryReview.status == "confirmed")
    scoped = authz.scoped_employee_ids(db, user)
    if scoped is not None:
        stmt = stmt.where(InventoryReview.employee_user_id.in_(scoped))
    if cycle_name:
        stmt = stmt.where(InventoryReview.cycle_name == cycle_name)
    rows = db.execute(stmt).scalars().all()
    summary = visible_summary([_review_dict(r) for r in rows], viewer_role=_viewer_role(db, user))
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

