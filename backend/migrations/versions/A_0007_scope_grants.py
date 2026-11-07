"""user_scope_grants: cross-scope authorization records (V2.6 §3.1 frozen scope).

Revision ID: A_0007_scope_grants
Revises: A_0006_merge_m3_c0003
Create Date: 2026-11-07

HR/org admins grant cross-department data scope with reason and validity
period; D's org-scope analytics and B's data filtering consume AccountScope
built from these grants. IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "A_0007_scope_grants"
down_revision = "A_0006_merge_m3_c0003"
branch_labels = None
depends_on = None

TABLES = ("user_scope_grants",)


def upgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(TABLES)],
    )
