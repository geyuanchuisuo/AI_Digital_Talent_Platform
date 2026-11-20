"""岗位-人才匹配批次（T-25）：接口 §8 全部路由；业务在 `match_service`，执行体在 `match_worker`。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, ManagementDep, PageParams, pagination
from app.core.errors import BizError
from app.core.response import ok, paginated
from app.database import get_db
from app.schemas.match import (
    MatchBatchCreateRequest,
    MatchBatchCreatedData,
    MatchBatchDetail,
    MatchBatchListItem,
    ManualVerdictRequest,
    MatchResultDetail,
    MatchResultItem,
    MatchRetryData,
)
from app.service import match_service

router = APIRouter(prefix="/api/match", tags=["match"])


@router.post("/batches", summary="发起匹配批次（同步圈人 + 成员快照，202）")
def create_match_batch(
    payload: MatchBatchCreateRequest,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    """岗位向量在批次内只算一次；Milvus/Embedding 不可用时降级执行（vector_degraded=1），不 503。"""
    data = match_service.create_batch(
        db,
        payload.job_profile_id,
        payload.scope.model_dump(exclude_none=True),
        user_id=user.id,
        idempotency_key=payload.idempotency_key,
    )
    return ok(MatchBatchCreatedData.model_validate(data).model_dump(), status_code=202)


@router.get("/batches", summary="匹配批次列表")
def list_match_batches(
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    job_profile_id: int | None = Query(None, ge=1),
):
    rows, total = match_service.list_batches_with_title(
        db, job_profile_id=job_profile_id, offset=page.offset, limit=page.limit
    )
    items = [
        MatchBatchListItem(
            batch_id=batch.id,
            job_profile_id=batch.job_profile_id,
            job_title=title,
            job_profile_version=batch.job_profile_version,
            status=batch.status,
            progress=batch.progress,
            total=batch.total,
            hard_failed=batch.hard_failed,
            retry_count=batch.retry_count,
            vector_degraded=bool(batch.vector_degraded),
            created_at=batch.created_at,
            finished_at=batch.finished_at,
        ).model_dump()
        for batch, title in rows
    ]
    return paginated(items, total=total, page=page.page, page_size=page.page_size)


@router.get("/batches/{batch_id}", summary="匹配批次详情（轮询）")
def get_match_batch(
    batch_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    data = match_service.get_batch_detail(db, batch_id)
    return ok(MatchBatchDetail.model_validate(data).model_dump())


@router.post("/batches/{batch_id}/retry", summary="重试失败批次（复用原批次，D-E）")
def retry_match_batch(
    batch_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    data = match_service.retry_batch(db, batch_id)
    return ok(MatchRetryData.model_validate(data).model_dump())


@router.get("/batches/{batch_id}/results", summary="匹配名次分页（硬淘汰置底，rank 查询时计算）")
def list_match_results(
    batch_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
):
    if match_service.get_batch_row(db, batch_id) is None:
        raise BizError(404, "匹配批次不存在")
    data = match_service.list_results(db, batch_id=batch_id, offset=page.offset, limit=page.limit)
    return paginated(
        [MatchResultItem.model_validate(item).model_dump() for item in data["items"]],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.get("/results/{result_id}", summary="匹配结果理由详情（F3-4/F3-5）")
def get_match_result(
    result_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    data = match_service.get_result_detail(db, result_id)
    return ok(MatchResultDetail.model_validate(data).model_dump())


@router.patch("/results/{result_id}", summary="人工推进标记（T-A41，撤销传 null）")
def patch_match_result(
    result_id: int,
    payload: ManualVerdictRequest,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    result = match_service.set_manual_verdict(db, result_id, payload.manual_verdict, user_id=user.id)
    return ok(MatchResultDetail.model_validate(match_service.get_result_detail(db, result.id)).model_dump())
