"""问数域路由（T-D01b 切片）：指标端点 + SSE 进度端点首用。

- GET /api/analytics/metrics/jobs/active：JSON 一次取值（含缓存命中标记）
- GET /api/analytics/metrics/jobs/active/events：SSE 流式进度（started → computed → done），
  用 StreamingResponse 实现（不依赖 sse-starlette——T-D06 正式任务再评估引入）；
  鉴权在流开始前完成（personal → 403，不进入流）；终态（done 事件）在快照落库后发出，
  断线重连重新订阅即重新计算并再次落快照（切片口径；断线补发归 T-D06 正式设计）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.modules.analytics import service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/metrics/jobs/active", summary="活跃岗位数（管理侧指标，Redis 缓存）")
async def get_active_jobs(user: CurrentUser):
    result = await service.get_jobs_active(user)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "metric_id": result.metric_id,
            "name": result.name,
            "value": result.value,
            "source": result.source,
            "cache_hit": result.cache_hit,
            "data_time": result.data_time.isoformat(),
            "snapshot_id": result.snapshot_id,
        },
    }


@router.get("/metrics/jobs/active/events", summary="活跃岗位数计算进度（SSE 首用）")
async def get_active_jobs_events(user: CurrentUser):
    """SSE 进度流：started → computed → done。

    鉴权先于流（403 不进流）；每个事件均为真实阶段，不虚构百分比。
    """
    # 鉴权/计算在流开始前完成（403 不进流；事件回放真实发生的阶段，不虚构进度）
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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
