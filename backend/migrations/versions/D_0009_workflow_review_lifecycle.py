"""workflow_definition_versions: review lifecycle columns (T-D15).

Revision ID: D_0009_workflow_review
Revises: D_0008_workflow_models
Create Date: 2026-11-08

Adds nullable review-lifecycle columns to workflow_definition_versions:
submitted_by/submitted_at (submitter identity, four-eyes separation from
reviewer) and review_note (approve/reject opinion). No new tables (70-table
ORM baseline unchanged). IDEMPOTENT (inspector checks, same pattern as
A_0003) because fresh databases build from the live model snapshot which
already has these columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "D_0009_workflow_review"
down_revision = "D_0008_workflow_models"
branch_labels = None
depends_on = None

NEW_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("workflow_definition_versions", "submitted_by", mysql.BIGINT(unsigned=True)),
    ("workflow_definition_versions", "submitted_at", sa.DateTime()),
    ("workflow_definition_versions", "review_note", sa.String(500)),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table, column, col_type in NEW_COLUMNS:
        existing = {c["name"] for c in insp.get_columns(table)}
        if column not in existing:
            op.add_column(table, sa.Column(column, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table, column, _col_type in reversed(NEW_COLUMNS):
        existing = {c["name"] for c in insp.get_columns(table)}
        if column in existing:
            op.drop_column(table, column)
