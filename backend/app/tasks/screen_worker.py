"""筛选批次执行体（T-24）· 需求 F1-7/F1-8 + 数据库 §6.2。

硬口径（改动前先读）：
1. **只读 `resolved_ids` 快照**（D-D）——禁止重新按 scope 圈人；创建时未进快照的
   画像（含 retry 前新增）一律不进本批。
2. **幂等**：依唯一键 `uk_screen_batch_talent` 先查后写；已写成员直接跳过
   （retry 只补缺，不重跑已写成员——快照语义，数据库 §6.2）。
3. **`use_vector=False`**（决策 D-A）：全程不触碰 Embedding/Milvus；
   `vector_degraded` 恒 0。
4. **逐人提交**：每完成一人 `progress+1` 并提交（轮询可见进度、心跳随之刷新）；
   快照内画像已软删/不存在 → 跳过不写行（done 后由详情接口计 `skipped_deleted`）。
5. **done 时按结果表重算三档计数**（防漂移）；`total` 保持快照值。
6. 异常 → 批次 `failed` + error，可 retry（断点续跑：已写行保留）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dao import screen as screen_dao
from app.models import ParsedResume, ScreenBatch
from app.config import settings
from app.service import match_engine
from app.service.screen_service import build_config_snapshot

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


def _verdict_for(score: float | None, snapshot: dict) -> str:
    if score is None:
        return "rejected"
    if score >= float(snapshot.get("pass_threshold", 75)):
        return "passed"
    if score >= float(snapshot.get("pending_threshold", 60)):
        return "pending"
    return "rejected"


def handle_screen_batch(batch_id: int) -> None:
    """TaskRunner 的 screen handler（签名 `(task_id)`，自开 session、写终态）。"""
    with _session() as db:
        batch = screen_dao.get_batch(db, batch_id)
        if batch is None:
            logger.warning("筛选批次 %s 不存在，跳过", batch_id)
            return
        if batch.status not in ("running", "pending"):
            # running=runner 认领；pending=requeue/启动恢复后的直接执行（重试）
            logger.warning("筛选批次 %s 状态为 %s，跳过", batch_id, batch.status)
            return
        try:
            _run_batch(db, batch)
        except Exception as exc:  # noqa: BLE001 - 批次级失败落 error，可 retry
            db.rollback()
            _fail_batch(batch_id, f"{type(exc).__name__}: {exc}")
            logger.exception("筛选批次 %s 执行失败", batch_id)


def _run_batch(db: Session, batch: ScreenBatch) -> None:
    snapshot = dict(batch.config_snapshot_json or {})
    scope = dict(batch.scope_json or {})
    resolved_ids: list[int] = scope.get("resolved_ids") or []
    # 断点续跑：已写结果的成员直接跳过（retry 只补缺）
    written = screen_dao.result_talent_ids(db, batch.id)
    engine_job = snapshot  # 引擎入参与快照同构（T-23 约定）

    for talent_id in resolved_ids:
        if talent_id in written:
            continue
        profile = screen_dao.list_profiles_by_ids(db, [talent_id]).get(talent_id)
        if profile is None or profile.deleted_at is not None:
            # 软删/消失：跳过不写行（skipped_deleted 由详情接口统计）
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
        result = match_engine.evaluate(engine_job, profile_input, use_vector=False)
        # 双重幂等：写前再查一次（并发/重试窗口内唯一键兜底）
        if screen_dao.get_result_by_talent(db, batch.id, talent_id) is not None:
            continue
        score = None if result.score is None else round(float(result.score), 2)
        screen_dao.create_result(
            db,
            model_version=settings.llm_model,
            prompt_version=settings.prompt_version,
            attempt=1,
            profile_input_snapshot_json=profile_input,
            screen_batch_id=batch.id,
            job_profile_id=batch.job_profile_id,
            talent_profile_id=talent_id,
            hard_fail=1 if result.hard_fail else 0,
            hard_fail_reason=result.hard_fail_reason,
            score=score,
            verdict=_verdict_for(result.score, snapshot),
            reason_summary=result.reason,
            hits_json=result.hits,
            misses_json=result.misses,
        )
        batch.progress += 1
        batch.heartbeat_at = datetime.now()  # handler 自管心跳（T-12 口径）
        db.commit()

    _finish_batch(db, batch)


def _finish_batch(db: Session, batch: ScreenBatch) -> None:
    """done 收尾：按结果表重算三档计数（有效结论口径），total 保持快照值。"""
    counts = screen_dao.count_by_effective_verdict(db, batch.id)
    batch.passed = counts["passed"]
    batch.pending_count = counts["pending"]
    batch.rejected = counts["rejected"]
    batch.status = "done"
    batch.error = None
    batch.finished_at = datetime.now()
    batch.heartbeat_at = datetime.now()
    db.commit()
    logger.info(
        "筛选批次 %s 完成：total=%s 进度=%s 通过=%s 待定=%s 淘汰=%s",
        batch.id, batch.total, batch.progress, batch.passed, batch.pending_count, batch.rejected,
    )


def _fail_batch(batch_id: int, message: str) -> None:
    with _session() as db:
        batch = screen_dao.get_batch(db, batch_id)
        if batch is None:
            return
        batch.status = "failed"
        batch.error = message[:2000]
        batch.finished_at = datetime.now()
        db.commit()


def _session() -> Session:
    from app.database import SessionLocal

    return SessionLocal()


__all__ = ["build_config_snapshot", "handle_screen_batch"]
