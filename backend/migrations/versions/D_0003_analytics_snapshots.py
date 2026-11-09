"""analytics_query_snapshots: metric query snapshots (T-D01b vertical slice).

Revision ID: D_0003_analytics_snapshots
Revises: D_0002_workflow_checkpoint
Create Date: 2026-11-07

Snapshot row per metric computation (metric/params/data_time/result/source/
cache_hit/created_by) — audit trail and same-snapshot export base (T-D11/T-D12
consume the id). IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0003_analytics_snapshots"
down_revision = "D_0002_workflow_checkpoint"
branch_labels = None
depends_on = None

TABLES = ("analytics_query_snapshots",)

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
