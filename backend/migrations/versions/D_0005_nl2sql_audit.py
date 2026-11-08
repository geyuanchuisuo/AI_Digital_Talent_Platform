"""nl2sql_audit: free-form NL2SQL audit trail (T-D09).

Revision ID: D_0005_nl2sql_audit
Revises: D_0004_metric_registry
Create Date: 2026-11-08

One row per attempt: question / generated SQL / stage / refusal reason /
row_count / duration_ms. Full-chain audit for the controlled-execution path.
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0005_nl2sql_audit"
down_revision = "D_0004_metric_registry"
branch_labels = None
depends_on = None

TABLES = ("nl2sql_audit",)

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
