"""workflow_definitions/versions/nodes/edges/runs/steps: 流程数据模型 (T-D14).

Revision ID: D_0008_workflow_models
Revises: D_0007_export_audit
Create Date: 2026-11-08

Six tables for F08 controlled agent workflows: definition -> version (draft/
published/deprecated, immutable once published = version locking) -> nodes
(whitelist: 5 task types + human approval) / edges (approval branch rules) /
business runs (pinned version_id) / run steps (run x node_key x attempt unique).
Engine checkpoint tables (workflow_checkpoints*) and engine registry
(workflow_engine_runs, D_0002) are NOT touched here.
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0008_workflow_models"
down_revision = "D_0007_export_audit"
branch_labels = None
depends_on = None

TABLES = (
    "workflow_definitions",
    "workflow_definition_versions",
    "workflow_nodes",
    "workflow_edges",
    "workflow_runs",
    "workflow_run_steps",
)

def upgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in TABLES],
        checkfirst=True,
    )

def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(TABLES)],
    )
