"""匹配批次执行体（T-25）· 需求 F3-2~F3-7 + 数据库 §6.4/§6.5 + 算法 §4.1/§4.2。

与 `screen_worker` 同构（D-D 快照 / 只补缺 / 逐人提交 / 断点续跑）；差异：
1. **`use_vector=True`**：岗位向量在批次内 `embed_one(build_job_vector_text(snapshot))`
   **只算一次**并复用（§4.2）；算不出（Embedding 挂）→ 全批降级；
2. **降级不失败（D-B）**：Milvus/Embedding 任一不可用 → `experience=llm`、
   `vector_similarity=null`（写入 `dimension_scores_json.vector_similarity`）、
   批次 `vector_degraded=1`，照常 done；
3. **A02-1（V2.6 §5）不再有硬淘汰**：规则层不符项/待核实项只进 `misses`，
   所有候选人都进 Prompt C 并拿到分数；`hard_fail` 恒 0、`hard_failed` 恒 0。
   原 `score=0` 置底分支已从 `match_engine.evaluate` 移除，DB 字段保留兼容历史行。
4. **A03 批次档案冻结（与 screen_worker 同口径）**：评分输入一律取
   `batch.frozen_inputs_json`（创建时冻结的值拷贝 + B 正式版本绑定），不再读取
   当前档案内容；排队期档案修订/重新解析不影响本批，retry 与历史回读不读新版本。
   当前档案只用于「软删/不存在跳过」；缺冻结列的历史批次显式告警回退。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dao import match as match_dao
from app.models import MatchBatch, ParsedResume
from app.service import match_engine
from app.service.screen_service import (
    build_profile_input,
    frozen_profile_input,
    resolve_batch_model_versions,
)

logger = logging.getLogger(__name__)


def _legacy_current_input(db: Session, profile) -> dict:
    """A03 兼容回退：历史批次（无 frozen_inputs_json）仍按当前档案取输入。

    只用于迁移前创建的批次，显式告警；新批次一律读冻结输入。
    """
    logger.warning(
        "匹配批次缺少成员 %s 的 A03 冻结输入（历史批次兼容），回退读取当前档案",
        profile.id,
    )
    parsed = db.execute(
        select(ParsedResume)
        .where(ParsedResume.talent_profile_id == profile.id)
        .order_by(ParsedResume.created_at.desc(), ParsedResume.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return build_profile_input(profile, parsed)


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
    # T-A42：整批共用快照固定的模型/Prompt 版本（与 screen_worker 同口径）。
    model_version, prompt_version = resolve_batch_model_versions(snapshot, logger=logger)
    scope = dict(batch.talent_scope_json or {})
    resolved_ids: list[int] = scope.get("resolved_ids") or []
    # A03：成员评分输入在创建批次时冻结；worker / retry 只读此列，不重读当前档案内容
    frozen = batch.frozen_inputs_json
    written = match_dao.result_talent_ids(db, batch.id)
    failed_ids = match_dao.failed_result_talent_ids(db, batch.id)
    written = written - failed_ids

    job_vector, degraded = _job_vector(snapshot)
    if degraded:
        batch.vector_degraded = 1
        db.commit()

    for talent_id in resolved_ids:
        if talent_id in written:
            continue
        profile = match_dao.list_profiles_by_ids(db, [talent_id]).get(talent_id)
        if profile is None or profile.deleted_at is not None:
            # A03 只冻结内容，不冻结「存在性」——软删/不存在仍跳过
            continue
        profile_input = frozen_profile_input(frozen, talent_id)
        if profile_input is None:
            profile_input = _legacy_current_input(db, profile)
        try:
            result = match_engine.evaluate(
                snapshot, profile_input, use_vector=True, job_vector=job_vector
            )
        except Exception as exc:  # noqa: BLE001 - T-A42 逐结果失败：落行留痕，批次继续
            logger.warning("匹配批次 %s 单份 %s 评分失败：%s", batch.id, talent_id, exc)
            match_dao.create_result(
                db,
                model_version=model_version,
                prompt_version=prompt_version,
                attempt=1,
                profile_input_snapshot_json=profile_input,
                match_batch_id=batch.id,
                talent_profile_id=talent_id,
                hard_fail=0,
                score=0,
                dimension_scores_json={},
                hits_json=[],
                misses_json=[],
                reason=None,
                failure_reason=f"{type(exc).__name__}: {exc}"[:2000],
            )
            batch.progress += 1
            batch.heartbeat_at = datetime.now()
            db.commit()
            continue
        if match_dao.get_result_by_talent(db, batch.id, talent_id) is not None:
            continue
        if result.vector_degraded:
            batch.vector_degraded = 1
        dimension_scores = {**result.dimension_scores, "vector_similarity": result.vector_similarity}
        match_dao.create_result(
            db,
            model_version=model_version,
            prompt_version=prompt_version,
            attempt=1,
            profile_input_snapshot_json=profile_input,
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
