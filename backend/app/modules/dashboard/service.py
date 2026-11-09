"""AI 中台管理面 service（T-D20）。

范围（一期后端）：模型供应商 CRUD/启停（默认执行面切换）、Prompt 版本库
（发布后不可变）、调用日志落库（llm_client 埋点）与用量/错误率聚合、
运行看板（workflow_runs 只读聚合）。

口径：
- 密钥仅落库，`to_provider_dict` 永不回显（验收①，测试断言）；
- 启停即时生效：执行面 `resolve_default_provider()` 每次调用读取（验收②）；
- 看板数字可回查：聚合 SQL 与明细同源 ai_call_logs（验收③）；
- 智能体注册/发布/退役复用 T-D15 工作流生命周期（需求：不另造第三套编排），
  本模块只提供运行看板只读视图。

llm_client 埋点：`record_call()` 由 llm_client._chat_raw 成功/失败两路调用，
落库失败只记日志不抛（审计是旁路，不阻塞业务调用）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.dashboard.models import (
    AI_CALL_RESULTS,
    AI_PROMPT_STATUSES,
    AI_PROVIDER_TYPES,
    AiCallLog,
    AiPromptVersion,
    AiProvider,
)
from app.modules.workflows.service import _require_management

logger = logging.getLogger("app.ai_platform")

_PROMPT_MAX = 8000


# ---- 模型供应商 ----

def list_providers(db: Session) -> list[AiProvider]:
    return list(db.execute(select(AiProvider).order_by(AiProvider.id.asc())).scalars().all())


def to_provider_dict(row: AiProvider) -> dict[str, Any]:
    """序列化：**无 api_key 字段**（验收①——密钥不出现在任何接口响应）。"""
    return {
        "id": row.id,
        "name": row.name,
        "provider_type": row.provider_type,
        "base_url": row.base_url,
        "model": row.model,
        "enabled": bool(row.enabled),
        "is_default": bool(row.is_default),
        "timeout_seconds": row.timeout_seconds,
        "note": row.note,
        "updated_by": row.updated_by,
        "has_api_key": bool(row.api_key),
    }


def create_provider(
    db: Session, *, name: str, provider_type: str, base_url: str, model: str,
    api_key: str, timeout_seconds: int = 120, note: str | None = None, user,
) -> AiProvider:
    _require_management(user)
    if not name or len(name) > 64:
        raise BizError(422, "name 必填且 ≤64 字符")
    if provider_type not in AI_PROVIDER_TYPES:
        raise BizError(422, f"provider_type {provider_type!r} 不在白名单（{', '.join(AI_PROVIDER_TYPES)}）")
    if not base_url or len(base_url) > 255:
        raise BizError(422, "base_url 必填且 ≤255 字符")
    if not model or len(model) > 128:
        raise BizError(422, "model 必填且 ≤128 字符")
    if not api_key or len(api_key) > 255:
        raise BizError(422, "api_key 必填且 ≤255 字符（仅落库，不回显）")
    if not 1 <= timeout_seconds <= 600:
        raise BizError(422, "timeout_seconds 须在 1–600 之间")
    row = AiProvider(
        name=name, provider_type=provider_type, base_url=base_url, model=model,
        api_key=api_key, timeout_seconds=timeout_seconds, note=note,
        updated_by=getattr(user, "id", None),
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - name 唯一约束
        db.rollback()
        raise BizError(409, f"供应商 name 已存在：{name}") from exc
    db.refresh(row)
    return row


def update_provider(
    db: Session, provider_id: int, *, user,
    base_url: str | None = None, model: str | None = None,
    api_key: str | None = None, timeout_seconds: int | None = None,
    note: str | None = None,
) -> AiProvider:
    """部分更新（管理侧）；api_key 传空字符串视为清除，传 None 不动。"""
    _require_management(user)
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise BizError(404, "供应商不存在")
    if base_url is not None:
        if not base_url or len(base_url) > 255:
            raise BizError(422, "base_url 必填且 ≤255 字符")
        row.base_url = base_url
    if model is not None:
        if not model or len(model) > 128:
            raise BizError(422, "model 必填且 ≤128 字符")
        row.model = model
    if api_key is not None:
        if len(api_key) > 255:
            raise BizError(422, "api_key ≤255 字符")
        row.api_key = api_key
    if timeout_seconds is not None:
        if not 1 <= timeout_seconds <= 600:
            raise BizError(422, "timeout_seconds 须在 1–600 之间")
        row.timeout_seconds = timeout_seconds
    if note is not None:
        row.note = note
    row.updated_by = getattr(user, "id", None)
    db.commit()
    db.refresh(row)
    return row


def set_provider_enabled(
    db: Session, provider_id: int, *, enabled: bool, user
) -> AiProvider:
    """启停（管理侧）：停用默认供应商时默认身份一并取消（执行面回退 .env 兜底）。"""
    _require_management(user)
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise BizError(404, "供应商不存在")
    row.enabled = 1 if enabled else 0
    if not enabled:
        row.is_default = 0
    row.updated_by = getattr(user, "id", None)
    db.commit()
    db.refresh(row)
    return row


def set_default_provider(db: Session, provider_id: int, *, user) -> AiProvider:
    """设默认执行面（管理侧；CAS 全表唯一；停用态供应商不可设默认）。"""
    _require_management(user)
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise BizError(404, "供应商不存在")
    if not row.enabled:
        raise BizError(422, "停用中的供应商不可设为默认，请先启用")
    db.execute(update(AiProvider).values(is_default=0))
    row.is_default = 1
    row.updated_by = getattr(user, "id", None)
    db.commit()
    db.refresh(row)
    return row


def resolve_default_provider(db: Session) -> AiProvider | None:
    """执行面读取入口（每次调用实时查询——启停/切换即时生效，验收②）。"""
    return db.execute(
        select(AiProvider).where(AiProvider.enabled == 1, AiProvider.is_default == 1)
    ).scalar_one_or_none()


# ---- Prompt 版本库 ----

def list_prompts(db: Session, name: str | None = None) -> list[AiPromptVersion]:
    stmt = select(AiPromptVersion).order_by(
        AiPromptVersion.name.asc(), AiPromptVersion.version.desc()
    )
    if name:
        stmt = stmt.where(AiPromptVersion.name == name)
    return list(db.scalars(stmt))


def create_prompt(
    db: Session, *, name: str, content: str, note: str | None = None, user,
    created_by: int | None = None,
) -> AiPromptVersion:
    """新建草稿版本：版本号 = 同 name max+1（管理侧）。"""
    _require_management(user)
    if not name or len(name) > 64:
        raise BizError(422, "name 必填且 ≤64 字符")
    if not content or len(content) > _PROMPT_MAX:
        raise BizError(422, f"content 必填且 ≤{_PROMPT_MAX} 字符")
    max_version = db.execute(
        select(func.max(AiPromptVersion.version)).where(AiPromptVersion.name == name)
    ).scalar()
    row = AiPromptVersion(
        name=name, version=(max_version or 0) + 1, status="draft",
        content=content, note=note, created_by=getattr(user, "id", None) or created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_draft_prompt(db: Session, version_id: int, *, content: str, user) -> AiPromptVersion:
    _require_management(user)
    row = db.get(AiPromptVersion, version_id)
    if row is None:
        raise BizError(404, "Prompt 版本不存在")
    if row.status != "draft":
        raise BizError(409, "发布后内容不可变（版本锁定），改版请新建版本")
    if not content or len(content) > _PROMPT_MAX:
        raise BizError(422, f"content 必填且 ≤{_PROMPT_MAX} 字符")
    row.content = content
    db.commit()
    db.refresh(row)
    return row


def publish_prompt(db: Session, version_id: int, *, user) -> AiPromptVersion:
    """草稿 → 发布（管理侧；同 name 其他 published 一并退役——唯一生效版）。"""
    _require_management(user)
    row = db.get(AiPromptVersion, version_id)
    if row is None:
        raise BizError(404, "Prompt 版本不存在")
    if row.status != "draft":
        raise BizError(409, f"仅草稿可发布，当前 {row.status}")
    db.execute(
        update(AiPromptVersion)
        .where(AiPromptVersion.name == row.name, AiPromptVersion.status == "published")
        .values(status="deprecated")
    )
    row.status = "published"
    row.published_by = getattr(user, "id", None)
    row.published_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


def resolve_published_prompt(db: Session, *, name: str) -> AiPromptVersion | None:
    """执行面读取：同 name 唯一 published 版本；无则 None（调用方回退文件加载器）。"""
    return db.execute(
        select(AiPromptVersion)
        .where(AiPromptVersion.name == name, AiPromptVersion.status == "published")
        .order_by(AiPromptVersion.version.desc())
        .limit(2)
    ).scalar_one_or_none()


def to_prompt_dict(row: AiPromptVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "status": row.status,
        "content": row.content,
        "note": row.note,
        "created_by": row.created_by,
        "published_by": row.published_by,
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


# ---- 调用日志（llm_client 埋点） ----

def record_call(
    db: Session, *, provider_name: str, model: str, purpose: str,
    result: str, latency_ms: int,
    prompt_tokens: int | None = None, completion_tokens: int | None = None,
    error: str | None = None, actor_user_id: int | None = None,
) -> None:
    """逐次调用落一行；审计旁路——失败只记日志不抛，不阻塞业务调用。"""
    if result not in AI_CALL_RESULTS:
        result = "error"
    try:
        db.add(AiCallLog(
            provider_name=provider_name, model=model,
            purpose=purpose[:64] or "unspecified", result=result,
            latency_ms=max(0, int(latency_ms)),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            error=(error or None) and str(error)[:500],
            actor_user_id=actor_user_id,
        ))
        db.commit()
    except Exception:  # noqa: BLE001 - 审计旁路
        db.rollback()
        logger.exception("AI 调用日志落库失败（不阻塞业务）")


def usage_summary(db: Session, *, days: int = 7) -> dict[str, Any]:
    """用量/错误率看板：近 N 天按 purpose×result 聚合；数字可回查明细（验收③）。"""
    if not 1 <= days <= 90:
        raise BizError(422, "days 须在 1–90 之间")
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(
            AiCallLog.purpose,
            AiCallLog.result,
            func.count().label("calls"),
            func.coalesce(func.sum(AiCallLog.latency_ms), 0).label("latency_ms"),
            func.coalesce(func.sum(AiCallLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AiCallLog.completion_tokens), 0).label("completion_tokens"),
        )
        .where(AiCallLog.created_at >= since)
        .group_by(AiCallLog.purpose, AiCallLog.result)
    ).all()
    by_purpose: dict[str, dict[str, Any]] = {}
    total_calls = 0
    total_errors = 0
    total_latency = 0
    for purpose, result, calls, latency, ptok, ctok in rows:
        bucket = by_purpose.setdefault(
            purpose,
            {"purpose": purpose, "ok": 0, "error": 0, "total_latency_ms": 0,
             "prompt_tokens": 0, "completion_tokens": 0},
        )
        calls = int(calls)
        bucket["ok" if result == "ok" else "error"] += calls
        bucket["total_latency_ms"] += int(latency)
        bucket["prompt_tokens"] += int(ptok or 0)
        bucket["completion_tokens"] += int(ctok or 0)
        total_calls += calls
        total_latency += int(latency)
        if result == "error":
            total_errors += calls
    summaries = []
    for bucket in by_purpose.values():
        calls = bucket["ok"] + bucket["error"]
        summaries.append({
            **bucket,
            "calls": calls,
            "error_rate": round(bucket["error"] / calls, 4) if calls else 0.0,
            "avg_latency_ms": round(bucket["total_latency_ms"] / calls) if calls else 0,
        })
    summaries.sort(key=lambda b: -b["calls"])
    return {
        "days": days,
        "total_calls": total_calls,
        "total_errors": total_errors,
        "error_rate": round(total_errors / total_calls, 4) if total_calls else 0.0,
        "avg_latency_ms": round(total_latency / total_calls) if total_calls else 0,
        "by_purpose": summaries,
    }


def list_call_logs(
    db: Session, *, days: int = 7, purpose: str | None = None,
    result: str | None = None, limit: int = 100,
) -> list[AiCallLog]:
    """明细回查（验收③：看板数字与明细同源）。"""
    if not 1 <= days <= 90:
        raise BizError(422, "days 须在 1–90 之间")
    if limit < 1 or limit > 500:
        raise BizError(422, "limit 须在 1–500 之间")
    since = datetime.now() - timedelta(days=days)
    stmt = (
        select(AiCallLog)
        .where(AiCallLog.created_at >= since)
        .order_by(AiCallLog.id.desc())
        .limit(limit)
    )
    if purpose:
        stmt = stmt.where(AiCallLog.purpose == purpose)
    if result:
        if result not in AI_CALL_RESULTS:
            raise BizError(422, f"result 仅允许 {', '.join(AI_CALL_RESULTS)}")
        stmt = stmt.where(AiCallLog.result == result)
    return list(db.scalars(stmt))


def to_call_log_dict(row: AiCallLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider_name": row.provider_name,
        "model": row.model,
        "purpose": row.purpose,
        "result": row.result,
        "latency_ms": row.latency_ms,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "error": row.error,
        "actor_user_id": row.actor_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---- 运行看板（工作流即智能体载体的只读视图） ----

def agent_run_dashboard(db: Session, *, days: int = 7) -> dict[str, Any]:
    """智能体运行看板：按定义聚合 workflow_runs（触发类型/状态分布）。"""
    from app.models import WorkflowDefinition, WorkflowRun

    if not 1 <= days <= 90:
        raise BizError(422, "days 须在 1–90 之间")
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(
            WorkflowRun.definition_id,
            WorkflowDefinition.name,
            WorkflowDefinition.code,
            WorkflowRun.trigger_type,
            WorkflowRun.status,
            func.count().label("runs"),
        )
        .join(WorkflowDefinition, WorkflowDefinition.id == WorkflowRun.definition_id)
        .where(WorkflowRun.created_at >= since)
        .group_by(
            WorkflowRun.definition_id, WorkflowDefinition.name,
            WorkflowDefinition.code, WorkflowRun.trigger_type, WorkflowRun.status,
        )
    ).all()
    agents: dict[int, dict[str, Any]] = {}
    for definition_id, name, code, trigger_type, status, runs in rows:
        bucket = agents.setdefault(
            definition_id,
            {"definition_id": definition_id, "name": name, "code": code,
             "by_trigger": {}, "by_status": {}, "total": 0},
        )
        bucket["by_trigger"][trigger_type] = bucket["by_trigger"].get(trigger_type, 0) + int(runs)
        bucket["by_status"][status] = bucket["by_status"].get(status, 0) + int(runs)
        bucket["total"] += int(runs)
    return {"days": days, "agents": sorted(agents.values(), key=lambda b: -b["total"])}
