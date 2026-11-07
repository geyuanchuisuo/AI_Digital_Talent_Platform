"""baseline：公共 13 表（数据库设计 v1.2；13 表冻结口径的迁移基线）

Revision ID: A_0001_baseline
Revises:
Create Date: 2026-11-07

基线迁移：已有 13 表库执行 `alembic stamp A_0001_baseline` 跳过建表；
全新库可从零 `alembic upgrade head`。 downgrade 会 DROP 全部公共表，慎用。
"""
from __future__ import annotations

from alembic import op

revision = "A_0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# 公共 13 表（与 app/models/__init__.py 的公共段一致）
PUBLIC_TABLES = (
    "users",
    "sys_settings",
    "resume_files",
    "parse_tasks",
    "parsed_resumes",
    "talent_profiles",
    "talent_tags",
    "talent_duplicates",
    "job_profiles",
    "screen_batches",
    "screen_results",
    "match_batches",
    "match_results",
)


def upgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in PUBLIC_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(PUBLIC_TABLES)],
    )
