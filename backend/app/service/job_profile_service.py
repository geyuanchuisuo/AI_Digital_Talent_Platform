"""岗位条件服务（T-22）· 接口 §6 + 数据库 §6.1 + 需求 F3-1。

三条硬口径：
1. **校验统一 400**（接口 §6）：weights 四维总和=100、pass_threshold > pending_threshold、
   `min_degree` 逐字等于六档字面值之一（**「高中中专」**，`"高中/中专"` 拒绝，D-G）。
2. **技能入库前归一化（D-N）**：`normalize_skills()` 是唯一实现——`Python`→`python`、
   `golang`→`go`、`Python 3.11`→`python`；岗位技能与画像技能、T-23 命中判定共用一张别名表。
3. **快照隔离**：PUT 成功 version+1；历史批次读的是自己的 `config_snapshot_json`，
   不受后续修改影响（数据库 §6.1）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.errors import BizError
from app.core.normalize import DEGREE_VALUES, normalize_skills
from app.dao import job_profiles as job_dao
from app.models import JobProfile
from app.schemas.job_profile import JobProfileWriteRequest

JOB_NOT_FOUND = "岗位不存在"
JOB_DISABLED = "岗位已停用（存在历史批次引用）"


def _validate(payload: JobProfileWriteRequest) -> None:
    """业务校验，非法抛 400（统一 400 口径，不经 Pydantic 422）。"""
    if payload.weights.total() != 100:
        raise BizError(
            400,
            f"weights 四维权重总和必须为 100，当前为 {payload.weights.total()}"
            f"（skills/experience/education/others）",
        )
    if payload.pass_threshold <= payload.pending_threshold:
        raise BizError(
            400,
            f"pass_threshold（{payload.pass_threshold}）必须大于 pending_threshold（{payload.pending_threshold}）",
        )
    if payload.min_degree is not None and payload.min_degree not in DEGREE_VALUES:
        raise BizError(
            400,
            f"min_degree 仅支持 {'/'.join(DEGREE_VALUES)}（注意是「高中中专」，不是「高中/中专」）",
        )


def _columns(payload: JobProfileWriteRequest) -> dict[str, Any]:
    """请求体 → ORM 列值；技能在此归一化（D-N）。"""
    return {
        "title": payload.title.strip(),
        "department": (payload.department or "").strip() or None,
        "jd_text": payload.jd_text,
        "requirements_text": payload.requirements_text,
        "min_degree": payload.min_degree,
        "min_work_years": (
            None if payload.min_work_years is None else round(float(payload.min_work_years), 1)
        ),
        "must_skills_json": normalize_skills(payload.must_skills),
        "bonus_skills_json": normalize_skills(payload.bonus_skills),
        "hard_rule_switches_json": payload.hard_rule_dict(),
        "pass_threshold": round(float(payload.pass_threshold), 2),
        "pending_threshold": round(float(payload.pending_threshold), 2),
        "weights_json": payload.weights.model_dump(),
    }


def create_job(db: Session, payload: JobProfileWriteRequest, *, user_id: int) -> JobProfile:
    """新建岗位：version=1、is_active=1、created_by/updated_by 同人。"""
    _validate(payload)
    columns = _columns(payload)
    row = job_dao.create(
        db, version=1, is_active=1, created_by=user_id, updated_by=user_id, **columns
    )
    from app.modules.talents.graph_outbox import enqueue
    enqueue(db, 'job', row.id, row.version, operation='upsert', requested_by=user_id)
    db.commit()
    db.refresh(row)
    return row


def put_job(db: Session, job_id: int, payload: JobProfileWriteRequest, *, user_id: int) -> JobProfile:
    """全量更新：校验同 POST；成功 version+1 并写 updated_by（快照隔离，历史批次不变）。"""
    row = db.scalar(select(JobProfile).where(JobProfile.id == job_id).with_for_update())
    if row is None:
        raise BizError(404, JOB_NOT_FOUND)
    _validate(payload)
    columns = _columns(payload)
    columns["version"] = row.version + 1
    columns["updated_by"] = user_id
    job_dao.apply_fields(db, row, columns)
    from app.modules.talents.graph_outbox import enqueue
    enqueue(db, 'job', row.id, row.version, operation='upsert' if row.is_active else 'invalidate', requested_by=user_id)
    db.commit()
    db.refresh(row)
    return row


def delete_job(db: Session, job_id: int) -> dict[str, Any]:
    """删除：有批次引用 → 软删（is_active=0）；无引用 → 物理删除；重复删除 404。"""
    row = db.scalar(select(JobProfile).where(JobProfile.id == job_id).with_for_update())
    if row is None or row.is_active == 0:
        # is_active=0 即已软删（可能仍被 GET 全量列表看到，但不可再删）
        raise BizError(404, JOB_NOT_FOUND)
    from app.modules.talents.graph_outbox import enqueue
    enqueue(db, 'job', row.id, row.version, operation='invalidate', force=True)
    if job_dao.has_batches(db, job_id):
        row.is_active = 0
        db.commit()
        return {"deleted": True, "soft_deleted": True}
    job_dao.hard_delete(db, row)
    db.commit()
    return {"deleted": True, "soft_deleted": False}


def get_job(db: Session, job_id: int) -> JobProfile:
    row = job_dao.get(db, job_id)
    if row is None:
        raise BizError(404, JOB_NOT_FOUND)
    return row


def list_jobs(
    db: Session,
    *,
    keyword: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[JobProfile], int]:
    return job_dao.list_page(db, keyword=keyword, is_active=is_active, offset=offset, limit=limit)


def apply_fields(db: Session, row: JobProfile, columns: dict[str, Any]) -> None:
    """供测试/内部复用的薄封装（T-22 内不直接使用）。"""
    job_dao.apply_fields(db, row, columns)


def graph_source(db: Session, job_id: int, version: int, *, user_id: int) -> dict:
    """Authoritative B09 input; full effective management gate and current version."""
    from app.modules.knowledge.service import require_admin
    require_admin(db, user_id)
    row = job_dao.get(db, job_id)
    if row is None or not row.is_active or row.version != version:
        raise BizError(409, '岗位版本或来源当前不可用')
    return dict(job_id=row.id, version=row.version, title=row.title, jd_text=row.jd_text,
                must_skills=list(row.must_skills_json or []))
