"""empty message

Revision ID: A_0014_merge_remote
Revises: C_0011_enroll_study, D_0013_nl2sql_audit_fields
Create Date: 2026-11-12 00:27:05.939644

Naming convention (ruling U-05): use `A_*` for public/recruitment table changes,
`C_*` for C's assessments/interviews/training; B and D use `B_*` / `D_*`.
One change per migration; downgrade must be reversible.
NOTE: keep generated files ASCII-only (Windows GBK locale read by alembic 1.20).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'A_0014_merge_remote'
down_revision = ('C_0011_enroll_study', 'D_0013_nl2sql_audit_fields')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
