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

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
