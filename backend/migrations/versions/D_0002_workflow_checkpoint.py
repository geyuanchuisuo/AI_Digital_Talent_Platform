"""workflow engine: checkpoint tables + run registry (T-D04).

Revision ID: D_0002_workflow_checkpoint
Revises: D_0001_notifications
Create Date: 2026-11-07

- workflow_checkpoints / workflow_checkpoint_writes: LangGraph checkpointer
  storage, managed by SQLAlchemyCheckpointSaver.setup() (Core-level, idempotent;
  NOT part of the ORM baseline — engine-internal structure).
- workflow_engine_runs: engine run registry (ORM model, part of the 53-table
  baseline). recover marks orphan running -> failed, NEVER touches waiting.

IDEMPOTENT (baseline design constraint, same as A_0007).
"""
from __future__ import annotations

from alembic import op

revision = "D_0002_workflow_checkpoint"
down_revision = "D_0001_notifications"
branch_labels = None
depends_on = None

ORM_TABLES = ("workflow_engine_runs",)

def upgrade() -> None:
    from app.core.workflows.engine.saver import SQLAlchemyCheckpointSaver
    from app.database import Base

    bind = op.get_bind()
    # 1) 引擎登记表（ORM 基线内）
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in ORM_TABLES],
        checkfirst=True,
    )
    # 2) 检查点表（Core 层，幂等；生产在 MySQL 库内，与业务表同库不同前缀）
    SQLAlchemyCheckpointSaver(bind).setup()

def downgrade() -> None:
    from app.core.workflows.engine.saver import _METADATA_TABLE, _WRITES_TABLE
    from app.database import Base

    bind = op.get_bind()
    _METADATA_TABLE.metadata.drop_all(
        bind, tables=[_METADATA_TABLE, _WRITES_TABLE], checkfirst=True
    )
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(ORM_TABLES)],
        checkfirst=True,
    )
