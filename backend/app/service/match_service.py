"""匹配批次服务（T-25）· 接口 §8 + 数据库 §6.4/§6.5 + 需求 F3-2~F3-7。

与 `screen_service` 同构（D-D 快照 / D-E retry 复用原批次）；差异：
- scope 为 `all / filter / upload_batch`（无 all_unscreened、多 filter）；
- worker `use_vector=True`：岗位向量**批次内算一次**；Milvus/Embedding 不可用 →
  **降级执行并置批次 `vector_degraded=1`，不 503 不失败**（§4.2）；
- 硬淘汰 `score=0` 参与置底排序；`hard_failed` 汇总到批次行；
- 名次查询时计算（`hard_fail ASC, score DESC, talent_profile_id ASC` + 行号），不持久化。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.dao import job_profiles as job_dao
from app.dao import match as match_dao
from app.models import MatchBatch, ParsedResume, ResumeFile, TalentProfile, TalentTag
from app.service import match_engine
from app.service.screen_service import build_config_snapshot

logger = logging.getLogger(__name__)

BATCH_NOT_FOUND = "匹配批次不存在"
RESULT_NOT_FOUND = "匹配结果不存在"
JOB_NOT_FOUND = "岗位不存在"
MATCH_SCOPE_TYPES = ("all", "filter", "upload_batch")


def _validate_match_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    scope_type = scope.get("type")
    if scope_type not in MATCH_SCOPE_TYPES:
        raise BizError(400, f"scope.type 仅支持 {' / '.join(MATCH_SCOPE_TYPES)}")
    normalized: dict[str, Any] = {"type": scope_type}
    if scope_type == "upload_batch":
        batch_id = (scope.get("upload_batch_id") or "").strip()
        if not batch_id:
            raise BizError(400, "upload_batch scope 需要提供 upload_batch_id")
        normalized["upload_batch_id"] = batch_id
    elif scope_type == "filter":
        degree = scope.get("degree")
        if degree is not None and not isinstance(degree, str):
            raise BizError(400, "filter.degree 必须是字符串")
        min_years = scope.get("min_years")
        if min_years is not None and not isinstance(min_years, (int, float)):
            raise BizError(400, "filter.min_years 必须是数字")
        tags = scope.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise BizError(400, "filter.tags 必须是字符串数组")
        normalized["degree"] = degree
        normalized["min_years"] = min_years
        normalized["tags"] = [t for t in tags if t.strip()]
    return normalized


def resolve_talent_ids(db: Session, scope: Mapping[str, Any]) -> list[int]:
    """按 match scope 圈人（附录 B；filter 的 degree/min_years/tags 全部 AND 语义）。"""
    scope_type = scope["type"]
    base = (
        select(TalentProfile.id)
        .join(ParsedResume, ParsedResume.talent_profile_id == TalentProfile.id)
        .where(TalentProfile.deleted_at.is_(None))
    )
    if scope_type == "all":
        stmt = base
    elif scope_type == "upload_batch":
        stmt = base.join(ResumeFile, ResumeFile.id == ParsedResume.resume_file_id).where(
            ResumeFile.upload_batch_id == scope["upload_batch_id"]
        )
    else:  # filter
        conds = []
        if scope.get("degree"):
            conds.append(TalentProfile.degree == scope["degree"])
        if scope.get("min_years") is not None:
            conds.append(TalentProfile.work_years >= float(scope["min_years"]))
        stmt = base.where(*conds) if conds else base
        if scope.get("tags"):
            # tags AND 语义：每个标签都必须命中（talent_tags 精确匹配；取舍见日志 T-25）
            for tag in scope["tags"]:
                exists = select(TalentTag.id).where(
                    TalentTag.talent_profile_id == TalentProfile.id, TalentTag.tag == tag
                ).exists()
                stmt = stmt.where(exists)
    return sorted(set(db.execute(stmt).scalars().all()))


def _profile_gate_input(row: TalentProfile) -> dict[str, Any]:
    return {
        "talent_profile_id": row.id,
        "degree": row.degree,
        "work_years": float(row.work_years) if row.work_years is not None else None,
        "skills_json": list(row.skills_json or []),
        "vector_status": row.vector_status,
    }


def create_batch(
    db: Session, job_profile_id: int, scope: Mapping[str, Any], *, user_id: int
) -> dict[str, Any]:
    job = job_dao.get(db, job_profile_id)
    if job is None:
        raise BizError(404, JOB_NOT_FOUND)
    if job.is_active == 0:
        raise BizError(400, "岗位已停用，不能发起匹配（历史批次可回看）")

    normalized_scope = _validate_match_scope(scope)
    resolved_ids = resolve_talent_ids(db, normalized_scope)
    if not resolved_ids:
        raise BizError(400, "范围内没有可匹配对象（需解析成功且已有画像）")

    snapshot = build_config_snapshot(job)
    profiles = match_dao.list_profiles_by_ids(db, resolved_ids)
    estimated = 0
    for talent_id in resolved_ids:
        row = profiles.get(talent_id)
        if row is None:
            continue
        reasons, _, _ = match_engine.hard_gate(snapshot, _profile_gate_input(row))
        if not reasons:
            estimated += 1

    batch = match_dao.create_batch(
        db,
        job_profile_id=job_profile_id,
        job_profile_version=job.version,
        config_snapshot_json=snapshot,
        talent_scope_json={**normalized_scope, "resolved_ids": resolved_ids},
        status="pending",
        total=len(resolved_ids),
        created_by=user_id,
    )
    db.commit()
    db.refresh(batch)
    return {
        "batch_id": batch.id,
        "total": batch.total,
        "status": batch.status,
        "estimated_llm_calls": estimated,
    }


def retry_batch(db: Session, batch_id: int) -> dict[str, Any]:
    batch = match_dao.get_batch(db, batch_id)
    if batch is None:
        raise BizError(404, BATCH_NOT_FOUND)
    if batch.status != "failed":
        raise BizError(400, "仅 failed 状态的批次可以重试")
    batch.status = "pending"
    batch.error = None
    batch.started_at = None
    batch.heartbeat_at = None
    batch.finished_at = None
    batch.vector_degraded = 0
    batch.retry_count += 1
    batch.progress = match_dao.count_results(db, batch_id)
    db.commit()
    db.refresh(batch)
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "retry_count": batch.retry_count,
        "total": batch.total,
        "progress": batch.progress,
    }


def get_batch_detail(db: Session, batch_id: int) -> dict[str, Any]:
    batch = match_dao.get_batch(db, batch_id)
    if batch is None:
        raise BizError(404, BATCH_NOT_FOUND)
    scope = dict(batch.talent_scope_json or {})
    resolved_ids: list[int] = scope.pop("resolved_ids", [])
    scope["resolved_ids_count"] = len(resolved_ids)

    processed = match_dao.count_results(db, batch_id)
    written = match_dao.result_talent_ids(db, batch_id)
    skipped_deleted = 0
    if resolved_ids:
        profiles = match_dao.list_profiles_by_ids(db, resolved_ids)
        for talent_id in resolved_ids:
            if talent_id in written:
                continue
            row = profiles.get(talent_id)
            if row is None or row.deleted_at is not None:
                skipped_deleted += 1

    return {
        "batch_id": batch.id,
        "job_profile_id": batch.job_profile_id,
        "job_profile_version": batch.job_profile_version,
        "config_snapshot": dict(batch.config_snapshot_json or {}),
        "scope": scope,
        "status": batch.status,
        "progress": batch.progress,
        "total": batch.total,
        "hard_failed": match_dao.count_hard_failed(db, batch_id),
        "processed": processed,
        "skipped_deleted": skipped_deleted,
        "vector_degraded": bool(batch.vector_degraded),
        "error": batch.error,
        "retry_count": batch.retry_count,
        "created_at": batch.created_at,
        "finished_at": batch.finished_at,
    }


def _result_summary(result: MatchResult, profile: TalentProfile | None, rank: int) -> dict[str, Any]:
    return {
        "result_id": result.id,
        "rank": rank,
        "talent_profile_id": result.talent_profile_id,
        "name": profile.name if profile is not None else "（已删除）",
        "deleted": profile is None or profile.deleted_at is not None,
        "current_title": profile.current_title if profile is not None else None,
        "score": float(result.score),
        "hard_fail": bool(result.hard_fail),
        "hard_fail_reason": result.hard_fail_reason,
        "hits_count": len(result.hits_json or []),
        "misses_count": len(result.misses_json or []),
    }


def list_results(
    db: Session, *, batch_id: int, offset: int = 0, limit: int = 20
) -> dict[str, Any]:
    """名次分页：rank = offset + 行号（接口 §8：名次查询时计算，不持久化）。"""
    rows, total = match_dao.ordered_results(db, batch_id, offset=offset, limit=limit)
    profiles = match_dao.list_profiles_by_ids(db, [r.talent_profile_id for r in rows])
    items = [
        _result_summary(result, profiles.get(result.talent_profile_id), rank=offset + index + 1)
        for index, result in enumerate(rows)
    ]
    return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}


def get_result_detail(db: Session, result_id: int) -> dict[str, Any]:
    """结果理由详情（F3-4/F3-5）：四维分 + vector_similarity（可 null）+ hits/misses + 名次。"""
    result = match_dao.get_result(db, result_id)
    if result is None:
        raise BizError(404, RESULT_NOT_FOUND)
    profile = match_dao.list_profiles_by_ids(db, [result.talent_profile_id]).get(result.talent_profile_id)
    batch = match_dao.get_batch(db, result.match_batch_id)
    base = _result_summary(result, profile, rank=match_dao.result_rank(db, result))
    base["vector_degraded"] = bool(batch.vector_degraded) if batch is not None else False
    base.update(
        {
            "dimension_scores": dict(result.dimension_scores_json or {}),
            "vector_similarity": (
                float(result.dimension_scores_json.get("vector_similarity"))
                if isinstance(result.dimension_scores_json, Mapping)
                and result.dimension_scores_json.get("vector_similarity") is not None
                else None
            ),
            "hits": list(result.hits_json or []),
            "misses": list(result.misses_json or []),
            "reason": result.reason,
        }
    )
    return base


def list_batches_with_title(
    db: Session, *, job_profile_id: int | None = None, offset: int = 0, limit: int = 20
) -> tuple[list[tuple[MatchBatch, str | None]], int]:
    """批次列表（附岗位标题）；T-25 列表接口用。"""
    return match_dao.list_batches(db, job_profile_id=job_profile_id, offset=offset, limit=limit)


def get_batch_row(db: Session, batch_id: int) -> MatchBatch | None:
    return match_dao.get_batch(db, batch_id)
