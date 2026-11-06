"""`job_profiles` 数据访问（T-22）。

约定与其余 DAO 一致：不 commit（事务归 service）、不做业务判断。
"""

from __future__ import annotations

from sqlalchemy import CHAR, func, or_, select
from sqlalchemy.orm import Session

from app.models import JobProfile, MatchBatch, ScreenBatch


def get(db: Session, job_id: int) -> JobProfile | None:
    return db.get(JobProfile, job_id)


def list_page(
    db: Session,
    *,
    keyword: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[JobProfile], int]:
    """岗位列表：keyword 对 title/department 模糊；is_active None = 不过滤。"""
    conds = []
    if keyword:
        pattern = f"%{keyword.replace(chr(92), chr(92) * 2).replace('%', chr(92) + '%').replace('_', chr(92) + '_')}%"
        conds.append(or_(JobProfile.title.like(pattern), JobProfile.department.like(pattern)))
    if is_active is not None:
        conds.append(JobProfile.is_active == (1 if is_active else 0))
    rows = list(
        db.execute(
            select(JobProfile)
            .where(*conds)
            .order_by(JobProfile.updated_at.desc(), JobProfile.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    total = int(db.execute(select(func.count(JobProfile.id)).where(*conds)).scalar_one())
    return rows, total


def create(db: Session, **fields: object) -> JobProfile:
    row = JobProfile(**fields)
    db.add(row)
    db.flush()
    return row


def has_batches(db: Session, job_id: int) -> bool:
    """筛选或匹配批次是否引用过该岗位（DELETE 软删/物删的判定依据）。"""
    screen_hit = db.execute(
        select(func.count(ScreenBatch.id)).where(ScreenBatch.job_profile_id == job_id)
    ).scalar_one()
    if int(screen_hit) > 0:
        return True
    match_hit = db.execute(
        select(func.count(MatchBatch.id)).where(MatchBatch.job_profile_id == job_id)
    ).scalar_one()
    return int(match_hit) > 0


def hard_delete(db: Session, job: JobProfile) -> None:
    db.delete(job)
    db.flush()


def apply_fields(db: Session, row: JobProfile, columns: dict) -> None:
    """逐字段赋值并 flush（PUT 用；调用方负责 version/updated_by）。"""
    for key, value in columns.items():
        setattr(row, key, value)
    db.flush()
