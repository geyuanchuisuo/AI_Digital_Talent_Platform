"""筛选批次/结果数据访问（T-24）。

约定：不 commit（事务归 service/worker）、不做业务判断。
快照口径（数据库 §6.2）：worker 与 retry **只读 `scope_json.resolved_ids`**，
本 DAO 不提供任何"重新圈人"的查询（圈人在 service 的同步阶段一次性完成）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobProfile, ScreenBatch, ScreenResult, TalentProfile


def get_batch(db: Session, batch_id: int) -> ScreenBatch | None:
    return db.get(ScreenBatch, batch_id)


def create_batch(db: Session, **fields: Any) -> ScreenBatch:
    row = ScreenBatch(**fields)
    db.add(row)
    db.flush()
    return row


def list_batches(
    db: Session,
    *,
    job_profile_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[tuple[ScreenBatch, str | None]], int]:
    """批次列表（附岗位标题；岗位可能被删，isouter 兜底显示 None）。"""
    conds = []
    if job_profile_id is not None:
        conds.append(ScreenBatch.job_profile_id == job_profile_id)
    base = select(ScreenBatch, JobProfile.title).join(
        JobProfile, JobProfile.id == ScreenBatch.job_profile_id, isouter=True
    )
    rows = list(
        db.execute(
            base.where(*conds).order_by(ScreenBatch.id.desc()).offset(offset).limit(limit)
        ).all()
    )
    total = int(
        db.execute(select(func.count(ScreenBatch.id)).where(*conds)).scalar_one()
    )
    return rows, total


def count_results(db: Session, batch_id: int) -> int:
    return int(
        db.execute(
            select(func.count(ScreenResult.id)).where(ScreenResult.screen_batch_id == batch_id)
        ).scalar_one()
    )


def result_talent_ids(db: Session, batch_id: int) -> set[int]:
    """已写结果的成员 id 集合（worker 断点续跑 / retry 只补缺的依据）。"""
    rows = db.execute(
        select(ScreenResult.talent_profile_id).where(ScreenResult.screen_batch_id == batch_id)
    ).scalars().all()
    return set(rows)


def get_result_by_talent(db: Session, batch_id: int, talent_profile_id: int) -> ScreenResult | None:
    """唯一键 `uk_screen_batch_talent` 查询（worker 幂等写）。"""
    return db.execute(
        select(ScreenResult)
        .where(ScreenResult.screen_batch_id == batch_id)
        .where(ScreenResult.talent_profile_id == talent_profile_id)
    ).scalar_one_or_none()


def create_result(db: Session, **fields: Any) -> ScreenResult:
    row = ScreenResult(**fields)
    db.add(row)
    db.flush()
    return row


def list_profiles_by_ids(db: Session, ids: list[int]) -> dict[int, TalentProfile]:
    if not ids:
        return {}
    rows = db.execute(
        select(TalentProfile).where(TalentProfile.id.in_(ids))
    ).scalars().all()
    return {row.id: row for row in rows}


def get_result(db: Session, result_id: int) -> ScreenResult | None:
    return db.get(ScreenResult, result_id)


def count_by_effective_verdict(db: Session, batch_id: int) -> dict[str, int]:
    """按**有效结论**（manual_verdict ?? verdict）重算三档计数（数据库 §6.2 计数口径）。"""
    effective = func.coalesce(ScreenResult.manual_verdict, ScreenResult.verdict)
    rows = db.execute(
        select(effective, func.count(ScreenResult.id))
        .where(ScreenResult.screen_batch_id == batch_id)
        .group_by(effective)
    ).all()
    counts = {"passed": 0, "pending": 0, "rejected": 0}
    for verdict, count in rows:
        if verdict in counts:
            counts[verdict] = int(count)
    return counts


def list_results_page(
    db: Session,
    *,
    batch_id: int,
    verdict: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[tuple[ScreenResult, TalentProfile | None]], int]:
    """结果分页：按有效结论过滤、关键词按姓名；排序 score DESC（NULL 置底）+ 硬淘汰置底。"""
    effective = func.coalesce(ScreenResult.manual_verdict, ScreenResult.verdict)
    conds = [ScreenResult.screen_batch_id == batch_id]
    if verdict is not None:
        conds.append(effective == verdict)
    base = select(ScreenResult, TalentProfile).join(
        TalentProfile, TalentProfile.id == ScreenResult.talent_profile_id, isouter=True
    )
    if keyword:
        pattern = f"%{keyword}%"
        conds.append(TalentProfile.name.like(pattern))
    rows = list(
        db.execute(
            base.where(*conds)
            .order_by(
                ScreenResult.hard_fail.asc(),
                ScreenResult.score.desc(),
                ScreenResult.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )
    total = int(
        db.execute(
            select(func.count(ScreenResult.id))
            .select_from(ScreenResult)
            .join(TalentProfile, TalentProfile.id == ScreenResult.talent_profile_id, isouter=True)
            .where(*conds)
        ).scalar_one()
    )
    return rows, total
