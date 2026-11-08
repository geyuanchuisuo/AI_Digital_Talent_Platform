"""analytics_feedback: metric result feedback (T-D12).

Revision ID: D_0006_analytics_feedback
Revises: D_0005_nl2sql_audit
Create Date: 2026-11-08

One row per (snapshot, user): the "mark as inaccurate" entry on the result
page. Feedback never affects metric computation (T-D12 acceptance ②).
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0006_analytics_feedback"
down_revision = "D_0005_nl2sql_audit"
branch_labels = None
depends_on = None

TABLES = ("analytics_feedback",)

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
