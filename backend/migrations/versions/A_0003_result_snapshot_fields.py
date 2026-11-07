"""result_snapshot_fields: snapshot/version/attempt columns + match manual-verdict alignment.

Revision ID: A_0003_snapshot
Revises: A_0002_users_person
Create Date: 2026-11-07

T-A33/T-A43 (feature 5b): adds nullable snapshot/version columns to
screen_results and match_results, and aligns match_results manual-verdict
columns with screen_results. IDEMPOTENT (inspector checks) because the
baseline builds tables from the LIVE model snapshot - fresh databases already
have these columns from A_0001. See A_0002 for the same design constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "A_0003_snapshot"
down_revision = "A_0002_users_person"
branch_labels = None
depends_on = None

# (table, column, sa_type)
NEW_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("screen_results", "model_version", sa.String(50)),
    ("screen_results", "prompt_version", sa.String(20)),
    ("screen_results", "attempt", mysql.INTEGER(unsigned=True)),
    ("screen_results", "profile_input_snapshot_json", mysql.JSON),
    ("match_results", "model_version", sa.String(50)),
    ("match_results", "prompt_version", sa.String(20)),
    ("match_results", "attempt", mysql.INTEGER(unsigned=True)),
    ("match_results", "profile_input_snapshot_json", mysql.JSON),
    ("match_results", "manual_verdict", sa.String(20)),
    ("match_results", "manual_verdict_by", mysql.BIGINT(unsigned=True)),
    ("match_results", "manual_verdict_at", sa.DateTime()),
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
