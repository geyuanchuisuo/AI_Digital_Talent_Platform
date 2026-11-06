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

from sqlalchemy.orm import Session

from app.dao import match as match_dao
from app.models import MatchBatch
from app.service import match_engine

logger = logging.getLogger(__name__)


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
            "degree": profile.degree,
            "work_years": float(profile.work_years) if profile.work_years is not None else None,
            "skills_json": list(profile.skills_json or []),
            "vector_status": profile.vector_status,
        }
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
