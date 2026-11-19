"""empty message

Revision ID: A_0015_merge_remote2
Revises: A_0014_merge_remote, A_0015_failure_reason
Create Date: 2026-11-12 01:15:15.153956

Naming convention (ruling U-05): use `A_*` for public/recruitment table changes,
`C_*` for C's assessments/interviews/training; B and D use `B_*` / `D_*`.
One change per migration; downgrade must be reversible.
NOTE: keep generated files ASCII-only (Windows GBK locale read by alembic 1.20).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'A_0015_merge_remote2'
down_revision = ('A_0014_merge_remote', 'A_0015_failure_reason')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
