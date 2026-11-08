"""只读受控执行器（T-D09）——安全模型的硬强制层。

- 事务内 `SET LOCAL statement_timeout`（超时 PG 主动取消查询）；
- `SET TRANSACTION READ ONLY`（任何写语句 PG 直接拒绝——校验器漏网时的兜底）；
- 行数上限 fetchmany(max+1) 截断并打 truncated 标；
- Decimal/datetime/bytes 等 JSON 不安全类型统一转换。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


class QueryTimeoutError(RuntimeError):
    """statement_timeout 触发（验收③：超时实测）。"""


class ReadOnlyViolation(RuntimeError):
    """只读事务拒绝写操作（验收④兜底：校验器漏网的写语句）。"""


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, timedelta)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "<binary>"
    return str(v)


def execute_readonly(
    engine: Engine,
    sql: str,
    *,
    timeout_seconds: int,
    max_rows: int,
) -> dict:
    """只读事务执行；返回 {columns, rows, row_count, truncated, duration_ms}。

    QueryTimeoutError / ReadOnlyViolation 由 PG 驱动异常翻译而来（调用方转 HTTP 语义）。
    """
    started = time.monotonic()
    with engine.connect() as conn:
        # SET 不支持绑定参数（PG 语法）；timeout_seconds 为 int，字面量拼接安全
        safe_ms = int(timeout_seconds) * 1000
        conn.execute(text(f"SET LOCAL statement_timeout = {safe_ms}"))
        conn.execute(text("SET TRANSACTION READ ONLY"))
        try:
            rs = conn.execute(text(sql))
            columns = list(rs.keys())
            raw = rs.fetchmany(max_rows + 1)
        except Exception as exc:  # noqa: BLE001 - 驱动异常统一翻译
            conn.rollback()
            msg = str(exc).lower()
            if "statement timeout" in msg or "canceling statement" in msg:
                raise QueryTimeoutError("查询超时") from exc
            if "read-only" in msg or "read only" in msg:
                raise ReadOnlyViolation("只读事务拒绝写操作") from exc
            raise
        truncated = len(raw) > max_rows
        raw = raw[:max_rows]
        conn.commit()  # 结束只读事务（释放快照）

    duration_ms = int((time.monotonic() - started) * 1000)
    rows = [{c: _jsonable(v) for c, v in zip(columns, row)} for row in raw]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "duration_ms": duration_ms,
    }
