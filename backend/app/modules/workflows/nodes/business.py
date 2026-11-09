"""五类业务节点执行器（T-D17）：各自调用对应域 service，不自查他域表。

接线（真实服务，权限由域服务按发起人 AccountScope 强制——越权即 403 节点失败）：
- profile_parse        → app.service.parse_service.extract_structured（B，真实 LLM）
- job_match            → app.service.match_service.create_batch（A，建批真实落库；
                         批次后续跑批由 TaskRunner 承载，节点产出 batch_id 供下游引用）
- assessment_analysis  → app.modules.assessments.service.get_result（C，读已判分结果）
- training_suggestion  → app.modules.training.service.create_ai_draft（C，真实 LLM 草稿）
- data_analysis        → app.modules.analytics.service.query_metric（D，registry 准入）

真实模型调用（parse/training）依赖用户配置的 LLM Key；Key 未配置时按统一模型
服务口径明确失败，不 mock、不静默降级（用户红线）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import BizError

from .runtime import NodeContext, load_user


def _config_value(node: dict[str, Any], inputs: dict[str, Any], key: str) -> Any:
    """配置优先、输入引用兜底的取值；两处都缺则 422（不猜默认值）。"""
    config = node.get("config") or {}
    if config.get(key) is not None:
        return config[key]
    if inputs.get(key) is not None:
        return inputs[key]
    raise BizError(422, f"节点 {node.get('node_key')!r} 缺少必填配置/输入 {key!r}")


# ---- profile_parse：文本 → 结构化档案（B） ----

def profile_parse(ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from app.service import parse_service  # noqa: PLC0415 - 域服务按需引入

    text = _config_value(node, inputs, "text")
    if not isinstance(text, str) or not text.strip():
        raise BizError(422, "档案解析需要非空文本输入")
    schema = parse_service.extract_structured(text)
    return parse_service.to_dict(schema)


# ---- job_match：发起岗位匹配批次（A） ----

def job_match(ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from app.service import match_service  # noqa: PLC0415

    job_profile_id = _config_value(node, inputs, "job_profile_id")
    if not isinstance(job_profile_id, int):
        raise BizError(422, "job_profile_id 必须为整数")
    scope = (node.get("config") or {}).get("scope") or inputs.get("scope")
    if not isinstance(scope, dict):
        raise BizError(422, "匹配节点缺少 scope（范围映射）")
    with ctx.session_factory() as db:
        batch = match_service.create_batch(
            db, job_profile_id, scope, user_id=ctx.user_id or 0
        )
    return {
        "batch_id": batch["batch_id"],
        "total": batch["total"],
        "status": batch["status"],
        "estimated_llm_calls": batch.get("estimated_llm_calls", 0),
    }


# ---- assessment_analysis：读取已判分测评结果（C） ----

def assessment_analysis(
    ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    from app.modules.assessments import service as assessment_service  # noqa: PLC0415

    assignment_id = _config_value(node, inputs, "assignment_id")
    if not isinstance(assignment_id, int):
        raise BizError(422, "assignment_id 必须为整数")
    user = load_user(ctx.session_factory, ctx.user_id)
    with ctx.session_factory() as db:
        detail = assessment_service.get_result(db, assignment_id, user)
        result = detail["result"]
        return {
            "assignment_id": assignment_id,
            "total_score": float(result.total_score) if result.total_score is not None else None,
            "dimension_scores": list(result.dimension_scores_json or []),
            "result_version": result.version,
            "published": bool(detail.get("published")),
        }


# ---- training_suggestion：AI 培训草稿（C） ----

def training_suggestion(
    ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    from app.modules.training.schemas import DraftRequest  # noqa: PLC0415
    from app.modules.training import service as training_service  # noqa: PLC0415

    employee_user_id = _config_value(node, inputs, "employee_user_id")
    if not isinstance(employee_user_id, int):
        raise BizError(422, "employee_user_id 必须为整数")
    config = node.get("config") or {}
    payload = DraftRequest(
        employee_user_id=employee_user_id,
        talent_profile_id=config.get("talent_profile_id") or inputs.get("talent_profile_id"),
        job_id=config.get("job_id") or inputs.get("job_id"),
        target_job_title=config.get("target_job_title") or inputs.get("target_job_title"),
        dimension_gaps=config.get("dimension_gaps") or inputs.get("dimension_gaps") or [],
        assessment_assignment_id=(
            config.get("assessment_assignment_id") or inputs.get("assessment_assignment_id")
        ),
        profile_summary=config.get("profile_summary") or inputs.get("profile_summary"),
    )
    user = load_user(ctx.session_factory, ctx.user_id)
    with ctx.session_factory() as db:
        draft = training_service.create_ai_draft(db, payload, admin=user)
    return {
        "generated": bool(draft.get("generated")),
        "reason": draft.get("reason", ""),
        "gap_message": draft.get("gap_message", ""),
        "plan": draft.get("plan"),
    }


# ---- data_analysis：registry 准入指标查询（D） ----

def data_analysis(ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from app.modules.analytics import service as analytics_service  # noqa: PLC0415

    metric_id = _config_value(node, inputs, "metric_id")
    if not isinstance(metric_id, str) or not metric_id:
        raise BizError(422, "metric_id 必须为非空字符串")
    config = node.get("config") or {}
    params = dict(config.get("params") or {})
    for item in inputs:
        if item not in params and item not in ("metric_id", "group_by"):
            params[item] = inputs[item]
    group_by = config.get("group_by") or inputs.get("group_by")
    user = load_user(ctx.session_factory, ctx.user_id)
    # query_metric 为 async；节点执行面是同步（FastAPI sync endpoint 线程池内无事件循环）
    result = asyncio.run(
        analytics_service.query_metric(user, metric_id, params=params, group_by=group_by)
    )
    return {
        "metric_id": result.metric_id,
        "name": result.name,
        "value": result.value,
        "source": result.source,
        "cache_hit": result.cache_hit,
        "data_time": result.data_time.isoformat(),
        "snapshot_id": result.snapshot_id,
        "table": result.table,
        "note": result.note,
    }


# T-D17 执行器注册表（node_type → 执行函数；审批节点在 human_approval.py，由注册侧合并）
BUSINESS_EXECUTORS = {
    "profile_parse": profile_parse,
    "job_match": job_match,
    "assessment_analysis": assessment_analysis,
    "training_suggestion": training_suggestion,
    "data_analysis": data_analysis,
}
