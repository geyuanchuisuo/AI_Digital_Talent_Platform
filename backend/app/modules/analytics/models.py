"""问数域 ORM（T-D01b 切片起步，T-D08 registry 化）。

- `MetricRegistry`：白名单指标目录落库（首批六类 + 切片指标）——metric_id 唯一、
  domain/status（active=口径已冻结可查；awaiting_caliber=待 B/C 交付口径，查询拒绝）、
  允许角色、允许参数键、缓存 TTL。registry 是查询准入的唯一依据。
- `AnalyticsQuerySnapshot`：查询快照（问题/指标/参数/数据时间/结果/来源/命中/操作人），
  同快照导出（T-D11）与反馈审计（T-D12）引用本表 id。
- `Nl2SqlAudit`：自由问数全链路审计（T-D09）——问题/生成 SQL/阶段/拒绝原因/行数/耗时。
- `MetricFeedback`：结果页「标记不准确」反馈（T-D12）——关联查询快照，一用户一快照一条；
  反馈不改当次结果、不自动改统计。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    BOOL_TYPE,
    CreatedAtMixin,
    UpdatedAtMixin,
)

METRIC_STATUS_ACTIVE = "active"
METRIC_STATUS_AWAITING = "awaiting_caliber"
METRIC_STATUSES = (METRIC_STATUS_ACTIVE, METRIC_STATUS_AWAITING)

#: 指标目录 v1（T-D00b/T-D08）：首批六类 + 切片指标。种子唯一来源（D_0004 迁移调用）。
#: status=awaiting_caliber 的卡（B/C 域）在口径交付前查询一律拒绝。
METRIC_CARDS: tuple[dict, ...] = (
    {
        "metric_id": "jobs.active",
        "name": "活跃岗位数",
        "domain": "A",
        "status": METRIC_STATUS_ACTIVE,
        "definition_ref": "docs/modules/A/指标口径交付.md §1（T-A53）",
        "allowed_roles": "admin,hr",
        "params_json": None,
        "cache_ttl": 300,
        "note": "T-D01b 切片指标",
    },
    {
        "metric_id": "job.demand_progress_gap",
        "name": "人才需求缺口",
        "domain": "A",
        "status": METRIC_STATUS_ACTIVE,
        "definition_ref": "docs/modules/A/指标口径交付.md v0.2 §7 + 指标目录 卡1",
        "allowed_roles": "admin,hr",
        "params_json": {"project_group_id": "int"},
        "cache_ttl": 300,
        "note": "公式按 A v0.2 复核：缺口=headcount−HR确认已录用，人工推进不混入（§12.3）",
    },
    {
        "metric_id": "match.threshold_reference",
        "name": "匹配参考达线",
        "domain": "A",
        "status": METRIC_STATUS_ACTIVE,
        "definition_ref": "指标目录 卡3（A 口径：score≥threshold，硬淘汰 score=0 排除；仅参考标签）",
        "allowed_roles": "admin,hr",
        "params_json": {"job_profile_id": "int"},
        "cache_ttl": 300,
        "note": None,
    },
    {
        "metric_id": "parse.success_failure",
        "name": "解析成功失败",
        "domain": "B",
        "status": METRIC_STATUS_AWAITING,
        "definition_ref": "指标目录 卡2（待 B 交付口径）",
        "allowed_roles": "admin,hr",
        "params_json": None,
        "cache_ttl": 300,
        "note": "B 契约 §6 已有草稿，字段终值确认后转 active",
    },
    {
        "metric_id": "exam.pass_rate",
        "name": "已完成测评通过率",
        "domain": "C",
        "status": METRIC_STATUS_AWAITING,
        "definition_ref": "指标目录 卡4（待 C 交付口径）",
        "allowed_roles": "admin,hr",
        "params_json": None,
        "cache_ttl": 300,
        "note": None,
    },
    {
        "metric_id": "training.formal_completion_rate",
        "name": "课程正式完成率",
        "domain": "C",
        "status": METRIC_STATUS_AWAITING,
        "definition_ref": "指标目录 卡5（待 C 交付口径）",
        "allowed_roles": "admin,hr",
        "params_json": None,
        "cache_ttl": 300,
        "note": "与学习自报进度分离，不混算",
    },
    {
        "metric_id": "learning.self_reported_progress",
        "name": "学习自报进度",
        "domain": "C",
        "status": METRIC_STATUS_AWAITING,
        "definition_ref": "指标目录 卡6（待 C 交付口径）",
        "allowed_roles": "admin,hr",
        "params_json": None,
        "cache_ttl": 300,
        "note": "单列口径，不与正式完成率混算",
    },
)


def seed_metric_registry(bind) -> int:
    """幂等种子：按 metric_id 判存在，缺则插（D_0004 迁移与测试共用）。返回新增数。"""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    inserted = 0
    with sessionmaker(bind=bind)() as db:
        for card in METRIC_CARDS:
            exists = db.execute(
                select(MetricRegistry.id).where(MetricRegistry.metric_id == card["metric_id"])
            ).first()
            if exists is None:
                db.add(MetricRegistry(**card))
                inserted += 1
        db.commit()
    return inserted


class MetricRegistry(PKMixin, UpdatedAtMixin, Base):
    """白名单指标目录（`metric_registry`）。"""

    __tablename__ = "metric_registry"
    __table_args__ = (
        UniqueConstraint("metric_id", name="uk_metric_registry_metric_id"),
        MYSQL_TABLE_OPTIONS,
    )

    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    domain: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=METRIC_STATUS_ACTIVE
    )
    definition_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    allowed_roles: Mapped[str] = mapped_column(String(64), nullable=False, default="admin,hr")
    params_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    cache_ttl: Mapped[int] = mapped_column(nullable=False, default=300)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AnalyticsQuerySnapshot(PKMixin, CreatedAtMixin, Base):
    """指标查询快照（`analytics_query_snapshots`）。"""

    __tablename__ = "analytics_query_snapshots"
    __table_args__ = (
        Index("idx_aqs_metric_time", "metric_id", "created_at"),
        Index("idx_aqs_user", "created_by"),
        MYSQL_TABLE_OPTIONS,
    )

    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    data_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")
    cache_hit: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class Nl2SqlAudit(PKMixin, CreatedAtMixin, Base):
    """自由问数审计（`nl2sql_audit`，T-D09）——每次尝试一行，全链路留痕。

    stage: generated → validated → executed；任一步失败/拒绝：
    refused_unmappable（生成器拒答）/ refused_unsafe（校验器拦截）/ timeout / error。
    """

    __tablename__ = "nl2sql_audit"
    __table_args__ = (
        Index("idx_nl2sql_audit_stage", "stage"),
        Index("idx_nl2sql_audit_user", "created_by"),
        MYSQL_TABLE_OPTIONS,
    )

    question: Mapped[str] = mapped_column(String(500), nullable=False)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    refused_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class MetricFeedback(PKMixin, CreatedAtMixin, Base):
    """问数反馈（`analytics_feedback`，T-D12）——结果页「标记不准确」。

    唯一约束 (snapshot_id, created_by)：同一用户对同一快照只留一条反馈（重复标记
    幂等返回已有行）。反馈不参与任何统计计算（T-D12 验收②）。
    """

    __tablename__ = "analytics_feedback"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "created_by", name="uk_feedback_snapshot_user"),
        Index("idx_feedback_snapshot", "snapshot_id"),
        Index("idx_feedback_user", "created_by"),
        MYSQL_TABLE_OPTIONS,
    )

    snapshot_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
