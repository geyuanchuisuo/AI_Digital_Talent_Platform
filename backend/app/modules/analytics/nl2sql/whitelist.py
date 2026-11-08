"""副本 schema 白名单发现（T-D09）。

白名单 = PgSQL 副本 information_schema 中实际存在的表/列——ETL 列级 allowlist
的落地物即字段白名单的执行面（副本里没有敏感列可查）。带 TTL 缓存；
ETL 重跑新增表后 TTL 过期自动可见。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

_CACHE: dict[str, Any] = {"data": None, "at": 0.0}
_TTL_SECONDS = 60.0


def get_whitelist(engine: Engine, *, force_refresh: bool = False) -> dict[str, frozenset[str]]:
    """{表名(小写): 列名集合(小写)}——只含 public schema 的普通表。"""
    now = time.monotonic()
    if (
        not force_refresh
        and _CACHE["data"] is not None
        and now - _CACHE["at"] < _TTL_SECONDS
    ):
        return _CACHE["data"]

    result: dict[str, frozenset[str]] = {}
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.table_name, c.column_name "
            "FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' "
            "  AND t.table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY c.table_name, c.ordinal_position"
        )).mappings()
        for r in rows:
            t = r["table_name"].lower()
            result.setdefault(t, set()).add(r["column_name"].lower())
    result = {t: frozenset(c) for t, c in result.items()}
    _CACHE["data"] = result
    _CACHE["at"] = now
    return result


def reset_whitelist_cache() -> None:
    """测试/ETL 后手动失效。"""
    _CACHE["data"] = None
    _CACHE["at"] = 0.0


def schema_context(whitelist: dict[str, frozenset[str]]) -> str:
    """给 LLM 的 schema 描述（白名单内的表+列）。"""
    lines = []
    for t in sorted(whitelist):
        cols = ", ".join(sorted(whitelist[t]))
        lines.append(f"- {t}({cols})")
    return "\n".join(lines)
