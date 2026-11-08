"""报告域 ORM（T-D11）：快照导出审计。

每次导出一行（谁/何时/格式/快照）——导出审计（T-D11 任务要求）。
"""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    CreatedAtMixin,
)


class SnapshotExportAudit(PKMixin, CreatedAtMixin, Base):
    """快照导出审计（`snapshot_export_audit`）。"""

    __tablename__ = "snapshot_export_audit"
    __table_args__ = (
        Index("idx_export_audit_snapshot", "snapshot_id"),
        Index("idx_export_audit_user", "created_by"),
        MYSQL_TABLE_OPTIONS,
    )

    snapshot_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    created_by: Mapped[int] = mapped_column(PK_TYPE, nullable=True)
