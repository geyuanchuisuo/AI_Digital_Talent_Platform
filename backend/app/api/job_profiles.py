"""岗位条件（T-22）：接口 §6 全部路由；业务在 `job_profile_service`。

与 talents 一样是纯 REST 前缀（`/api/job-profiles`），无动态段冲突。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, ManagementDep, PageParams, pagination
from app.core.errors import BizError
from app.core.response import ok, paginated
from app.database import get_db
from app.schemas.job_profile import (
    JobProfileDeleteData,
    JobProfileJdDraftRequest,
    JobProfileListItem,
    JobProfileOut,
    JobProfileWriteRequest,
)
from app.service import job_profile_service

router = APIRouter(prefix="/api/job-profiles", tags=["job-profiles"])


def _out(row) -> dict:
    """完整对象（POST / GET{id} / PUT 响应）。"""
    return JobProfileOut(
        id=row.id,
        title=row.title,
        department=row.department,
        jd_text=row.jd_text,
        requirements_text=row.requirements_text,
        min_degree=row.min_degree,
        min_work_years=float(row.min_work_years) if row.min_work_years is not None else None,
        must_skills=list(row.must_skills_json or []),
        bonus_skills=list(row.bonus_skills_json or []),
        hard_rule_switches=dict(row.hard_rule_switches_json),
        pass_threshold=float(row.pass_threshold),
        pending_threshold=float(row.pending_threshold),
        weights=dict(row.weights_json),
        version=row.version,
        is_active=bool(row.is_active),
        created_by=row.created_by,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    ).model_dump()


def _item(row) -> dict:
    """列表项（不含 jd/requirements 全文，接口 §6）。"""
    return JobProfileListItem(
        id=row.id,
        title=row.title,
        department=row.department,
        min_degree=row.min_degree,
        min_work_years=float(row.min_work_years) if row.min_work_years is not None else None,
        must_skills=list(row.must_skills_json or []),
        bonus_skills=list(row.bonus_skills_json or []),
        version=row.version,
        is_active=bool(row.is_active),
        updated_at=row.updated_at,
    ).model_dump()


@router.get("", summary="岗位条件列表（不含全文）")
def list_job_profiles(
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    keyword: str | None = Query(None, max_length=100, description="岗位名/部门模糊"),
    is_active: bool | None = Query(None, description="按启用状态过滤；缺省返回全部"),
):
    rows, total = job_profile_service.list_jobs(
        db, keyword=keyword, is_active=is_active, offset=page.offset, limit=page.limit
    )
    return paginated([_item(row) for row in rows], total=total, page=page.page, page_size=page.page_size)


@router.post("", summary="新建岗位条件")
def create_job_profile(
    payload: JobProfileWriteRequest,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    """校验统一 400（weights 总和 / pass>pending / min_degree 六档字面值）；技能归一化后存储。"""
    row = job_profile_service.create_job(db, payload, user_id=user.id)
    return ok(_out(row))


@router.post("/jd-draft", summary="AI 生成 JD 草稿（真实模型；失败 502 明确报错，不返回样例）")
def create_jd_draft(
    payload: JobProfileJdDraftRequest,
    user: ManagementDep,
):
    """T-A22：草稿不落库，人工编辑确认后随岗位 POST/PUT 保存（jd_text 承担「保存后可查」）。"""
    return ok(job_profile_service.generate_jd_draft(payload))


@router.get("/{job_id}", summary="岗位条件详情（完整配置）")
def get_job_profile(
    job_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    row = job_profile_service.get_job(db, job_id)
    return ok(_out(row))


@router.put("/{job_id}", summary="全量更新岗位条件（version+1）")
def put_job_profile(
    job_id: int,
    payload: JobProfileWriteRequest,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    """历史批次读快照不受影响（数据库 §6.1）；响应含新 version。"""
    row = job_profile_service.put_job(db, job_id, payload, user_id=user.id)
    return ok(_out(row))


@router.delete("/{job_id}", summary="删除岗位条件（有批次引用软删）")
def delete_job_profile(
    job_id: int,
    user: ManagementDep,
    db: Annotated[Session, Depends(get_db)],
):
    """存在筛选/匹配批次引用 → `is_active=0` 软删（`soft_deleted:true`）；无引用 → 物理删除；重复删除 404。"""
    data = job_profile_service.delete_job(db, job_id)
    return ok(JobProfileDeleteData.model_validate(data).model_dump())
