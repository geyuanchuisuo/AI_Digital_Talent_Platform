"""问数域 ORM（T-D01b 切片起步）：analytics_query_snapshots 查询快照。

快照口径（todo T-D08 全量落库前的最小版）：每次指标计算落一行——指标 id、
参数、数据时间、结果 JSON、数据来源（副本/主库）、是否缓存命中、操作人。
同快照导出（T-D11）与反馈审计（T-D12）都引用本表 id。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    BOOL_TYPE,
    CreatedAtMixin,
)


class AnalyticsQuerySnapshot(PKMixin, CreatedAtMixin, Base):
    """指标查询快照（`analytics_query_snapshots`）。"""

    __tablename__ = "analytics_query_snapshots"
    __table_args__ = (
        Index("idx_aqs_metric_time", "metric_id", "created_at"),
        Index("idx_aqs_user", "created_by"),
        MYSQL_TABLE_OPTIONS,
    )

    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    data_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")
    cache_hit: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
