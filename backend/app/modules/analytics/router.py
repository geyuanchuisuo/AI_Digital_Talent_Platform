"""问数域路由（T-D01b 切片 + T-D08 registry 化）。

- GET /api/analytics/metrics                       指标目录（registry 全量，管理侧）
- GET /api/analytics/metrics/{metric_id}           指标查询（registry 准入，含 403/422 前置）
- GET /api/analytics/metrics/jobs/active[...]      切片兼容端点（含 SSE 首用）

静态路由声明在 /metrics/{metric_id} 之前；鉴权/准入先于任何统计（验收④）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.modules.analytics import service
from app.modules.analytics.nl2sql import service as service_nl2sql

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _result_payload(result) -> dict:
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


@router.get("/metrics", summary="指标目录（registry 全量，管理侧）")
async def list_metrics(user: CurrentUser):
    if not get_account_scope(user).can_view_all_persons:
        raise BizError(403, "仅管理侧可查看指标目录")
    return {"code": 0, "message": "success", "data": {"items": service.list_registry()}}


@router.get("/metrics/jobs/active", summary="活跃岗位数（管理侧指标，Redis 缓存）")
async def get_active_jobs(user: CurrentUser):
    result = await service.get_jobs_active(user)
    return {"code": 0, "message": "success", "data": _result_payload(result)}


@router.get("/metrics/jobs/active/events", summary="活跃岗位数计算进度（SSE 首用）")
async def get_active_jobs_events(user: CurrentUser):
    """SSE 进度流：started → computed → done。鉴权先于流（403 不进流）。"""
    initial = await service.get_jobs_active(user)

    async def event_stream():
        yield _sse("progress", {"stage": "started", "metric_id": initial.metric_id})
        yield _sse(
            "progress",
            {
                "stage": "computed",
                "value": initial.value,
                "source": initial.source,
                "cache_hit": initial.cache_hit,
            },
        )
        yield _sse(
            "done",
            {
                "value": initial.value,
                "data_time": initial.data_time.isoformat(),
                "snapshot_id": initial.snapshot_id,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/metrics/{metric_id}", summary="指标查询（registry 准入；B/C 待口径指标拒绝）")
async def query_metric(
    metric_id: str,
    user: CurrentUser,
    project_group_id: int | None = Query(None, description="job.demand_progress_gap 分组过滤"),
    job_profile_id: int | None = Query(None, description="match.threshold_reference 过滤"),
    bypass_cache: bool = Query(False, description="跳过缓存直查（调试用）"),
):
    params = {
        k: v
        for k, v in {"project_group_id": project_group_id, "job_profile_id": job_profile_id}.items()
        if v is not None
    }
    result = await service.query_metric(user, metric_id, params=params, bypass_cache=bypass_cache)
    return {"code": 0, "message": "success", "data": _result_payload(result)}


@router.post("/nl2sql/query", summary="自由问数（生成→校验→只读执行→审计，管理侧）")
async def nl2sql_query(user: CurrentUser, body: dict):
    question = (body or {}).get("question", "")
    result = await service_nl2sql.nl2sql_query(user, question)
    return {"code": 0, "message": "success", "data": result}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
