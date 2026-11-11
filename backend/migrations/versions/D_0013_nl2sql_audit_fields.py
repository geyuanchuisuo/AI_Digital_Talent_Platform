"""nl2sql_audit: add executed_sql + truncated columns (T-D09 D15 audit binding).

Revision ID: D_0013_nl2sql_audit_fields
Revises: C_0010_capability_ratings
Create Date: 2026-11-11

Adds two columns to nl2sql_audit (D15 audit-binding hard-gap fix):
- `executed_sql` (TEXT, nullable): the final SQL actually executed after
  validator LIMIT-wrapping. `generated_sql` keeps the model's raw output, so
  the two-step chain is fully traceable.
- `truncated` (TINYINT(1), NOT NULL, server_default 0): whether the result was
  truncated by the row cap. Response already returned `truncated`; now it is
  durable in audit for back-checking.

Idempotent ALTER: the baseline D_0005 created the table from the live model
snapshot (which now includes these columns), so a fresh empty database already
has them; legacy databases upgraded via stamp D_0005 do not. The inspector check
makes both paths work. Do NOT replace inspector checks with blind ALTER here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "D_0013_nl2sql_audit_fields"
down_revision = "C_0010_capability_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    cols = {c["name"] for c in insp.get_columns("nl2sql_audit")}

    if "executed_sql" not in cols:
        op.add_column(
            "nl2sql_audit",
            sa.Column("executed_sql", sa.Text(), nullable=True),
        )
    if "truncated" not in cols:
        op.add_column(
            "nl2sql_audit",
            sa.Column(
                "truncated",
                mysql.TINYINT(display_width=1),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    cols = {c["name"] for c in insp.get_columns("nl2sql_audit")}

    # 降级前先清值：truncated 非空约束要求先删列或回填，这里直接删列（审计数据
    # 可按需从备份恢复；常规回滚不涉及生产审计）。
    if "truncated" in cols:
        op.drop_column("nl2sql_audit", "truncated")
    if "executed_sql" in cols:
        op.drop_column("nl2sql_audit", "executed_sql")
