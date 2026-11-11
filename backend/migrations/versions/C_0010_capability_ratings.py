"""C 域迁移：正式能力评级与变更链（证据 + 经理评价 → HR 审核 → 新评级版本）（F05 / 跟踪表 C14）。

Revision ID: C_0010_capability_ratings
Revises: C_0009_prejob_plans（挂当前单头，保持线性）
Create Date: 2026-11-11

新增两张表：
- `capability_ratings`：按维度的正式能力评级，追加版本、历史不覆盖；仅由 HR 审核通过的变更生成。
- `capability_rating_changes`：变更申请链（实践成果证据 + 经理评价 + HR 审核状态）。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0010_capability_ratings"
down_revision: Union[str, None] = "C_0009_prejob_plans"
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


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "capability_ratings" not in tables:
        op.create_table(
            "capability_ratings",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("employee_user_id", sa.BigInteger(), nullable=False),
            sa.Column("talent_profile_id", sa.BigInteger(), nullable=True),
            sa.Column("dimension_name", sa.String(length=100), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("effective", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
            sa.Column("source_change_id", sa.BigInteger(), nullable=True),
            sa.Column("basis_json", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "employee_user_id", "talent_profile_id", "dimension_name", "version",
                name="uk_capability_rating_dim_version",
            ),
            **TABLE_OPTIONS,
        )
    if "capability_rating_changes" not in tables:
        op.create_table(
            "capability_rating_changes",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("employee_user_id", sa.BigInteger(), nullable=False),
            sa.Column("talent_profile_id", sa.BigInteger(), nullable=True),
            sa.Column("dimension_name", sa.String(length=100), nullable=False),
            sa.Column("evidence_json", sa.JSON(), nullable=False),
            sa.Column("manager_user_id", sa.BigInteger(), nullable=True),
            sa.Column("manager_level", sa.Integer(), nullable=True),
            sa.Column("manager_comment", sa.Text(), nullable=True),
            sa.Column("hr_user_id", sa.BigInteger(), nullable=True),
            sa.Column("hr_level", sa.Integer(), nullable=True),
            sa.Column("hr_comment", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending_hr"),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            **TABLE_OPTIONS,
        )


def downgrade() -> None:
    op.drop_table("capability_rating_changes")
    op.drop_table("capability_ratings")
