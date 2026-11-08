"""人才画像与查重（T-17 查重 + T-18 画像 CRUD）：接口 §5 全部路由。

口径：`docs/接口设计.md` v1.3 §5；业务逻辑分别在 `app.service.dedup_service`（查重）与
`app.service.talent_service`（画像 CRUD），Controller 只做参数接收 + 统一返回（架构 §5.2）。

⚠️ **路由注册顺序**（FastAPI 按注册顺序匹配）：
1. `/duplicates`、`/duplicates/{id}/resolve`（T-17）在最前；
2. `/search`（T-19）**必须排在 `/{talent_id}` 之前**，否则会被
   `/{talent_id}` 吃掉并因 int 转换失败返回 422（接口 §7 实现注意第 1 条）；
3. `/{talent_id}` 系列路由在最后。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination, AccountScopeDep
from app.core.errors import HALF_WRITE_VECTOR_MESSAGE, BizError
from app.core.normalize import DEGREE_VALUES
from app.core.response import ok, paginated
from app.database import get_db
from app.schemas.talent import (
    DeleteTalentData,
    DuplicateItem,
    DuplicateResolveRequest,
    KeepResolveData,
    MergedProfile,
    MergeResolveData,
    PatchTalentData,
    RestoreTalentData,
    RevectorTalentData,
    TAG_TYPE_VALUES,
    TalentDetailData,
    TalentListItem,
    TalentPatchRequest,
    TalentSearchItem,
)
from app.service import dedup_service, talent_service
from app.modules.talents.access import require_manager, require_profile_reader
from app.core.auth.scope import get_account_scope

router = APIRouter(prefix="/api/talents", tags=["talents"])

DUPLICATE_STATUSES = ("pending", "merged", "kept")


@router.get("/duplicates", summary="查重待确认列表", dependencies=[Depends(require_manager)])
def list_duplicates(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    status: str = Query("pending", description="pending / merged / kept，默认 pending"),
):
    """`matched_value` 与 `parsed_resume.phone` 均为脱敏值，后端已处理，前端原样展示。"""
    if status not in DUPLICATE_STATUSES:
        raise BizError(400, f"status 只支持 {' / '.join(DUPLICATE_STATUSES)}")

    items, total = dedup_service.list_duplicates(
        db, status=status, offset=page.offset, limit=page.limit
    )
    return paginated(
        [DuplicateItem.model_validate(item).model_dump() for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/duplicates/{duplicate_id}/resolve", summary="处理查重：合并或保留", dependencies=[Depends(require_manager)])
def resolve_duplicate(
    duplicate_id: int,
    payload: DuplicateResolveRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """合并 200（向量失败仍 200 + `vector_status=failed`）；保留 202（后台补齐标签/向量）。

    已处理过的记录再调 → 400（接口 §5 错误约定）；记录不存在 → 404。
    """
    result = dedup_service.resolve(
        db,
        duplicate_id,
        action=payload.action,
        master_profile_id=payload.master_profile_id,
        note=payload.resolution_note,
        user_id=user.id,
    )

    if result.action == "merge":
        profile = result.profile
        data = MergeResolveData(
            duplicate_id=result.duplicate.id,
            status=result.duplicate.status,
            master_profile_id=profile.id,
            profile=MergedProfile(
                id=profile.id,
                name=profile.name,
                current_title=profile.current_title,
                degree=profile.degree,
                work_years=float(profile.work_years) if profile.work_years is not None else None,
                city=profile.city,
                skills=list(profile.skills_json or []),
                tags=list(profile.tags_json or []),
                vector_status=profile.vector_status,
                updated_at=profile.updated_at,
            ),
            vector_status=result.vector_status or profile.vector_status,
            task_id=result.task.id if result.task is not None else None,
            task_status=result.task.status if result.task is not None else None,
        )
        failed = data.vector_status == "failed"
        return ok(
            data.model_dump(),
            message=HALF_WRITE_VECTOR_MESSAGE if failed else "success",
        )

    data = KeepResolveData(
        duplicate_id=result.duplicate.id,
        status=result.duplicate.status,
        talent_profile_id=result.profile.id,
        task_id=result.task.id if result.task is not None else None,
        task_status=result.task.status if result.task is not None else None,
        stage=result.task.stage if result.task is not None else "profile",
    )
    return ok(data.model_dump(), status_code=202)


# =========================================================================== #
# T-18 画像 CRUD（接口 §5）。⚠️ /search（T-19）加入时必须插在 GET /{talent_id} 之前
# =========================================================================== #
@router.get("", summary="画像列表（默认未删除视角，deleted=true 为回收站）", dependencies=[Depends(require_manager)])
def list_talents(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    deleted: bool = Query(False, description="true = 回收站视角，只返回已软删除画像"),
    keyword: str | None = Query(None, max_length=100, description="姓名/职位/技能模糊"),
    tag: str | None = Query(None, max_length=100, description="标签精确匹配（走 talent_tags）"),
    tag_type: str | None = Query(None, max_length=20, description="标签类型过滤"),
    degree: str | None = Query(None, max_length=20, description="学历精确过滤"),
    min_years: float | None = Query(None, ge=0, le=80, description="年限下限（含）"),
    max_years: float | None = Query(None, ge=0, le=80, description="年限上限（含）"),
):
    if tag_type is not None and tag_type not in TAG_TYPE_VALUES:
        raise BizError(400, f"tag_type 仅支持 {'/'.join(TAG_TYPE_VALUES)}")
    if degree is not None and degree not in DEGREE_VALUES:
        raise BizError(400, f"degree 仅支持 {'/'.join(DEGREE_VALUES)}")
    if min_years is not None and max_years is not None and min_years > max_years:
        raise BizError(400, "min_years 不能大于 max_years")

    items, total = talent_service.list_talents(
        db,
        deleted=deleted,
        keyword=keyword,
        tag=tag,
        tag_type=tag_type,
        degree=degree,
        min_years=min_years,
        max_years=max_years,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated(
        [TalentListItem.model_validate(item).model_dump() for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/search", summary="语义检索（F2-5，T-19）", dependencies=[Depends(require_manager)])
def search_talents(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    query: str = Query(..., min_length=1, max_length=500, description="自然语言查询，≤500 字"),
    top_k: int = Query(20, ge=1, le=100, description="返回条数上限，默认 20"),
):
    """⚠️ **必须注册在 `/{talent_id}` 之前**（否则 "search" 会被 int 转换吃成 422）。

    Embedding/Milvus 不可用 → 503（本接口无业务写入，接口 §5 明确的 503 合法场景）。
    相同查询文本命中 embedding 短期缓存（300s TTL，命中不算真实调用）。
    """
    items = talent_service.search_talents(db, query=query, top_k=top_k)
    return ok({"items": [TalentSearchItem.model_validate(item).model_dump() for item in items]})


@router.get("/{talent_id}", summary="画像详情（经历类为聚合只读视图）", dependencies=[Depends(require_profile_reader)])
def get_talent(
    talent_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = talent_service.get_detail(db, talent_id)
    if not get_account_scope(user).can_view_all_persons:
        data = {**data, "screen_history": [], "match_history": []}
    return ok(TalentDetailData.model_validate(data).model_dump())


@router.patch("/{talent_id}", summary="人工修正画像（F2-6 字段级保护）", dependencies=[Depends(require_manager)])
def patch_talent(
    talent_id: int,
    payload: TalentPatchRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """经历类字段一期只读（D-J）：请求里出现即 400（在 Pydantic 校验前精确判定）。

    向量失败仍 200 + `vector_status:"failed"`（D-B 半写口径，业务字段已保存）。
    """
    readonly = [name for name in talent_service.READONLY_EXPERIENCE_FIELDS if name in payload.model_fields_set]
    if readonly:
        raise BizError(400, f"{'、'.join(readonly)} 为一期只读字段（经历由解析结果聚合展示），不可修改")

    data = talent_service.patch_profile(db, talent_id, payload, user_id=user.id)
    body = PatchTalentData.model_validate(data).model_dump()
    failed = body["vector_status"] == "failed"
    return ok(body, message=HALF_WRITE_VECTOR_MESSAGE if failed else "success")


@router.delete("/{talent_id}", summary="软删除画像（F2-7）", dependencies=[Depends(require_manager)])
def delete_talent(
    talent_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """MySQL 软删仍生效；Milvus 向量删除失败返回 `vector_deleted: false`（不阻塞）。"""
    data = talent_service.soft_delete(db, talent_id, user_id=user.id)
    return ok(DeleteTalentData.model_validate(data).model_dump())


@router.post("/{talent_id}/restore", summary="撤销软删除（D-H）", dependencies=[Depends(require_manager)])
def restore_talent(
    talent_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """恢复后按 hash 强制重嵌向量（软删时向量已删，必须 force）；半写口径适用。"""
    data = talent_service.restore(db, talent_id, user_id=user.id)
    body = RestoreTalentData.model_validate(data).model_dump()
    failed = body["vector_status"] == "failed"
    return ok(body, message=HALF_WRITE_VECTOR_MESSAGE if failed else "success")


@router.post("/{talent_id}/revector", summary="强制重建向量（D-B 补偿）", dependencies=[Depends(require_manager)])
def revector_talent(
    talent_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """只重算向量不写业务字段；Milvus/Embedding 不可用 → 503（接口 §5 的 503 合法场景）。"""
    data = talent_service.revector(db, talent_id)
    return ok(RevectorTalentData.model_validate(data).model_dump())


@router.get("/{talent_id}/resume-file", summary="下载画像关联的原始简历", dependencies=[Depends(require_profile_reader)])
def download_resume_file(
    talent_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    resume_file_id: int = Query(..., description="详情页 resume_files 列表里的文件 id"),
):
    """文件流下载：前端必须用 axios `responseType: 'blob'` 携带 Bearer（`<a>` 直链会 401）。

    文件必须属于该画像（防越权遍历他人文件）；文件名走 RFC 5987 `filename*`，
    ASCII 回退名兜底不支持 RFC 5987 的旧客户端。
    """
    file_row, path = talent_service.get_resume_file(db, talent_id, resume_file_id)
    # ASCII 回退名：stem 去 ASCII 外字符（纯中文 → 空 → "resume"），扩展名保留
    source = Path(file_row.filename)
    ascii_stem = source.stem.encode("ascii", "ignore").decode("ascii").strip()
    ascii_suffix = source.suffix.encode("ascii", "ignore").decode("ascii").strip()
    ascii_name = f"{ascii_stem or 'resume'}{ascii_suffix}"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(file_row.filename)}'
        )
    }
    return FileResponse(path, media_type="application/octet-stream", headers=headers)


@router.get("/{talent_id}/snapshot", dependencies=[Depends(require_profile_reader)])
def get_snapshot(talent_id: int, scope: AccountScopeDep,
                 db: Annotated[Session, Depends(get_db)], version: int | None = Query(None, ge=1)):
    return ok(talent_service.get_person_snapshot(db, talent_id, version=version, scope=scope))


@router.get("/{talent_id}/evidence", dependencies=[Depends(require_profile_reader)])
def get_evidence(talent_id: int, scope: AccountScopeDep,
                 db: Annotated[Session, Depends(get_db)], version: int | None = Query(None, ge=1)):
    return ok(talent_service.get_person_evidence(db, [talent_id], version=version, scope=scope))
