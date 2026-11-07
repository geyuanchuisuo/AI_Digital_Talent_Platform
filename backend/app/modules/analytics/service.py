"""问数域服务（T-D01b 切片）：registry + 受控统计 + 副本选择 + 缓存 + 快照落库。

口径（docs/modules/A/指标口径交付.md §1，T-A53）：
- **活跃岗位数** = `is_active=1` 且该行是其 title 的最新 version（按 title 去重）。
  实现：`version = (SELECT MAX(version) FROM job_profiles j2 WHERE j2.title = jp.title)`；
  同 title 同最高 version 并存的极端情况按行计数（当前无此数据形态）。

数据源选择（T-D02 契约）：`analytics_db_url` 非空 → 查 PgSQL 只读副本；
空 → 降级主库 MySQL 直查。**不静默**：结果里始终带 `source` 字段。

缓存（T-D01 契约）：data 类键 `jobs.active:v1`，TTL 300s；未配置 Redis 时
`get_cache()` 为降级实例（get 恒 None），行为等价于无缓存直查。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import settings
from app.core.auth.scope import DENY_MESSAGE, get_account_scope
from app.core.cache import get_cache
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.analytics.models import AnalyticsQuerySnapshot

METRIC_ACTIVE_JOBS = "jobs.active"
ACTIVE_JOBS_CACHE_KEY = "jobs.active:v1"
ACTIVE_JOBS_CACHE_TTL = 300  # 秒；切片默认 5 分钟，T-D08 registry 化后按指标配置

METRIC_REGISTRY: dict[str, dict[str, Any]] = {
    METRIC_ACTIVE_JOBS: {
        "name": "活跃岗位数",
        "definition_ref": "docs/modules/A/指标口径交付.md §1（T-A53）",
        "allowed_roles": ("admin", "hr"),  # 个人无此指标（指标目录卡片口径）
        "params": {},
        "source_table": "job_profiles",
    },
}

_UNKNOWN_METRIC = "未登记的指标（白名单外不支持）"

_replica_engine: Engine | None = None


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    name: str
    value: int
    source: str  # replica | primary
    cache_hit: bool
    data_time: datetime
    snapshot_id: int | None = None


def _replica_engine_lazy() -> Engine | None:
    """副本引擎懒建（进程内复用）；未配置返回 None。"""
    global _replica_engine
    if _replica_engine is None and settings.analytics_db_url:
        _replica_engine = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    return _replica_engine


def compute_active_jobs(engine: Engine) -> int:
    """受控统计：活跃岗位数（纯函数式，engine 注入便于测试）。"""
    sql = text(
        """
        SELECT COUNT(*) FROM job_profiles jp
        WHERE jp.is_active = 1
          AND jp.version = (
              SELECT MAX(j2.version) FROM job_profiles j2 WHERE j2.title = jp.title
          )
        """
    )
    with engine.connect() as conn:
        return int(conn.execute(sql).scalar_one())


def _resolve_engine() -> tuple[Engine, str]:
    replica = _replica_engine_lazy()
    if replica is not None:
        # 副本连通性自检失败时降级主库（连接池 pre_ping 会在取连接时报错）
        try:
            with replica.connect():
                return replica, "replica"
        except Exception:  # noqa: BLE001 - 副本不可用降级主库（T-D02 回退口径）
            pass
    from app.database import engine as primary_engine

    return primary_engine, "primary"


async def get_jobs_active(user, *, bypass_cache: bool = False) -> MetricResult:
    """活跃岗位数：鉴权 → 缓存 → 统计 → 快照落库。

    个人角色无此指标（403）；统计与落库走 to_thread 不阻塞事件循环。
    """
    scope = get_account_scope(user)
    metric = METRIC_REGISTRY[METRIC_ACTIVE_JOBS]
    if scope.role not in metric["allowed_roles"]:
        raise BizError(403, DENY_MESSAGE)

    cache = get_cache()
    cache_hit = False
    value: int | None = None
    source = "primary"
    data_time = datetime.now()

    if not bypass_cache:
        cached = await cache.get("data", ACTIVE_JOBS_CACHE_KEY)
        if isinstance(cached, dict) and isinstance(cached.get("value"), int):
            cache_hit = True
            value = cached["value"]
            source = cached.get("source", "primary")
            data_time = datetime.fromisoformat(cached["data_time"]) if cached.get("data_time") else data_time

    if not cache_hit:
        engine, source = await asyncio.to_thread(_resolve_engine)
        value = await asyncio.to_thread(compute_active_jobs, engine)
        data_time = datetime.now()
        await cache.set(
            "data",
            ACTIVE_JOBS_CACHE_KEY,
            {"value": value, "source": source, "data_time": data_time.isoformat()},
            ttl=ACTIVE_JOBS_CACHE_TTL,
        )

    snapshot_id = await asyncio.to_thread(
        _write_snapshot, user, value or 0, source, cache_hit, data_time
    )
    return MetricResult(
        metric_id=METRIC_ACTIVE_JOBS,
        name=metric["name"],
        value=value or 0,
        source=source,
        cache_hit=cache_hit,
        data_time=data_time,
        snapshot_id=snapshot_id,
    )


def _write_snapshot(user, value: int, source: str, cache_hit: bool, data_time: datetime) -> int:
    """快照落库（始终写主库 MySQL；副本只读）。"""
    with SessionLocal() as db:
        row = AnalyticsQuerySnapshot(
            metric_id=METRIC_ACTIVE_JOBS,
            params_json={},
            data_time=data_time,
            result_json={"value": value, "name": "活跃岗位数"},
            source=source,
            cache_hit=1 if cache_hit else 0,
            created_by=getattr(user, "id", None),
        )
        db.add(row)
        db.commit()
        return int(row.id)
