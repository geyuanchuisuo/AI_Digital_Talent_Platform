"""sys_admin_tables: six sys_* tables per design §8.3 (8b, V2.6 frozen scope).

Revision ID: A_0008_sys_admin
Revises: D_0003_analytics_snapshots (current head of the unified chain)
Create Date: 2026-11-08

Creates sys_departments / sys_roles / sys_user_roles / sys_role_permissions /
sys_menus / sys_operation_logs. IDEMPOTENT create_all (baseline constraint).
"""
from __future__ import annotations

from alembic import op

revision = "A_0008_sys_admin"
down_revision = "D_0003_analytics_snapshots"
branch_labels = None
depends_on = None

TABLES = (
    "sys_departments",
    "sys_roles",
    "sys_user_roles",
    "sys_role_permissions",
    "sys_menus",
    "sys_operation_logs",
)


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
