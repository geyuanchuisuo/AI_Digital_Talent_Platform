"""C 自适应测评：方案策略/题库快照、任务出题字段、选题留痕表与能力分析字段。

Revision ID: C_0002_adaptive_assessment
Revises: C_0001_c_module
Create Date: 2026-11-07

依赖 A 维护的 C_0001_c_module 基线链；本地无 MySQL 环境，迁移链未实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "C_0002_adaptive_assessment"
down_revision: Union[str, None] = "C_0001_c_module"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.add_column(
        "assessment_plans",
        sa.Column(
            "mode", sa.String(length=20), nullable=False, server_default=sa.text("'adaptive'")
        ),
    )
    op.add_column("assessment_plans", sa.Column("strategy_json", sa.JSON(), nullable=True))
    op.add_column("assessment_plans", sa.Column("bank_snapshot_json", sa.JSON(), nullable=True))

    op.alter_column(
        "assessment_assignments",
        "paper_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "assessment_assignments",
        sa.Column(
            "mode", sa.String(length=20), nullable=False, server_default=sa.text("'adaptive'")
        ),
    )
    op.add_column(
        "assessment_assignments", sa.Column("strategy_snapshot_json", sa.JSON(), nullable=True)
    )
    op.add_column(
        "assessment_assignments", sa.Column("target_question_count", sa.Integer(), nullable=True)
    )

    op.create_table(
        "assessment_selections",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("dimension_name", sa.String(length=100), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("question_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("selection_reason_json", sa.JSON(), nullable=False),
        sa.Column("strategy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", "sequence_no", name="uk_selection_sequence"),
        sa.UniqueConstraint("assignment_id", "question_id", name="uk_selection_question"),
        **TABLE_OPTIONS,
    )

    op.add_column("assessment_results", sa.Column("ability_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessment_results", "ability_json")
    op.drop_table("assessment_selections")
    op.drop_column("assessment_assignments", "target_question_count")
    op.drop_column("assessment_assignments", "strategy_snapshot_json")
    op.drop_column("assessment_assignments", "mode")
    op.alter_column(
        "assessment_assignments",
        "paper_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("assessment_plans", "bank_snapshot_json")
    op.drop_column("assessment_plans", "strategy_json")
    op.drop_column("assessment_plans", "mode")
