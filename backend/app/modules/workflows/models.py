"""工作流流程数据模型 ORM（T-D14，D_0008 迁移，70 表基线）。

⚠️ 与 C 域 / engine models.py 同一纪律：定义 ORM 类、被 app/models/__init__.py
导入，**不要作为 import 入口**；使用方一律 `from app.models import WorkflowDefinition`。

结构（契约 docs/modules/D/工作流数据模型契约.md）：
- WorkflowDefinition          流程定义（code 唯一）
- WorkflowDefinitionVersion   定义版本（草稿→发布→废弃；发布后不可变 = 版本锁定）
- WorkflowNode / WorkflowEdge 版本内节点/连线（节点白名单 + 输入/输出引用落库）
- WorkflowRun                 业务运行实例（启动时**钉死 version_id**，不受新版本影响）
- WorkflowRunStep             节点步骤记录（run × node_key × attempt 唯一，支撑重试审计）

版本锁定口径：运行实例只关联 version_id；已发布版本的节点/连线由 service 层拒绝
修改（改版 = 新建草稿版本）；引擎侧 thread_id → workflow_engine_runs（T-D04）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    UpdatedAtMixin,
    JSONDict,
    JSONList,
)

# 节点白名单：五类任务节点（T-D17 落执行器，调各域授权服务）+ 人工审批节点。
# 白名单外一律拒绝入库（T-D14 验收②）。
NODE_TYPES: tuple[str, ...] = (
    "profile_parse",         # 档案解析
    "job_match",             # 岗位匹配
    "assessment_analysis",   # 测评分析
    "training_suggestion",   # 培训建议
    "data_analysis",         # 数据分析
    "human_approval",        # 人工审批（等待态不被恢复/心跳重置，T-D04 语义）
)

NODE_TYPE_LABELS: dict[str, str] = {
    "profile_parse": "档案解析",
    "job_match": "岗位匹配",
    "assessment_analysis": "测评分析",
    "training_suggestion": "培训建议",
    "data_analysis": "数据分析",
    "human_approval": "人工审批",
}

# 人工审批节点的出边分支（approve/reject 两路）；非审批节点不允许分支出边。
APPROVAL_BRANCHES: tuple[str, ...] = ("approve", "reject")

# 版本状态机（T-D15 受控生命周期）：draft →(submit)→ pending_review
# →(审核通过)→ published →(停用)→ deprecated；拒绝则退回 draft（附 review_note）。
# 回滚 = 目标版本重新成为唯一发布版（其余 published 一并停用）。
VERSION_STATUSES: tuple[str, ...] = ("draft", "pending_review", "published", "deprecated")

# 运行状态机与引擎登记（workflow_engine_runs）同口径：running → (waiting | succeeded |
# failed)；waiting 仅人工 resume 推进；cancelled 供管理面。本表为业务侧镜像。
RUN_STATUSES: tuple[str, ...] = ("running", "waiting", "succeeded", "failed", "cancelled")
RUN_TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "cancelled")

# 步骤状态：节点执行器（T-D17）逐节点落记录；skipped 供条件分支未走到时审计。
STEP_STATUSES: tuple[str, ...] = ("pending", "running", "waiting", "succeeded", "failed", "skipped")

# 触发类型：定时触发默认关闭（T-D18 验收④），仅登记枚举。
TRIGGER_TYPES: tuple[str, ...] = ("manual", "event", "schedule")

# 输入引用来源（T-D14 验收③，schema 见契约 §4）：
# trigger=触发负载 / form_field=表单字段 / node_output=上游节点输出 / literal=常量。
REF_KINDS: tuple[str, ...] = ("trigger", "form_field", "node_output", "literal")

NODE_KEY_MAX = 64


class WorkflowDefinition(PKMixin, UpdatedAtMixin, Base):
    """流程定义（`workflow_definitions`）。"""

    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("code", name="uk_workflow_definitions_code"),
        MYSQL_TABLE_OPTIONS,
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)


class WorkflowDefinitionVersion(PKMixin, UpdatedAtMixin, Base):
    """流程定义版本（`workflow_definition_versions`）。

    发布后节点/连线不可变（版本锁定）；改版一律新建更高版本号草稿。
    """

    __tablename__ = "workflow_definition_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uk_workflow_version"),
        Index("idx_wdv_definition_status", "definition_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    definition_id: Mapped[int] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(nullable=True)
    # T-D15 受控生命周期：提交与审核分离（四眼原则——提交人不可自行发布）
    submitted_by: Mapped[int | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_by: Mapped[int | None] = mapped_column(nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowNode(PKMixin, UpdatedAtMixin, Base):
    """版本内节点（`workflow_nodes`）。

    inputs_json/outputs_json 落输入/输出引用（验收③）：inputs 为
    [{as, from:{kind,...}}]，outputs 为声明 [{as, type?, desc?}]。
    """

    __tablename__ = "workflow_nodes"
    __table_args__ = (
        UniqueConstraint("version_id", "node_key", name="uk_workflow_node_key"),
        Index("idx_workflow_nodes_version", "version_id"),
        MYSQL_TABLE_OPTIONS,
    )

    version_id: Mapped[int] = mapped_column(nullable=False)
    node_key: Mapped[str] = mapped_column(String(NODE_KEY_MAX), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    position_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    inputs_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    outputs_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)


class WorkflowEdge(PKMixin, UpdatedAtMixin, Base):
    """版本内连线（`workflow_edges`）。

    branch 仅人工审批节点出边使用（approve/reject）；其余节点出边 branch 为空串。
    """

    __tablename__ = "workflow_edges"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "source_node_key", "target_node_key", "branch",
            name="uk_workflow_edge",
        ),
        Index("idx_workflow_edges_version", "version_id"),
        MYSQL_TABLE_OPTIONS,
    )

    version_id: Mapped[int] = mapped_column(nullable=False)
    source_node_key: Mapped[str] = mapped_column(String(NODE_KEY_MAX), nullable=False)
    target_node_key: Mapped[str] = mapped_column(String(NODE_KEY_MAX), nullable=False)
    branch: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    condition_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)


class WorkflowRun(PKMixin, UpdatedAtMixin, Base):
    """业务运行实例（`workflow_runs`）。

    version_id 在 start 时钉死（版本锁定）；thread_id 唯一，对应引擎登记
    workflow_engine_runs（T-D04）与检查点。status 为业务侧镜像，由 service
    sync_run_status 随引擎转移维护。
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uk_workflow_runs_thread"),
        Index("idx_workflow_runs_status", "status"),
        Index("idx_workflow_runs_definition", "definition_id"),
        MYSQL_TABLE_OPTIONS,
    )

    definition_id: Mapped[int] = mapped_column(nullable=False)
    version_id: Mapped[int] = mapped_column(nullable=False)
    thread_id: Mapped[str] = mapped_column(String(191), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    trigger_ref: Mapped[str | None] = mapped_column(String(191), nullable=True)
    params_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowRunStep(PKMixin, UpdatedAtMixin, Base):
    """节点步骤记录（`workflow_run_steps`）。

    (run_id, node_key, attempt) 唯一：同一次尝试重放幂等覆盖，重试记新 attempt
    （T-D18「失败阶段重试不重复生成已成功产物」的审计依据）。
    """

    __tablename__ = "workflow_run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "node_key", "attempt", name="uk_workflow_step_attempt"),
        MYSQL_TABLE_OPTIONS,
    )

    run_id: Mapped[int] = mapped_column(nullable=False)
    node_key: Mapped[str] = mapped_column(String(NODE_KEY_MAX), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    output_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
