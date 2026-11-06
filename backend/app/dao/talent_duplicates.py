"""`talent_duplicates` 数据访问（T-17）。

口径：`docs/数据库设计.md` v1.2 §5.3。

约定与其余 DAO 一致：**不 commit**，事务边界由 service 决定；不做业务判断。

两条不要动的口径：
- `parsed_resume_id` 有 UNIQUE（`uk_parsed_resume`）：一条解析结果只能有一条查重记录，
  重试再次命中时**更新原记录**（`upsert_pending`），绝不新增行——这是 T-13 幂等防护③。
- `profile_b_id` 在 `pending` / `merged` 场景恒为 NULL，只有 `keep` 同步建出画像后回填。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TalentDuplicate

STATUS_PENDING = "pending"
STATUS_MERGED = "merged"
STATUS_KEPT = "kept"


def get(db: Session, duplicate_id: int) -> TalentDuplicate | None:
    return db.get(TalentDuplicate, duplicate_id)


def get_by_parsed_resume(db: Session, parsed_resume_id: int) -> TalentDuplicate | None:
    return db.execute(
        select(TalentDuplicate).where(TalentDuplicate.parsed_resume_id == parsed_resume_id)
    ).scalar_one_or_none()


def create(db: Session, **fields: object) -> TalentDuplicate:
    row = TalentDuplicate(**fields)
    db.add(row)
    db.flush()
    return row


def upsert_pending(
    db: Session,
    *,
    parsed_resume_id: int,
    profile_a_id: int,
    reason: str,
    matched_value: str | None,
) -> tuple[TalentDuplicate, bool]:
    """按 `parsed_resume_id` upsert 一条 `pending` 记录；返回 `(row, created)`。

    已存在时**重置为 pending** 并刷新 `reason/matched_value/profile_a_id`——
    "上次已 merged，这次同一份简历重跑又命中"是合法场景（requeue 全流程重跑）。
    `profile_b_id` 一并清空：新的 pending 阶段没有第二个画像。
    """
    row = get_by_parsed_resume(db, parsed_resume_id)
    if row is None:
        row = create(
            db,
            parsed_resume_id=parsed_resume_id,
            profile_a_id=profile_a_id,
            profile_b_id=None,
            reason=reason,
            matched_value=matched_value,
            status=STATUS_PENDING,
        )
        return row, True

    row.profile_a_id = profile_a_id
    row.profile_b_id = None
    row.reason = reason
    row.matched_value = matched_value
    row.status = STATUS_PENDING
    row.resolution_note = None
    row.resolved_by = None
    row.resolved_at = None
    db.flush()
    return row, False


def list_page(
    db: Session, *, status: str | None, offset: int, limit: int
) -> tuple[list[TalentDuplicate], int]:
    """按状态分页（默认 pending），`created_at` 倒序。

    计数与列表用同一 `where`，避免"total 与 items 不一致"。
    """
    condition = [TalentDuplicate.status == status] if status else []
    total = int(
        db.execute(select(func.count(TalentDuplicate.id)).where(*condition)).scalar_one()
    )
    rows = list(
        db.execute(
            select(TalentDuplicate)
            .where(*condition)
            .order_by(TalentDuplicate.created_at.desc(), TalentDuplicate.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    return rows, total


def mark_resolved(
    db: Session,
    row: TalentDuplicate,
    *,
    status: str,
    user_id: int,
    at: datetime,
    note: str | None = None,
    profile_b_id: int | None = None,
) -> None:
    """收尾：写状态 + 处理人/时间/备注（keep 额外回填 `profile_b_id`）。"""
    row.status = status
    row.resolved_by = user_id
    row.resolved_at = at
    row.resolution_note = note
    if profile_b_id is not None:
        row.profile_b_id = profile_b_id
    db.flush()


__all__ = [
    "STATUS_KEPT",
    "STATUS_MERGED",
    "STATUS_PENDING",
    "create",
    "get",
    "get_by_parsed_resume",
    "list_page",
    "mark_resolved",
    "upsert_pending",
]
