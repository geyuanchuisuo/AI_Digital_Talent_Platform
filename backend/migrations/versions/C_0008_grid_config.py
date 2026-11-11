"""C 域迁移：九宫格标准配置版本 + 复核记录所依据配置版本（F05a / 跟踪表 C09）。

Revision ID: C_0008_grid_config
Revises: A_0013_demand_approval（2026-11-11 挂当前单头，保持线性）
Create Date: 2026-11-11

新增 `inventory_grid_configs`：五档阈值/象限/标签经 HR 批准（status=approved）
后方可产出正式盘点评级，未批准不套示意数值。`inventory_reviews.grid_config_version`
记录确认当时所依据的配置版本，历史评级不随后续配置改动重算。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0008_grid_config"
down_revision: Union[str, None] = "A_0013_demand_approval"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "inventory_grid_configs" not in tables:
        op.create_table(
            "inventory_grid_configs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("job_family", sa.String(length=100), nullable=True),
            sa.Column("performance_bands_json", sa.JSON(), nullable=False),
            sa.Column("potential_bands_json", sa.JSON(), nullable=False),
            sa.Column("labels_json", sa.JSON(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            **TABLE_OPTIONS,
        )
    columns = {col["name"] for col in inspector.get_columns("inventory_reviews")}
    if "grid_config_version" not in columns:
        op.add_column(
            "inventory_reviews",
            sa.Column(
                "grid_config_version",
                sa.Integer(),
                nullable=True,
                comment="确认时所依据的九宫格标准配置版本；无经批准配置为 NULL（待评估）",
            ),
        )


def downgrade() -> None:
    op.drop_column("inventory_reviews", "grid_config_version")
    op.drop_table("inventory_grid_configs")
