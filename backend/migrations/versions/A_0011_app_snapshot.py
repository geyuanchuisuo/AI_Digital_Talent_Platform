"""app_snapshot: frozen application-time inputs (A08 / V2.6 F01).

Revision ID: A_0011_app_snapshot
Revises: A_0010_batch_idempotency (current head of the unified chain)
Create Date: 2026-11-10

Adds nullable ``input_snapshot_json`` / ``input_snapshot_at`` to ``applications``.
The snapshot is a **value copy** of the job/JD conditions, talent profile, resume
file and parsed resume captured once when the application is created; later edits
to any source row must never rewrite it (requirements section 73 / 201 / 280).

Both columns are nullable so applications created before this migration stay
valid and are reported as ``available=false`` by the read-only snapshot endpoint.
IDEMPOTENT via inspector checks: a fresh empty database already gets the columns
from ``Base.metadata`` (``create_all``), while a legacy upgraded database gets
them via ALTER. Do NOT replace the inspector checks with a blind ALTER.

NOTE ON ID LENGTH: this repository's ``alembic_version.version_num`` is
VARCHAR(32) (MySQL). Revision IDs MUST be <= 32 chars; the originally proposed
``A_0011_application_input_snapshot`` (36 chars) applied its DDL but failed with
``DataError 1406 Data too long for column 'version_num'``. Keep the short id.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "A_0011_app_snapshot"
down_revision = "A_0010_batch_idempotency"
branch_labels = None
depends_on = None

_TABLE = "applications"
_COLUMNS = {
    "input_snapshot_json": sa.JSON(),
    "input_snapshot_at": sa.DateTime(),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    existing = {column["name"] for column in insp.get_columns(_TABLE)}
    for name, type_ in _COLUMNS.items():
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    existing = {column["name"] for column in insp.get_columns(_TABLE)}
    for name in reversed(list(_COLUMNS)):
        if name in existing:
            op.drop_column(_TABLE, name)
