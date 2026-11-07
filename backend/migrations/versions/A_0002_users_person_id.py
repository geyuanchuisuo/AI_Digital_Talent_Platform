"""users_person_id: add nullable person_id binding column with unique constraint (idempotent).

Revision ID: A_0002_users_person
Revises: C_0001_c_module
Create Date: 2026-11-07

Adds users.person_id (B contract 2026-11-07: reuse talent_profiles.id, same
PK_TYPE BIGINT UNSIGNED, nullable for admin accounts, unique for personal
accounts). This is the chain's first real ALTER; it is IDEMPOTENT because the
baseline migration builds tables from the LIVE model snapshot - a fresh empty
database already gets this column from A_0001, while legacy 13-table databases
(upgraded via stamp A_0001) do not. The inspector check makes both paths work
with the same chain. Do NOT replace inspector checks with blind ALTER here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "A_0002_users_person"
down_revision = "C_0001_c_module"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "person_id" not in user_cols:
        op.add_column(
            "users",
            sa.Column("person_id", mysql.BIGINT(unsigned=True), nullable=True),
        )
    existing_ucs = {
        uc["name"]
        for uc in insp.get_unique_constraints("users")
        if uc.get("name")
    }
    if "uk_user_person_id" not in existing_ucs:
        op.create_unique_constraint("uk_user_person_id", "users", ["person_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    existing_ucs = {
        uc["name"]
        for uc in insp.get_unique_constraints("users")
        if uc.get("name")
    }
    if "uk_user_person_id" in existing_ucs:
        op.drop_constraint("uk_user_person_id", "users", type_="unique")
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "person_id" in user_cols:
        op.drop_column("users", "person_id")
