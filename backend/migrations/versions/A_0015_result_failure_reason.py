"""result_failure_reason: per-result failure tracking for retry (T-A42).

Revision ID: A_0015_failure_reason
Revises: D_0013_nl2sql_audit_fields
Create Date: 2026-11-09

Adds nullable failure_reason TEXT to screen_results and match_results.
Non-null marks a failed per-result row that retry re-runs; successful rows
are never re-processed (D-E snapshot semantics).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "A_0015_failure_reason"
down_revision = "D_0013_nl2sql_audit_fields"
branch_labels = None
depends_on = None

NEW_COLUMNS = (
    ("screen_results", "failure_reason"),
    ("match_results", "failure_reason"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in NEW_COLUMNS:
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if column not in existing:
            op.add_column(table, sa.Column(column, mysql.TEXT(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in reversed(NEW_COLUMNS):
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if column in existing:
            op.drop_column(table, column)
