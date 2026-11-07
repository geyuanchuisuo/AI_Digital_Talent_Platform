"""m3_recruitment: five recruitment-domain tables (M3-V2.6 feature 8a-1).

Revision ID: A_0005_m3_recruitment
Revises: A_0004_merge_heads
Create Date: 2026-11-07

Creates project_groups / projects / project_demands (design §9 fields, with
headcount & job linkage), jd_publications (V2.6 F01 JD generation & posting)
and applications (multi-position independent applications with the confirmed
five-stage external mapping). IDEMPOTENT create_all on the named tables (same
baseline design constraint as A_0001/C_0001).
"""
from __future__ import annotations

from alembic import op

revision = "A_0005_m3_recruitment"
down_revision = "A_0004_merge_heads"
branch_labels = None
depends_on = None

M3_TABLES = (
    "project_groups",
    "projects",
    "project_demands",
    "jd_publications",
    "applications",
)


def upgrade() -> None:
    from app.models import Base  # importing registers all 40 tables

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in M3_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(M3_TABLES)],
    )
