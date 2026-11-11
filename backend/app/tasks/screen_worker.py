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
7. **A02-1（V2.6 §5）不再有硬淘汰**：硬条件不符/资料缺失者照样进 Prompt C 拿分，
   不符项与待核实项进 `misses`；`hard_fail` 恒 0、不再写 `score=NULL`。
   三档 `verdict` 仍是**内部参考标签**，不改申请状态、不自动推进/淘汰；人工结论
   独立存 `manual_verdict*`（两档标签与人工三态对齐属 A02-2/M5 范围）。
8. **A03 批次档案冻结**：评分输入一律取 `batch.frozen_inputs_json`（创建时冻结的
   值拷贝 + B 正式版本绑定），**不再读取当前** `talent_profiles` / `parsed_resumes`
   内容；排队期档案修订/重新解析不影响本批，retry 补缺与历史回读不读新版本。
   当前档案只用于判断「软删/不存在跳过」（T-24 既有语义）。缺冻结列的历史批次
   走 `_legacy_current_input()` 显式告警回退，不静默污染新批次。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dao import screen as screen_dao
from app.models import ParsedResume, ScreenBatch
from app.service import match_engine
from app.service.screen_service import (
    build_config_snapshot,
    build_profile_input,
    frozen_profile_input,
    resolve_batch_model_versions,
)

logger = logging.getLogger(__name__)


def _legacy_current_input(db: Session, profile) -> dict:
    """A03 兼容回退：历史批次（无 frozen_inputs_json）仍按当前档案取输入。

    只用于迁移前创建的批次，显式告警；不得用于新批次，否则排队期档案变更会
    回溯污染本批（A03 要修的正是这一点）。
    """
    logger.warning(
        "筛选批次缺少成员 %s 的 A03 冻结输入（历史批次兼容），回退读取当前档案",
        profile.id,
    )
    parsed = db.execute(
        select(ParsedResume)
        .where(ParsedResume.talent_profile_id == profile.id)
        .order_by(ParsedResume.created_at.desc(), ParsedResume.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return build_profile_input(profile, parsed)


def _verdict_for(score: float | None, snapshot: dict) -> str:
    """三档**内部参考标签**（V2.6 §5）。两档化 + 人工三态属 A02-2/M5 范围，本轮不动。

    T-A34 三处口径修复：
    1. 阈值**必须来自批次快照**，不设来源示例默认值（V2.6 §5「具体阈值由管理员
       配置，不设置来源示例默认值」）。旧实现 `snapshot.get(..., 75/60)` 在快照
       损坏或缺字段时会静默按示例值判定，改为显式 `KeyError`，交由批次失败/重试
       路径暴露数据问题。
    2. `score is None`（评分失败）**不得计为「不满足条件」**（V2.6 §5「评分失败
       单独显示，不计为不满足条件」）。A02-1 之后引擎恒返回分数；该分支若真出现，
       抛错让批次失败并保留重试入口，不伪装成内部标签 `rejected`。
    3. 达线判定保持 `>=`（**含等于**，T-A34 验收点），由下方反例测试固化。
    """
    if score is None:
        raise ValueError(
            "评分失败（score=None）不得写入内部参考标签；批次按失败处理并保留重试入口"
        )
    pass_threshold = float(snapshot["pass_threshold"])
    pending_threshold = float(snapshot["pending_threshold"])
    if score >= pass_threshold:
        return "passed"
    if score >= pending_threshold:
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
    # T-A34（V2.6 §5）：阈值必须来自管理员配置的批次快照，禁止静默套用示例默认值。
    # 放在调用 LLM 之前校验，避免损坏快照消耗整批模型调用后才暴露问题。
    missing_thresholds = [
        key for key in ("pass_threshold", "pending_threshold") if key not in snapshot
    ]
    if missing_thresholds:
        raise ValueError(
            f"筛选批次 {batch.id} 配置快照缺少阈值字段 {missing_thresholds}，"
            "拒绝按示例默认值判定（V2.6 §5）"
        )
    # T-A42：整批共用快照固定的模型/Prompt 版本，retry 补缺也不受运行时配置变更影响。
    model_version, prompt_version = resolve_batch_model_versions(snapshot, logger=logger)
    scope = dict(batch.scope_json or {})
    resolved_ids: list[int] = scope.get("resolved_ids") or []
    # A03：成员评分输入在创建批次时冻结；worker / retry 只读此列，绝不重读当前档案内容
    frozen = batch.frozen_inputs_json
    # 断点续跑：已写成功结果的成员跳过；失败行（failure_reason 非空）重跑（T-A42）
    written = screen_dao.result_talent_ids(db, batch.id)
    failed_ids = screen_dao.failed_result_talent_ids(db, batch.id)
    written = written - failed_ids
    engine_job = snapshot  # 引擎入参与快照同构（T-23 约定）

    for talent_id in resolved_ids:
        if talent_id in written:
            continue
        profile = screen_dao.list_profiles_by_ids(db, [talent_id]).get(talent_id)
        if profile is None or profile.deleted_at is not None:
            # 软删/消失：跳过不写行（skipped_deleted 由详情接口统计）
            # A03 只冻结内容，不冻结「存在性」——T-24 既有语义保持不变
            continue
        profile_input = frozen_profile_input(frozen, talent_id)
        if profile_input is None:
            profile_input = _legacy_current_input(db, profile)
        try:
            result = match_engine.evaluate(engine_job, profile_input, use_vector=False)
        except Exception as exc:  # noqa: BLE001 - T-A42 逐结果失败：落行留痕，批次继续
            logger.warning("筛选批次 %s 单份 %s 评分失败：%s", batch.id, talent_id, exc)
            screen_dao.create_result(
                db,
                model_version=model_version,
                prompt_version=prompt_version,
                attempt=1,
                profile_input_snapshot_json=profile_input,
                screen_batch_id=batch.id,
                job_profile_id=batch.job_profile_id,
                talent_profile_id=talent_id,
                verdict="failed",
                failure_reason=f"{type(exc).__name__}: {exc}"[:2000],
            )
            batch.progress += 1
            batch.heartbeat_at = datetime.now()
            db.commit()
            continue
        # 双重幂等：写前再查一次（并发/重试窗口内唯一键兜底）
        if screen_dao.get_result_by_talent(db, batch.id, talent_id) is not None:
            continue
        score = None if result.score is None else round(float(result.score), 2)
        screen_dao.create_result(
            db,
            model_version=model_version,
            prompt_version=prompt_version,
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
