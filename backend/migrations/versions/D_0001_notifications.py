"""notifications: in-app notification center (T-D03, F09 closed list 12 types).

Revision ID: D_0001_notifications
Revises: A_0007_scope_grants
Create Date: 2026-11-07

idempotency_key UNIQUE is the DB-level guarantee for "duplicate event must not
produce duplicate notification" (F09 acceptance). IDEMPOTENT create_all
(baseline design constraint, same as A_0007).
"""
from __future__ import annotations

from alembic import op

revision = "D_0001_notifications"
down_revision = "A_0007_scope_grants"
branch_labels = None
depends_on = None

TABLES = ("notifications",)

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
