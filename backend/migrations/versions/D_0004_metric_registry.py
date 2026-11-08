"""metric_registry: whitelist metric catalog persistence (T-D08 layer-1).

Revision ID: D_0004_metric_registry
Revises: A_0008_sys_admin
Create Date: 2026-11-08

Seeds the first-batch six metric cards (指标目录 v1) + the T-D01b slice
metric. Idempotent: create_all + upsert-by-metric_id seed (safe on rerun).
B-domain parse card and C-domain exam/training/learning cards are seeded as
`awaiting_caliber` — queries are rejected until B/C deliver their calibers.
"""
from __future__ import annotations

from alembic import op

revision = "D_0004_metric_registry"
down_revision = "A_0008_sys_admin"
branch_labels = None
depends_on = None

TABLES = ("metric_registry",)

def upgrade() -> None:
    from app.models import Base
    from app.modules.analytics.models import seed_metric_registry

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in TABLES],
        checkfirst=True,
    )
    seed_metric_registry(bind)

def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(TABLES)],
    )
