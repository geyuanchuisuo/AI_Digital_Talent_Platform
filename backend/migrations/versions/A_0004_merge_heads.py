"""merge A_0003_snapshot and C_0002_adaptive_assessment (parallel branch heads).

Revision ID: A_0004_merge_heads
Revises: A_0003_snapshot, C_0002_adaptive_assessment
Create Date: 2026-11-07

Pure graph merge: A_0002/A_0003 (A's public snapshot columns) and C_0002
(C's adaptive-assessment migrations) were authored in parallel off C_0001.
No schema actions - just joins the two branches so `alembic upgrade head`
applies both. Follow-up migrations use this as their single parent.
"""
from __future__ import annotations

revision = "A_0004_merge_heads"
down_revision = ("A_0003_snapshot", "C_0002_adaptive_assessment")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
