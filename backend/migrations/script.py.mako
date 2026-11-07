"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Naming convention (ruling U-05): use `A_*` for public/recruitment table changes,
`C_*` for C's assessments/interviews/training; B and D use `B_*` / `D_*`.
One change per migration; downgrade must be reversible.
NOTE: keep generated files ASCII-only (Windows GBK locale read by alembic 1.20).
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
