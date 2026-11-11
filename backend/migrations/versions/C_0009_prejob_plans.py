"""C 域迁移：岗前培养计划（分阶段 phases + 课程 + 导师，版本化）（F05 / 跟踪表 C12）。

Revision ID: C_0009_prejob_plans
Revises: C_0008_grid_config（挂当前单头，保持线性）
Create Date: 2026-11-11

新增三张表：
- `prejob_plans`：面向新入职/待上岗人员的可编辑分阶段培养计划（目标、版本、状态）。
- `prejob_phases`：阶段快照，绑定当时课程与导师；改课程不回写历史版本。
- `prejob_plan_versions`：版本快照，修订时保留历史。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0009_prejob_plans"
down_revision: Union[str, None] = "C_0008_grid_config"
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
    if "prejob_plans" not in tables:
        op.create_table(
            "prejob_plans",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("employee_user_id", sa.BigInteger(), nullable=False),
            sa.Column("talent_profile_id", sa.BigInteger(), nullable=True),
            sa.Column("target_job_title", sa.String(length=100), nullable=True),
            sa.Column("goal", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("input_snapshot_json", sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            **TABLE_OPTIONS,
        )
    if "prejob_phases" not in tables:
        op.create_table(
            "prejob_phases",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("plan_id", sa.BigInteger(), nullable=False),
            sa.Column("plan_version", sa.Integer(), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("goal", sa.Text(), nullable=True),
            sa.Column("mentor_user_id", sa.BigInteger(), nullable=True),
            sa.Column("courses_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("plan_id", "plan_version", "seq", name="uk_prejob_phase_order"),
            **TABLE_OPTIONS,
        )
    if "prejob_plan_versions" not in tables:
        op.create_table(
            "prejob_plan_versions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("plan_id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("snapshot_json", sa.JSON(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            *_ts_columns(),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("plan_id", "version", name="uk_prejob_plan_version"),
            **TABLE_OPTIONS,
        )


def downgrade() -> None:
    op.drop_table("prejob_plan_versions")
    op.drop_table("prejob_phases")
    op.drop_table("prejob_plans")
