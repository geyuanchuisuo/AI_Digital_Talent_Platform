"""匹配批次执行体（T-25）· 需求 F3-2~F3-7 + 数据库 §6.4/§6.5 + 算法 §4.1/§4.2。

与 `screen_worker` 同构（D-D 快照 / 只补缺 / 逐人提交 / 断点续跑）；差异：
1. **`use_vector=True`**：岗位向量在批次内 `embed_one(build_job_vector_text(snapshot))`
   **只算一次**并复用（§4.2）；算不出（Embedding 挂）→ 全批降级；
2. **降级不失败（D-B）**：Milvus/Embedding 任一不可用 → `experience=llm`、
   `vector_similarity=null`（写入 `dimension_scores_json.vector_similarity`）、
   批次 `vector_degraded=1`，照常 done；
3. 硬淘汰 `score=0`（参与置底排序），`hard_failed` 汇总到批次行（done 时按结果表重算）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dao import match as match_dao
from app.models import MatchBatch, ParsedResume
from app.service import match_engine

logger = logging.getLogger(__name__)


def _evidence_input(db: Session, talent_profile_id: int) -> dict:
    """取该主档最新解析结果的结构化证据（educations/employments/projects）。

    H-A07 修复（2026-11-07）：此前 worker 只传 5 字段，评分摘要拿不到经历/项目
    证据。阶段 1 直接从 `parsed_resumes.structured_json` 取（B 契约 §5 的
    profile_input 兼容口径）；B 的快照服务落地后由 A 切换到该入口。
    查询建议后续收编进 B 的 parsed_resumes dao（此处内联避免跨模块写）。
    """
    parsed = db.execute(
        select(ParsedResume)
        .where(ParsedResume.talent_profile_id == talent_profile_id)
        .order_by(ParsedResume.created_at.desc(), ParsedResume.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if parsed is None or not parsed.structured_json:
        return {"missing_fields": ["resume_parsed"]}  # 原文未解析：证据缺失，按缺失处理
    structured = parsed.structured_json if isinstance(parsed.structured_json, dict) else {}
    evidence = {
        "educations": list(structured.get("educations") or []),
        "employments": list(structured.get("employments") or []),
        "projects": list(structured.get("projects") or []),
    }
    if structured.get("skills"):
        evidence["parsed_skills"] = list(structured["skills"])
    return evidence


def handle_match_batch(batch_id: int) -> None:
    """TaskRunner 的 match handler（签名 `(task_id)`，自开 session、写终态）。"""
    with _session() as db:
        batch = match_dao.get_batch(db, batch_id)
        if batch is None:
            logger.warning("匹配批次 %s 不存在，跳过", batch_id)
            return
        if batch.status not in ("running", "pending"):
            logger.warning("匹配批次 %s 状态为 %s，跳过", batch_id, batch.status)
            return
        try:
            _run_batch(db, batch)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            _fail_batch(batch_id, f"{type(exc).__name__}: {exc}")
            logger.exception("匹配批次 %s 执行失败", batch_id)


def _job_vector(snapshot: dict) -> tuple[list[float] | None, bool]:
    """岗位向量批次内算一次；Embedding 失败 → (None, True) 全批降级（§4.2）。"""
    try:
        from app.core.embedding_client import EmbeddingError, embed_one

        return embed_one(match_engine.build_job_vector_text(snapshot)), False
    except EmbeddingError as exc:
        logger.warning("岗位向量 embedding 失败，本批降级：%s", exc)
        return None, True
    except Exception as exc:  # noqa: BLE001 - 任何向量侧异常都降级
        logger.warning("岗位向量计算异常，本批降级：%s: %s", type(exc).__name__, exc)
        return None, True


def _run_batch(db: Session, batch: MatchBatch) -> None:
    snapshot = dict(batch.config_snapshot_json or {})
    scope = dict(batch.talent_scope_json or {})
    resolved_ids: list[int] = scope.get("resolved_ids") or []
    written = match_dao.result_talent_ids(db, batch.id)

    job_vector, degraded = _job_vector(snapshot)
    if degraded:
        batch.vector_degraded = 1
        db.commit()

    for talent_id in resolved_ids:
        if talent_id in written:
            continue
        profile = match_dao.list_profiles_by_ids(db, [talent_id]).get(talent_id)
        if profile is None or profile.deleted_at is not None:
            continue
        profile_input = {
            "talent_profile_id": profile.id,
            "name": profile.name,
            "degree": profile.degree,
            "work_years": float(profile.work_years) if profile.work_years is not None else None,
            "skills_json": list(profile.skills_json or []),
            "vector_status": profile.vector_status,
            # H-A07 修复（2026-11-07）：补全画像与经历证据输入（阶段 1 用 parsed+profile）
            "current_title": profile.current_title,
            "expected_title": profile.expected_title,
            "tags_json": list(profile.tags_json or []),
            "ability_scores_json": profile.ability_scores_json,
            "manual_edited_fields": list(profile.manual_edited_fields or []),
        }
        profile_input.update(_evidence_input(db, profile.id))
        if profile_input.get("parsed_skills") and not profile_input["skills_json"]:
            profile_input["skills_json"] = list(profile_input.pop("parsed_skills"))
        else:
            profile_input.pop("parsed_skills", None)
        result = match_engine.evaluate(
            snapshot, profile_input, use_vector=True, job_vector=job_vector
        )
        if match_dao.get_result_by_talent(db, batch.id, talent_id) is not None:
            continue
        if result.vector_degraded:
            batch.vector_degraded = 1
        dimension_scores = {**result.dimension_scores, "vector_similarity": result.vector_similarity}
        match_dao.create_result(
            db,
            match_batch_id=batch.id,
            talent_profile_id=talent_id,
            score=round(float(result.score if result.score is not None else 0), 2),
            hard_fail=1 if result.hard_fail else 0,
            hard_fail_reason=result.hard_fail_reason,
            dimension_scores_json=dimension_scores,
            hits_json=result.hits,
            misses_json=result.misses,
            reason=result.reason,
        )
        batch.progress += 1
        batch.heartbeat_at = datetime.now()
        db.commit()

    _finish_batch(db, batch)


def _finish_batch(db: Session, batch: MatchBatch) -> None:
    batch.hard_failed = match_dao.count_hard_failed(db, batch.id)
    batch.status = "done"
    batch.error = None
    batch.finished_at = datetime.now()
    batch.heartbeat_at = datetime.now()
    db.commit()
    logger.info(
        "匹配批次 %s 完成：total=%s 进度=%s 硬淘汰=%s 降级=%s",
        batch.id, batch.total, batch.progress, batch.hard_failed, batch.vector_degraded,
    )


def _fail_batch(batch_id: int, message: str) -> None:
    with _session() as db:
        batch = match_dao.get_batch(db, batch_id)
        if batch is None:
            return
        batch.status = "failed"
        batch.error = message[:2000]
        batch.finished_at = datetime.now()
        db.commit()


def _session() -> Session:
    from app.database import SessionLocal

    return SessionLocal()


__all__ = ["handle_match_batch"]
