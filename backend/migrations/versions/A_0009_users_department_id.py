"""users_department_id: add nullable department_id for dept row-level scope (A06-2).

Revision ID: A_0009_users_department_id
Revises: D_0012_digital_employee (current head of the unified chain)
Create Date: 2026-11-10

Adds users.department_id (nullable BIGINT UNSIGNED, no FK — consistency is
enforced by the service layer per system/models.py convention). IDEMPOTENT via
inspector check: a fresh empty database already gets this column from
Base.metadata (create_all), while a legacy upgraded database gets it via this
ALTER. Do NOT replace inspector checks with a blind ALTER.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "A_0009_users_department_id"
down_revision = "D_0012_digital_employee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "department_id" not in user_cols:
        op.add_column(
            "users",
            sa.Column("department_id", mysql.BIGINT(unsigned=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "department_id" in user_cols:
        op.drop_column("users", "department_id")
