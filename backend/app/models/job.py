"""岗位与筛选/匹配域（数据库设计 v1.2 §6）：
`job_profiles` / `screen_batches` / `screen_results` / `match_batches` / `match_results`。

关键口径：
- 批次表用 `retry_count` / `vector_degraded`（**无 retry_of_batch_id**，决策 D-E）；
- `scope_json` / `talent_scope_json` 创建时追加 `resolved_ids` 成员快照（决策 D-D）；
- T-A42：批次 `(job_profile_id, idempotency_key)` 组合唯一，`idempotency_key` 可空，
  用于「重复请求不重复建结果」；不传键时行为不变（岗位/档案变更新建一轮）；
- 结果表用复合唯一键保证 retry 幂等；`match_results` **不持久化 rank 列**（查询时计算）；
- 筛选硬淘汰 score=NULL，匹配硬淘汰 score=0（决策 D-8）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    BOOL_TYPE,
    JSONDict,
    JSONList,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    UINT_TYPE,
    CreatedAtMixin,
    UpdatedAtMixin,
    num_default,
    str_default,
)


class JobProfile(PKMixin, UpdatedAtMixin, Base):
    """§6.1 岗位条件；version 每次 PUT +1（快照/缓存失效依据）。"""

    __tablename__ = "job_profiles"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    title: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements_text: Mapped[str] = mapped_column(Text, nullable=False)
    min_degree: Mapped[str | None] = mapped_column(String(20), nullable=True)
    min_work_years: Mapped[Decimal | None] = mapped_column(DECIMAL(4, 1), nullable=True)
    must_skills_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    bonus_skills_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    hard_rule_switches_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    pass_threshold: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, default=Decimal("75"), server_default=num_default(75)
    )
    pending_threshold: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, default=Decimal("60"), server_default=num_default(60)
    )
    weights_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=num_default(1)
    )
    is_active: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=1, server_default=num_default(1)
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class ScreenBatch(PKMixin, CreatedAtMixin, Base):
    """§6.2 筛选批次；total = len(scope_json['resolved_ids'])，retry 保持不变。"""

    __tablename__ = "screen_batches"
    __table_args__ = (
        Index("idx_screen_job", "job_profile_id"),
        UniqueConstraint(
            "job_profile_id", "idempotency_key", name="uk_screen_batch_idem"
        ),
        MYSQL_TABLE_OPTIONS,
    )

    job_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    scope_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    # A03：创建时冻结的成员评分输入 + B 正式档案版本摘要（worker/retry 只读此列）
    frozen_inputs_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    progress: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    total: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    passed: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    pending_count: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    rejected: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    vector_degraded: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    created_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # T-A42：客户端显式幂等键（可空＝不启用）。同一岗位下重复提交只回原批次。
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ScreenResult(PKMixin, CreatedAtMixin, Base):
    """§6.3 筛选结果；uk_screen_batch_talent 保证 retry 只补缺不重跑。"""

    __tablename__ = "screen_results"
    __table_args__ = (
        UniqueConstraint("screen_batch_id", "talent_profile_id", name="uk_screen_batch_talent"),
        Index("idx_batch_verdict", "screen_batch_id", "verdict"),
        Index("idx_screen_job_result", "job_profile_id"),
        Index("idx_talent", "talent_profile_id"),
        MYSQL_TABLE_OPTIONS,
    )

    screen_batch_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    talent_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    hard_fail: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    hard_fail_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 2), nullable=True)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    manual_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    manual_verdict_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    manual_verdict_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    hits_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    misses_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    # T-A42 逐结果失败：引擎异常/超时的单份失败原因；非空=失败行（retry 重跑目标）
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 结果快照与版本追溯（T-A33/T-A43，2026-11-07 功能 5b；D 指标口径可追溯基础）----
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    attempt: Mapped[int | None] = mapped_column(UINT_TYPE, nullable=True)
    profile_input_snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)


class MatchBatch(PKMixin, CreatedAtMixin, Base):
    """§6.4 匹配批次；成员快照语义与复位规则同 ScreenBatch。"""

    __tablename__ = "match_batches"
    __table_args__ = (
        Index("idx_match_job", "job_profile_id"),
        UniqueConstraint(
            "job_profile_id", "idempotency_key", name="uk_match_batch_idem"
        ),
        MYSQL_TABLE_OPTIONS,
    )

    job_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    talent_scope_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    # A03：与 ScreenBatch 同口径的批次开始冻结输入
    frozen_inputs_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    progress: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    total: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    hard_failed: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        UINT_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    vector_degraded: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    created_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # T-A42：与 ScreenBatch 同构的请求级幂等键（可空＝不启用）。
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MatchResult(PKMixin, CreatedAtMixin, Base):
    """§6.5 匹配结果；名次查询时计算，不持久化 rank 列。"""

    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("match_batch_id", "talent_profile_id", name="uk_match_batch_talent"),
        Index("idx_match_talent", "talent_profile_id"),
        MYSQL_TABLE_OPTIONS,
    )

    match_batch_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    talent_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    score: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, default=Decimal("0"), server_default=num_default(0)
    )
    hard_fail: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    hard_fail_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dimension_scores_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    hits_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    misses_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # T-A42 逐结果失败：引擎异常/超时的单份失败原因；非空=失败行（retry 重跑目标）
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 结果快照与版本追溯（T-A33/T-A43，2026-11-07 功能 5b）----
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    attempt: Mapped[int | None] = mapped_column(UINT_TYPE, nullable=True)
    profile_input_snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    # ---- 人工结论（对齐 screen_results.manual_verdict*，T-A43；评分失败≠初筛未通过）----
    manual_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    manual_verdict_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    manual_verdict_at: Mapped[datetime | None] = mapped_column(nullable=True)
