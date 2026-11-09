"""自由问数编排（T-D09）——生成 → 校验 → 执行 → 审计，全链路 nl2sql_audit 留痕。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.engine import Engine

from app.core.auth.scope import DENY_MESSAGE, get_account_scope
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.analytics.models import Nl2SqlAudit
from app.modules.analytics.nl2sql.executor import (
    QueryTimeoutError,
    ReadOnlyViolation,
    execute_readonly,
)
from app.modules.analytics.nl2sql.generator import (
    GeneratorUnavailable,
    get_generator,
)
from app.modules.analytics.nl2sql.validator import validate_sql
from app.modules.analytics.nl2sql.whitelist import get_whitelist, reset_whitelist_cache

QUESTION_MAX_CHARS = 500


async def nl2sql_query(user, question: str) -> dict:
    """自由问数全链路。返回 {question, sql, columns, rows, row_count, truncated, duration_ms, source, audit_id}。

    拒绝语义（验收②）：生成器返回 None → 422「无法安全映射」；校验器拦截 → 422「不安全 SQL」；
    LLM 未配置/调用失败 → 503；超时 → 503。每一阶段（含拒绝）均落 nl2sql_audit。
    """
    scope = get_account_scope(user)
    if scope.role not in ("admin", "hr"):
        raise BizError(403, DENY_MESSAGE)
    question = (question or "").strip()
    if not question or len(question) > QUESTION_MAX_CHARS:
        raise BizError(422, "问题为空或超过 500 字符")

    engine, source = await asyncio.to_thread(_resolve_engine)
    audit_id: int | None = None
    sql: str | None = None
    stage = "generated"
    refused_reason: str | None = None

    try:
        generator = get_generator()  # 不可用 → GeneratorUnavailable → 503
    except GeneratorUnavailable as exc:
        audit_id = _audit(user, question, None, "refused_unmappable", f"生成通道不可用：{exc}", None, None)
        raise BizError(503, f"自由问数模型不可用：{exc}") from exc

    # 生成（LLM 调用放线程池，不阻塞事件循环）
    try:
        whitelist = await asyncio.to_thread(get_whitelist, engine)
        from app.modules.analytics.nl2sql.whitelist import schema_context

        sql = await asyncio.to_thread(
            generator.generate_sql, question, schema_context(whitelist)
        )
    except GeneratorUnavailable as exc:
        audit_id = _audit(user, question, None, "refused_unmappable", f"生成通道不可用：{exc}", None, None)
        raise BizError(503, f"自由问数模型不可用：{exc}") from exc

    if sql is None:
        audit_id = _audit(user, question, None, "refused_unmappable", "无法安全映射为白名单 SQL", None, None)
        raise BizError(422, "无法安全映射为白名单内查询，已拒答")

    audit_id = _audit(user, question, sql, stage, None, None, None)

    # 校验（白名单发现同样放线程池）
    whitelist = await asyncio.to_thread(get_whitelist, engine)
    vr = await asyncio.to_thread(
        validate_sql, sql, whitelist,
        max_rows=settings_nl2sql_max_rows(),
    )
    if not vr.ok:
        stage, refused_reason = "refused_unsafe", vr.reason
        _audit_update(audit_id, stage=stage, refused_reason=vr.reason)
        raise BizError(422, f"生成 SQL 未通过安全校验：{vr.reason}")
    sql = vr.sql
    stage = "validated"
    _audit_update(audit_id, stage=stage, generated_sql=sql)

    # 受控执行（只读事务 + 超时 + 行上限）
    started = datetime.now()
    try:
        result = await asyncio.to_thread(
            execute_readonly, engine, sql,
            timeout_seconds=settings_nl2sql_timeout(),
            max_rows=settings_nl2sql_max_rows(),
        )
    except QueryTimeoutError as exc:
        stage, refused_reason = "timeout", "查询超时"
        _audit_update(audit_id, stage=stage, refused_reason=str(exc),
                      duration_ms=_ms_since(started))
        raise BizError(503, "查询超时") from exc
    except ReadOnlyViolation as exc:
        stage, refused_reason = "refused_unsafe", "只读事务拒绝写操作"
        _audit_update(audit_id, stage=stage, refused_reason=str(exc))
        raise BizError(422, "只读事务拒绝写操作") from exc
    except Exception as exc:  # noqa: BLE001 - 执行期其余失败（列不存在等）如实转 422
        stage, refused_reason = "error", str(exc)[:255]
        _audit_update(audit_id, stage=stage, refused_reason=str(exc)[:255])
        raise BizError(422, f"查询执行失败：{str(exc)[:120]}") from exc

    stage = "executed"
    duration = _ms_since(started)
    _audit_update(audit_id, stage=stage, row_count=result["row_count"], duration_ms=duration)
    return {
        "question": question,
        "sql": sql,
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "truncated": result["truncated"],
        "duration_ms": duration,
        "source": source,
        "audit_id": audit_id,
    }


def _resolve_engine() -> tuple[Engine, str]:
    from app.modules.analytics.service import _resolve_engine as shared_resolve

    return shared_resolve()


def settings_nl2sql_timeout() -> int:
    from app.config import settings

    return settings.nl2sql_timeout_seconds


def settings_nl2sql_max_rows() -> int:
    from app.config import settings

    return settings.nl2sql_max_rows


def _ms_since(started: datetime) -> int:
    return int((datetime.now() - started).total_seconds() * 1000)


def _audit(user, question: str, sql: str | None, stage: str,
           refused_reason: str | None, row_count: int | None, duration_ms: int | None) -> int:
    with SessionLocal() as db:
        row = Nl2SqlAudit(
            question=question[:500],
            generated_sql=sql,
            stage=stage,
            refused_reason=refused_reason,
            row_count=row_count,
            duration_ms=duration_ms,
            created_by=getattr(user, "id", None),
        )
        db.add(row)
        db.commit()
        return int(row.id)


def _audit_update(audit_id: int, **fields) -> None:
    with SessionLocal() as db:
        row = db.get(Nl2SqlAudit, audit_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()
