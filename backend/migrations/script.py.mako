"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

命名约定（U-05 裁决）：公共/招聘表变更用 `A_*`，C 的测评/面试/培训用 `C_*`，
B/D 落地时分别用 `B_*` / `D_*`。每个迁移只做一件事，downgrade 必须可回滚。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = "${up_revision}"
down_revision = ${down_revision | none,repr}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
