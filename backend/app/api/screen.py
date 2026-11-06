"""简历筛选批次（T-24）：接口 §7 全部路由；业务在 `screen_service`，执行体在 `screen_worker`。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.errors import BizError
from app.core.response import ok, paginated
from app.database import get_db
from app.schemas.screen import (
    ManualVerdictRequest,
    ScreenBatchCreateRequest,
    ScreenBatchCreatedData,
    ScreenBatchDetail,
    ScreenBatchListItem,
    ScreenRetryData,
    ScreenResultItem,
)
from app.service import screen_service

router = APIRouter(prefix="/api/screen", tags=["screen"])


@router.post("/batches", summary="发起筛选批次（同步圈人 + 成员快照，202）")
def create_screen_batch(
    payload: ScreenBatchCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """同步阶段 ≤2s：圈定 `resolved_ids` 快照 + 硬门槛预检估 LLM 次数；执行由 TaskRunner 异步。"""
    data = screen_service.create_batch(
        db, payload.job_profile_id, payload.scope.model_dump(exclude_none=True), user_id=user.id
    )
    return ok(ScreenBatchCreatedData.model_validate(data).model_dump(), status_code=202)


@router.get("/batches", summary="筛选批次列表")
def list_screen_batches(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    job_profile_id: int | None = Query(None, ge=1),
):
    rows, total = screen_service.list_batches_with_title(
        db, job_profile_id=job_profile_id, offset=page.offset, limit=page.limit
    )
    items = [
        ScreenBatchListItem(
            batch_id=batch.id,
            job_profile_id=batch.job_profile_id,
            job_title=title,
            job_profile_version=batch.job_profile_version,
            status=batch.status,
            progress=batch.progress,
            total=batch.total,
            passed=batch.passed,
            pending_count=batch.pending_count,
            rejected=batch.rejected,
            retry_count=batch.retry_count,
            created_at=batch.created_at,
            finished_at=batch.finished_at,
        ).model_dump()
        for batch, title in rows
    ]
    return paginated(items, total=total, page=page.page, page_size=page.page_size)


@router.get("/batches/{batch_id}", summary="筛选批次详情（轮询）")
def get_screen_batch(
    batch_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = screen_service.get_batch_detail(db, batch_id)
    return ok(ScreenBatchDetail.model_validate(data).model_dump())


@router.post("/batches/{batch_id}/retry", summary="重试失败批次（复用原批次，D-E）")
def retry_screen_batch(
    batch_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = screen_service.retry_batch(db, batch_id)
    return ok(ScreenRetryData.model_validate(data).model_dump())


@router.get("/batches/{batch_id}/results", summary="筛选结果分页（硬淘汰置底）")
def list_screen_results(
    batch_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    verdict: str | None = Query(None, description="按有效结论过滤（manual_verdict ?? verdict）"),
    keyword: str | None = Query(None, max_length=50, description="姓名模糊"),
):
    # 批次存在性校验（404 早于空列表）
    if screen_service.get_batch_row(db, batch_id) is None:
        raise BizError(404, "筛选批次不存在")
    data = screen_service.list_results(
        db, batch_id=batch_id, verdict=verdict, keyword=keyword,
        offset=page.offset, limit=page.limit,
    )
    return paginated(
        [ScreenResultItem.model_validate(item).model_dump() for item in data["items"]],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.patch("/results/{result_id}", summary="人工改判（F1-10，撤销传 null）")
def patch_screen_result(
    result_id: int,
    payload: ManualVerdictRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = screen_service.set_manual_verdict(db, result_id, payload.manual_verdict, user_id=user.id)
    return ok(ScreenResultItem.model_validate(data).model_dump())
