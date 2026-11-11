"""C 域迁移：报名职责审批链（C06）+ 防挂机学习会话与按课程完成条件（C08）+ 盘点经理初评留痕（C09）。

Revision ID: C_0011_enrollment_chain_study_session
Revises: C_0010_capability_ratings（挂当前单头，保持线性）
Create Date: 2026-11-11

改动：
- `training_courses`：approval_mode（none/manager/admin）、has_cost、occupies_work_time、
  completion_rules_json（按课程正式完成条件）。
- `training_enrollments`：manager_decided_by / manager_decided_at（直属经理审批留痕）。
- `training_study_sessions`：服务端学习会话，有效学时按心跳累计（防挂机），nonce 唯一、结算置 consumed。
- `inventory_reviews`：manager_comment（经理初评意见留痕）。
全部 IDEMPOTENT（inspector 检查）：空库由 Base.metadata.create_all 建好， legacy 库由本 ALTER 补齐。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/MASTER）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0011_enrollment_chain_study_session"
down_revision: Union[str, None] = "C_0010_capability_ratings"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    ]


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_columns(
        "training_courses",
        [
            sa.Column("approval_mode", sa.String(length=20), nullable=False, server_default="admin"),
            sa.Column("has_cost", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("occupies_work_time", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("completion_rules_json", sa.JSON(), nullable=True),
        ],
    )
    _add_columns(
        "training_enrollments",
        [
            sa.Column("manager_decided_by", sa.BigInteger(), nullable=True),
            sa.Column("manager_decided_at", sa.DateTime(), nullable=True),
        ],
    )
    _add_columns(
        "inventory_reviews",
        [sa.Column("manager_comment", sa.Text(), nullable=True)],
    )

    inspector = sa.inspect(op.get_bind())
    if "training_study_sessions" not in set(inspector.get_table_names()):
        op.create_table(
            "training_study_sessions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("learning_record_id", sa.Integer(), nullable=False),
            sa.Column("employee_user_id", sa.Integer(), nullable=False),
            sa.Column("nonce", sa.String(length=32), nullable=False),
            sa.Column("issued_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("active_minutes", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("consumed", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("nonce", name="uk_study_session_nonce"),
            **TABLE_OPTIONS,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "training_study_sessions" in tables:
        op.drop_table("training_study_sessions")

    def _drop_columns(table: str, names: list[str]) -> None:
        if table not in tables:
            return
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name in names:
            if name in existing:
                op.drop_column(table, name)

    _drop_columns("inventory_reviews", ["manager_comment"])
    _drop_columns("training_enrollments", ["manager_decided_at", "manager_decided_by"])
    _drop_columns(
        "training_courses",
        ["completion_rules_json", "occupies_work_time", "has_cost", "approval_mode"],
    )