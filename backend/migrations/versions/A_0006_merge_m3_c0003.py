"""merge A_0005_m3_recruitment and C_0003_phase1_expansion (parallel branch heads).

Revision ID: A_0006_merge_m3_c0003
Revises: A_0005_m3_recruitment, C_0003_phase1_expansion
Create Date: 2026-11-07

Pure graph merge: A's recruitment tables (A_0005) and C's phase-1 expansion
(C_0003) were authored in parallel off A_0004_merge_heads. No schema actions.
"""
from __future__ import annotations

revision = "A_0006_merge_m3_c0003"
down_revision = ("A_0005_m3_recruitment", "C_0003_phase1_expansion")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
