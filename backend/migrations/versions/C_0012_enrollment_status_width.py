"""C domain migration: widen training_enrollments.status for waitlist promotion.

Revision ID: C_0012_enroll_status
Revises: B_0014_merge_a15
Create Date: 2026-11-21

`awaiting_confirmation` is 21 characters while the original column was VARCHAR(20).
MySQL rejected the waitlist promotion with DataError 1406; SQLite unit tests do not
enforce VARCHAR length, so this migration is verified against a real MySQL instance.

Idempotent: an empty database created from the current metadata already has VARCHAR(32).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0012_enroll_status"
down_revision: Union[str, None] = "B_0014_merge_a15"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TABLE = "training_enrollments"
COLUMN = "status"
TARGET_LENGTH = 32
LEGACY_LENGTH = 20


def _status_column():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in set(inspector.get_table_names()):
        return None
    for column in inspector.get_columns(TABLE):
        if column["name"] == COLUMN:
            return column
    return None


def upgrade() -> None:
    column = _status_column()
    if column is None:
        return
    length = getattr(column["type"], "length", None)
    if length is not None and length >= TARGET_LENGTH:
        return
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=length or LEGACY_LENGTH),
        type_=sa.String(length=TARGET_LENGTH),
        existing_nullable=False,
        existing_server_default="pending",
    )


def downgrade() -> None:
    column = _status_column()
    if column is None:
        return
    length = getattr(column["type"], "length", None)
    if length is not None and length <= LEGACY_LENGTH:
        return
    bind = op.get_bind()
    overflow = bind.scalar(
        sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE CHAR_LENGTH({COLUMN}) > {LEGACY_LENGTH}")
    )
    if overflow:
        raise RuntimeError(
            "cannot downgrade: training_enrollments.status contains values longer than 20 chars"
        )
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=length or TARGET_LENGTH),
        type_=sa.String(length=LEGACY_LENGTH),
        existing_nullable=False,
        existing_server_default="pending",
    )