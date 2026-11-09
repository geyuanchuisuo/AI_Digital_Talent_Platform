"""ai_providers / ai_prompt_versions / ai_call_logs: AI 中台管理面 (T-D20).

Revision ID: D_0011_ai_platform
Revises: D_0010_workflow_skills
Create Date: 2026-11-09

Three tables for F08 AI 中台管理矩阵（PRD §2.7，需求 D-35）：providers
(multi-vendor, enable/disable, key stored locally never echoed), prompt
versions (name+version unique, immutable once published), call logs
(llm_client instrumentation source for usage/error dashboards).
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0011_ai_platform"
down_revision = "D_0010_workflow_skills"
branch_labels = None
depends_on = None

TABLES = (
    "ai_providers",
    "ai_prompt_versions",
    "ai_call_logs",
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
