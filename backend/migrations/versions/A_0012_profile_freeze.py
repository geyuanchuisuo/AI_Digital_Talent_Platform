"""profile_freeze: frozen batch-start profile inputs (A03 / F03).

Revision ID: A_0012_profile_freeze
Revises: A_0011_app_snapshot (current head of the unified chain)
Create Date: 2026-11-10

Adds nullable ``frozen_inputs_json`` to ``screen_batches`` and ``match_batches``.
The value is written ONCE when a batch is created: a value copy of each member's
scoring input plus the immutable B profile version/content binding. Workers and
retries read only this column; edits to talent_profiles / parsed_resumes while a
batch is queued must never change its results, and history reads must not pick up
a newer profile version (requirements F03, MASTER tracking table A03).

Nullable so batches created before this migration stay valid; those legacy rows
fall back (with a warning) to the previous current-profile read path.
IDEMPOTENT via inspector checks: a fresh empty database already gets the columns
from ``Base.metadata`` (``create_all``), while a legacy upgraded database gets
them via ALTER. Do NOT replace the inspector checks with a blind ALTER.

NOTE ON ID LENGTH: this repository's ``alembic_version.version_num`` is
VARCHAR(32) (MySQL). Revision IDs MUST be <= 32 chars.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "A_0012_profile_freeze"
down_revision = "A_0011_app_snapshot"
branch_labels = None
depends_on = None

_TABLES = ("screen_batches", "match_batches")
_COLUMN = "frozen_inputs_json"


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table in _TABLES:
        existing = {column["name"] for column in insp.get_columns(table)}
        if _COLUMN not in existing:
            op.add_column(table, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table in _TABLES:
        existing = {column["name"] for column in insp.get_columns(table)}
        if _COLUMN in existing:
            op.drop_column(table, _COLUMN)
