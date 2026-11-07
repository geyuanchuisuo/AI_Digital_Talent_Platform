"""baseline: 13 public tables (DB design v1.2; migration baseline of the frozen 13-table scope).

Revision ID: A_0001_baseline
Revises:
Create Date: 2026-11-07

Baseline migration: existing 13-table databases should run
`alembic stamp A_0001_baseline` to skip table creation; fresh databases can
`alembic upgrade head` from zero. downgrade DROPs all public tables - be careful.
"""
from __future__ import annotations

from alembic import op

revision = "A_0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# 13 public tables (matches the public section of app/models/__init__.py)
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
