"""C application_id 对接 A 的 applications 表：类型对齐 + 外键。

Revision ID: C_0004_application_fk
Revises: B_0002_merge_workflow_review
Create Date: 2026-11-07

依据：A 的 M3 招聘域已落地 applications 表（同人多岗位独立申请，PK 为
BIGINT UNSIGNED）。C 的 interview_rounds.application_id 与
ai_interview_sessions.application_id 原为可空 Integer 占位，现对齐类型并加外键。
外键不加 ON DELETE（默认 RESTRICT）：申请记录须保留，防误删连带丢面试记录。
挂点说明（2026-11-09 调整）：原挂 `A_0007_scope_grants`，master 合入 A_0008/B_0001/
B_0002/D_0001~D_0009 后会产生双 head，改挂当前单头 `B_0002_merge_workflow_review`。
本迁移从未在任何库执行过，改挂安全。
本地无 MySQL 环境，迁移链未实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "C_0004_application_fk"
down_revision: Union[str, None] = "B_0002_merge_workflow_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 app.models.base.PK_TYPE 在 MySQL 下的渲染一致
PK_MYSQL = mysql.BIGINT(unsigned=True)


def upgrade() -> None:
    op.alter_column(
        "interview_rounds",
        "application_id",
        existing_type=sa.Integer(),
        type_=PK_MYSQL,
        existing_nullable=True,
    )
    op.create_foreign_key(
        "fk_interview_rounds_application",
        "interview_rounds",
        "applications",
        ["application_id"],
        ["id"],
    )
    op.alter_column(
        "ai_interview_sessions",
        "application_id",
        existing_type=sa.Integer(),
        type_=PK_MYSQL,
        existing_nullable=True,
    )
    op.create_foreign_key(
        "fk_ai_interview_sessions_application",
        "ai_interview_sessions",
        "applications",
        ["application_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ai_interview_sessions_application", "ai_interview_sessions", type_="foreignkey"
    )
    op.alter_column(
        "ai_interview_sessions",
        "application_id",
        existing_type=PK_MYSQL,
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.drop_constraint(
        "fk_interview_rounds_application", "interview_rounds", type_="foreignkey"
    )
    op.alter_column(
        "interview_rounds",
        "application_id",
        existing_type=PK_MYSQL,
        type_=sa.Integer(),
        existing_nullable=True,
    )
