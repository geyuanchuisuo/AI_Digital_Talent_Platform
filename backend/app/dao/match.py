"""匹配批次/结果数据访问（T-25）。

与 `dao/screen.py` 同构；差异：结果无 verdict 列（名次查询时计算）、批次有 `hard_failed` 汇总。
快照口径（数据库 §6.4）：worker 与 retry 只读 `talent_scope_json.resolved_ids`。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobProfile, MatchBatch, MatchResult, TalentProfile


def get_batch(db: Session, batch_id: int) -> MatchBatch | None:
    return db.get(MatchBatch, batch_id)


def create_batch(db: Session, **fields: Any) -> MatchBatch:
    row = MatchBatch(**fields)
    db.add(row)
    db.flush()
    return row


def list_batches(
    db: Session,
    *,
    job_profile_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[tuple[MatchBatch, str | None]], int]:
    conds = []
    if job_profile_id is not None:
        conds.append(MatchBatch.job_profile_id == job_profile_id)
    base = select(MatchBatch, JobProfile.title).join(
        JobProfile, JobProfile.id == MatchBatch.job_profile_id, isouter=True
    )
    rows = list(
        db.execute(base.where(*conds).order_by(MatchBatch.id.desc()).offset(offset).limit(limit)).all()
    )
    total = int(db.execute(select(func.count(MatchBatch.id)).where(*conds)).scalar_one())
    return rows, total


def count_results(db: Session, batch_id: int) -> int:
    return int(
        db.execute(
            select(func.count(MatchResult.id)).where(MatchResult.match_batch_id == batch_id)
        ).scalar_one()
    )


def count_hard_failed(db: Session, batch_id: int) -> int:
    return int(
        db.execute(
            select(func.count(MatchResult.id))
            .where(MatchResult.match_batch_id == batch_id)
            .where(MatchResult.hard_fail == 1)
        ).scalar_one()
    )


def result_talent_ids(db: Session, batch_id: int) -> set[int]:
    rows = db.execute(
        select(MatchResult.talent_profile_id).where(MatchResult.match_batch_id == batch_id)
    ).scalars().all()
    return set(rows)


def get_result_by_talent(db: Session, batch_id: int, talent_profile_id: int) -> MatchResult | None:
    return db.execute(
        select(MatchResult)
        .where(MatchResult.match_batch_id == batch_id)
        .where(MatchResult.talent_profile_id == talent_profile_id)
    ).scalar_one_or_none()


def failed_result_talent_ids(db: Session, batch_id: int) -> set[int]:
    """T-A42：本批失败行的人才 id 集合（retry 只重跑失败项）。"""
    from app.models import MatchResult

    rows = db.execute(
        select(MatchResult.talent_profile_id).where(
            getattr(MatchResult, "match_batch_id") == batch_id,
            MatchResult.failure_reason.is_not(None),
        )
    ).scalars().all()
    return set(rows)


def create_result(db: Session, **fields: Any) -> MatchResult:
    row = MatchResult(**fields)
    db.add(row)
    db.flush()
    return row


def list_profiles_by_ids(db: Session, ids: list[int]) -> dict[int, TalentProfile]:
    if not ids:
        return {}
    rows = db.execute(select(TalentProfile).where(TalentProfile.id.in_(ids))).scalars().all()
    return {row.id: row for row in rows}


def ordered_results(
    db: Session, batch_id: int, *, offset: int = 0, limit: int = 20
) -> tuple[list[MatchResult], int]:
    """名次序：`hard_fail ASC, score DESC, talent_profile_id ASC`（接口 §8；名次查询时计算）。"""
    base = select(MatchResult).where(MatchResult.match_batch_id == batch_id)
    rows = list(
        db.execute(
            base.order_by(
                MatchResult.hard_fail.asc(), MatchResult.score.desc(), MatchResult.talent_profile_id.asc()
            )
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    total = int(
        db.execute(select(func.count(MatchResult.id)).where(MatchResult.match_batch_id == batch_id)).scalar_one()
    )
    return rows, total


def get_result(db: Session, result_id: int) -> MatchResult | None:
    return db.get(MatchResult, result_id)


def result_rank(db: Session, result: MatchResult) -> int:
    """单条结果的名次 = 排序在我之前的行数 + 1（不持久化 rank 列，数据库 §6.5）。"""
    ahead = int(
        db.execute(
            select(func.count(MatchResult.id)).where(
                MatchResult.match_batch_id == result.match_batch_id,
                (
                    (MatchResult.hard_fail < result.hard_fail)
                    | (
                        (MatchResult.hard_fail == result.hard_fail)
                        & (MatchResult.score > result.score)
                    )
                    | (
                        (MatchResult.hard_fail == result.hard_fail)
                        & (MatchResult.score == result.score)
                        & (MatchResult.talent_profile_id < result.talent_profile_id)
                    )
                ),
            )
        ).scalar_one()
    )
    return ahead + 1
