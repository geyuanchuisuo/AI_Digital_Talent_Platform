"""C 域迁移：课程名额上限（报名占位/候补/24h 超时释放）。

Revision ID: C_0006_course_capacity
Revises: C_0005_plan_item_due_date
Create Date: 2026-11-09

只加一列 `training_courses.capacity INT NULL`；NULL 表示报名不限名额。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0006_course_capacity"
down_revision: Union[str, None] = "C_0005_plan_item_due_date"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    # C_0001 用当前模型元数据 create_all，空库建表时已含本列；重复执行需幂等
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("training_courses")}
    if "capacity" in columns:
        return
    op.add_column(
        "training_courses",
        sa.Column("capacity", sa.Integer(), nullable=True, comment="报名名额上限，NULL 不限"),
    )


def downgrade() -> None:
    op.drop_column("training_courses", "capacity")
