"""batch_idempotency: request-level idempotency key for screen/match batches (T-A42).

Revision ID: A_0010_batch_idempotency
Revises: A_0009_users_department_id (current head of the unified chain)
Create Date: 2026-11-10

Adds a nullable ``idempotency_key`` column plus a composite UNIQUE constraint on
``(job_profile_id, idempotency_key)`` to ``screen_batches`` and ``match_batches``.
NULL keys are unlimited in MySQL, so batches created without a key keep the old
behaviour (every request starts a new round when the job/profile changes).

IDEMPOTENT via inspector checks on both columns and unique constraints: a fresh
empty database already gets the column + constraint from ``Base.metadata``
(``create_all``), while a legacy upgraded database gets them via ALTER. Do NOT
replace the inspector checks with a blind ALTER / CREATE.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "A_0010_batch_idempotency"
down_revision = "A_0009_users_department_id"
branch_labels = None
depends_on = None

_TABLES = (
    ("screen_batches", "uk_screen_batch_idem"),
    ("match_batches", "uk_match_batch_idem"),
)


def _existing_constraint_names(insp: Inspector, table: str) -> set[str]:
    """MySQL surfaces UNIQUE constraints via either reflection API depending on
    the server/driver version, so check both before creating."""
    names = {item["name"] for item in insp.get_unique_constraints(table)}
    names.update(item["name"] for item in insp.get_indexes(table))
    return {name for name in names if name}


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table, constraint_name in _TABLES:
        columns = {column["name"] for column in insp.get_columns(table)}
        if "idempotency_key" not in columns:
            op.add_column(
                table, sa.Column("idempotency_key", sa.String(64), nullable=True)
            )
        if constraint_name not in _existing_constraint_names(insp, table):
            op.create_unique_constraint(
                constraint_name, table, ["job_profile_id", "idempotency_key"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for table, constraint_name in _TABLES:
        if constraint_name in _existing_constraint_names(insp, table):
            op.drop_constraint(constraint_name, table, type_="unique")
        columns = {column["name"] for column in insp.get_columns(table)}
        if "idempotency_key" in columns:
            op.drop_column(table, "idempotency_key")
