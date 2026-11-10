"""workflow_skills / workflow_skill_versions: Skills 组件库 (T-D19).

Revision ID: D_0010_workflow_skills
Revises: C_0006_course_capacity
Create Date: 2026-11-09

Two tables for F06/D11 Skills 可编排组件库（PRD §2.7，需求 D-35）：
skill definition (code unique, tool/output_parser whitelist) -> skill version
(draft/pending_review/published/deprecated, prompt_template + params JSON
schema snapshot, immutable once published = version locking, four-eyes
review lifecycle same as workflow definition versions).
IDEMPOTENT create_all (baseline design constraint).
"""
from __future__ import annotations

from alembic import op

revision = "D_0010_workflow_skills"
down_revision = "C_0006_course_capacity"
branch_labels = None
depends_on = None

TABLES = (
    "workflow_skills",
    "workflow_skill_versions",
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
