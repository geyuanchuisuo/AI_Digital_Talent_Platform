"""C 域迁移：培训计划项到期日（F09 必修到期/逾期/延期）。

Revision ID: C_0005_plan_item_due_date
Revises: C_0004_application_fk
Create Date: 2026-11-09

只加一列 `training_plan_items.due_date DATE NULL`，用于到期提醒、逾期判定与延期决策。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0005_plan_item_due_date"
down_revision: Union[str, None] = "C_0004_application_fk"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    # C_0001 用当前模型元数据 create_all，空库建表时已含本列；重复执行需幂等
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("training_plan_items")}
    if "due_date" in columns:
        return
    op.add_column(
        "training_plan_items",
        sa.Column("due_date", sa.Date(), nullable=True, comment="必修到期日"),
    )


def downgrade() -> None:
    op.drop_column("training_plan_items", "due_date")
