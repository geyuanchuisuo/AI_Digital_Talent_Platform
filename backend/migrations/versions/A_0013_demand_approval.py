"""demand_approval: A11 demand approval chain and immutable audit records.

Revision ID: A_0013_demand_approval
Revises: A_0012_profile_freeze (current head of the unified chain)
Create Date: 2026-11-10

Implements MASTER tracking table A11 / requirements section 6.11 step 1:
HR submits a project talent demand, a business owner approves it, and every
transition is recorded with the frozen demand version it applied to.

Adds five nullable/defaulted columns to project_demands (approval_status,
approval_version, approved_version, approved_by, approved_at) and creates
demand_approval_records (append-only audit trail with an A11-v1 value-copy
snapshot). Business lifecycle status and business approval status are kept
separate on purpose: status flow must never be mistaken for an approval.

IDEMPOTENT via inspector checks: a fresh empty database already gets all of
this from Base.metadata (create_all), while a legacy upgraded database gets
them via ALTER / CREATE TABLE. Do NOT replace the inspector checks with blind
DDL.

NOTE ON ID LENGTH: this repository's alembic_version.version_num is VARCHAR(32)
(MySQL). Revision IDs MUST be <= 32 chars.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "A_0013_demand_approval"
down_revision = "A_0012_profile_freeze"
branch_labels = None
depends_on = None

_TABLE = "project_demands"
_RECORD_TABLE = "demand_approval_records"
_UINT = mysql.BIGINT(unsigned=True)

_COLUMNS = {
    "approval_status": sa.Column(
        "approval_status", sa.String(20), nullable=False, server_default=sa.text("'draft'")
    ),
    "approval_version": sa.Column(
        "approval_version", sa.Integer(), nullable=False, server_default=sa.text("1")
    ),
    "approved_version": sa.Column("approved_version", sa.Integer(), nullable=True),
    "approved_by": sa.Column("approved_by", _UINT, nullable=True),
    "approved_at": sa.Column("approved_at", sa.DateTime(), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    existing = {c["name"] for c in insp.get_columns(_TABLE)}
    for name, column in _COLUMNS.items():
        if name not in existing:
            op.add_column(_TABLE, column)

    if not insp.has_table(_RECORD_TABLE):
        op.create_table(
            _RECORD_TABLE,
            sa.Column("id", _UINT, primary_key=True, autoincrement=True),
            sa.Column("demand_id", _UINT, nullable=False),
            sa.Column("demand_version", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("from_status", sa.String(20), nullable=False),
            sa.Column("to_status", sa.String(20), nullable=False),
            sa.Column("actor_user_id", _UINT, nullable=False),
            sa.Column("actor_roles", sa.String(150), nullable=False),
            sa.Column("reason", sa.String(255), nullable=True),
            sa.Column("snapshot_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Index("idx_demand_approval_demand", "demand_id", "created_at"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    if insp.has_table(_RECORD_TABLE):
        op.drop_table(_RECORD_TABLE)
    existing = {c["name"] for c in insp.get_columns(_TABLE)}
    for name in reversed(list(_COLUMNS)):
        if name in existing:
            op.drop_column(_TABLE, name)
