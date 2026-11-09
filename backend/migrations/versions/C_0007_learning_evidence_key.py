"""C 域迁移：学习证据可信来源键（防重复计入/凭证重放）。

Revision ID: C_0007_learning_evidence_key
Revises: C_0006_course_capacity
Create Date: 2026-11-09

新增 `training_learning_evidence.evidence_key`，同一学习记录内唯一；
历史行按 `legacy:<id>` 回填，历史数据不再计入正式完成（verified_by 不在可信集合）。
本地无 MySQL 环境，升级/回滚需在 A 的集成环境实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0007_learning_evidence_key"
down_revision: Union[str, None] = "C_0006_course_capacity"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.add_column(
        "training_learning_evidence",
        sa.Column(
            "evidence_key",
            sa.String(length=100),
            nullable=True,
            comment="可信来源键：出勤记录/测评结果/学习会话 nonce，同记录内唯一",
        ),
    )
    op.execute(
        "UPDATE training_learning_evidence "
        "SET evidence_key = CONCAT('legacy:', id) WHERE evidence_key IS NULL"
    )
    op.alter_column(
        "training_learning_evidence",
        "evidence_key",
        existing_type=sa.String(length=100),
        nullable=False,
    )
    op.create_unique_constraint(
        "uk_evidence_record_key",
        "training_learning_evidence",
        ["learning_record_id", "evidence_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uk_evidence_record_key", "training_learning_evidence", type_="unique"
    )
    op.drop_column("training_learning_evidence", "evidence_key")
