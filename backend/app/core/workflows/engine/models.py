"""引擎运行实例 ORM（T-D04，D_0002 迁移）。

⚠️ 本模块与 C 域 models.py 同一纪律：定义 ORM 类、被 app/models/__init__.py
导入，**不要作为 import 入口**（会经 app.models.base 触发父包初始化形成环）；
使用方一律 `from app.models import WorkflowEngineRun`。

状态机：running → (waiting | succeeded | failed)；
waiting →（人工 resume 后）→ succeeded/failed；waiting **不参与任何自动恢复**。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import MYSQL_TABLE_OPTIONS, PKMixin, UpdatedAtMixin

ENGINE_RUN_STATUSES: tuple[str, ...] = ("running", "waiting", "succeeded", "failed", "cancelled")

# 自动恢复允许触碰的状态：孤儿 running（进程被杀没来得及写终态）置 failed 可重试。
# waiting 是人工等待/审批——**明确排除**，恢复/心跳/重启都不得重置（T-D04 验收②）。
RECOVERABLE_STATUSES: tuple[str, ...] = ("running",)


class WorkflowEngineRun(PKMixin, UpdatedAtMixin, Base):
    """引擎运行实例（`workflow_engine_runs`）。"""

    __tablename__ = "workflow_engine_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uk_workflow_engine_runs_thread"),
        Index("idx_workflow_engine_runs_status", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    graph_name: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(191), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
