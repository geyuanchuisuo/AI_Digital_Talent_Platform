"""Skills 组件库路由（T-D19）：受控生命周期 → 提交 → 管理员审核发布/停用。

权限口径（与流程定义 T-D15 一致）：
- 组件/版本查看、创建、修改、提交：管理侧（can_view_all_persons）；
- 审核（通过/驳回）：管理侧 + 四眼原则（提交人不可自行审核，403）；
- 停用：管理侧。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.workflows import skills_service
from app.modules.workflows.models import SKILL_OUTPUT_PARSERS, SKILL_TOOLS

router = APIRouter(prefix="/api/workflows/skills", tags=["workflows-skills"])


@router.get("/meta", summary="组件库元数据（工具/解析器白名单、版本状态机）")
def skill_meta():
    return ok(
        {
            "tools": list(SKILL_TOOLS),
            "output_parsers": list(SKILL_OUTPUT_PARSERS),
        }
    )


@router.get("", summary="Skills 组件列表（管理侧）")
def list_skills(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    return ok([skills_service.to_skill_dict(s) for s in skills_service.list_skills(db)])


@router.post("", summary="新建 Skills 组件（管理侧；工具/解析器白名单校验）")
def create_skill(
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    skill = skills_service.create_skill(
        db,
        code=str(body.get("code", "")),
        name=str(body.get("name", "")),
        tool=str(body.get("tool") or "none"),
        output_parser=str(body.get("output_parser") or "raw_text"),
        description=body.get("description"),
        user=user,
    )
    return ok(skills_service.to_skill_dict(skill))


@router.get("/{skill_id}/versions", summary="组件版本列表（版本号倒序，管理侧）")
def list_skill_versions(
    skill_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    return ok(
        [skills_service.to_skill_version_dict(v) for v in skills_service.list_skill_versions(db, skill_id)]
    )


@router.post("/{skill_id}/versions", summary="新建草稿版本（管理侧；模板/参数 schema 校验）")
def create_skill_version(
    skill_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    schema = body.get("params_json_schema")
    if schema is not None and not isinstance(schema, dict):
        raise BizError(422, "params_json_schema 必须为对象")
    version = skills_service.create_skill_version(
        db,
        skill_id,
        prompt_template=str(body.get("prompt_template", "")),
        params_json_schema=schema,
        user=user,
        created_by=user.id,
        note=body.get("note"),
    )
    return ok(skills_service.to_skill_version_dict(version))


@router.put("/versions/{version_id}", summary="替换草稿版本内容（发布版本拒绝）")
def update_skill_version(
    version_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    schema = body.get("params_json_schema")
    if schema is not None and not isinstance(schema, dict):
        raise BizError(422, "params_json_schema 必须为对象")
    version = skills_service.update_draft_skill_version(
        db,
        version_id,
        prompt_template=str(body.get("prompt_template", "")),
        params_json_schema=schema,
        user=user,
    )
    return ok(skills_service.to_skill_version_dict(version))


@router.post("/versions/{version_id}/submit", summary="提交审核（管理侧）")
def submit_skill_version(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    version = skills_service.submit_skill_version(db, version_id, user=user)
    return ok(skills_service.to_skill_version_dict(version))


@router.post("/versions/{version_id}/review", summary="审核：approve=true 发布 / false 驳回（四眼原则）")
def review_skill_version(
    version_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    if "approve" not in body or not isinstance(body.get("approve"), bool):
        raise BizError(422, "approve 必填（布尔）")
    version = skills_service.review_skill_version(
        db,
        version_id,
        user=user,
        approve=bool(body["approve"]),
        note=body.get("note"),
    )
    return ok(skills_service.to_skill_version_dict(version))


@router.post("/versions/{version_id}/deactivate", summary="停用发布版本（已引用运行不受影响）")
def deactivate_skill_version(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    version = skills_service.deactivate_skill_version(db, version_id, user=user)
    return ok(skills_service.to_skill_version_dict(version))


@router.get("/versions/{version_id}/resolve", summary="编排引用解析（仅发布版本；返回四件套快照）")
def resolve_skill_version(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    skills_service._require_management(user)
    return ok(skills_service.resolve_published_skill(db, skill_version_id=version_id))
