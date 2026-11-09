"""snapshot_export_audit: report export audit (T-D11).

Revision ID: D_0007_export_audit
Revises: D_0006_analytics_feedback
Create Date: 2026-11-08

One row per export: who / when / format / which snapshot. Export re-checks
permission and NEVER re-queries metrics (same-snapshot guarantee).
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0007_export_audit"
down_revision = "D_0006_analytics_feedback"
branch_labels = None
depends_on = None

TABLES = ("snapshot_export_audit",)

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
