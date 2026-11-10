"""member_im_accounts / digital_tasks / digital_outbound_requests: 数字员工 (T-D21).

Revision ID: D_0012_digital_employee
Revises: D_0011_ai_platform
Create Date: 2026-11-10

Three tables for F08 数字员工（PRD §2.8，纯站内版——2026-11-10 用户确认无企微
凭据先行）：im accounts (manual binding, channel reserved), tasks (three
read-only scenarios, idempotent, in-app notify only), outbound requests
(register-only, never auto-send; approval != send).
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0012_digital_employee"
down_revision = "D_0011_ai_platform"
branch_labels = None
depends_on = None

TABLES = (
    "member_im_accounts",
    "digital_tasks",
    "digital_outbound_requests",
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
