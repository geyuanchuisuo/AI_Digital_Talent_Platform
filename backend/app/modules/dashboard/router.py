"""AI 中台管理面路由（T-D20）：供应商 / Prompt 库 / 调用日志与看板 / 运行看板。

全部端点管理侧门禁（can_view_all_persons）。密钥**只写不读**——任何响应
不含 api_key（验收①，测试断言）；供应商启停/切换每次执行面实时读取（验收②）；
用量/错误看板与明细同源 ai_call_logs（验收③）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.dashboard import me, overview, service

router = APIRouter(prefix="/api/ai-platform", tags=["ai-platform"])


# ---- 管理员工作台（T-D22）----

@router.get("/admin-overview", summary="管理员工作台六类数量卡片（管理侧；实时查询）")
def admin_overview(user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    return ok(overview.admin_overview(db, user=user))


# ---- 个人工作台（T-D23）----

@router.get("/me-overview", summary="个人工作台三要素（personal 本人；实时查询）")
def me_overview(user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    me._require_personal(user)
    return ok(me.me_overview(db, user=user))


# ---- 模型供应商 ----

@router.get("/providers", summary="供应商列表（管理侧；不回显密钥）")
def list_providers(user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    service._require_management(user)
    return ok([service.to_provider_dict(r) for r in service.list_providers(db)])


@router.post("/providers", summary="新建供应商（管理侧；密钥仅落库）")
def create_provider(
    body: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    return ok(service.to_provider_dict(service.create_provider(
        db,
        name=str(body.get("name", "")),
        provider_type=str(body.get("provider_type") or "openai_compatible"),
        base_url=str(body.get("base_url", "")),
        model=str(body.get("model", "")),
        api_key=str(body.get("api_key", "")),
        timeout_seconds=body.get("timeout_seconds") if isinstance(body.get("timeout_seconds"), int) else 120,
        note=body.get("note"),
        user=user,
    )))


@router.patch("/providers/{provider_id}", summary="更新供应商（管理侧；api_key 传 null 不动，空串清除）")
def update_provider(
    provider_id: int, body: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    if not isinstance(body, dict):
        raise BizError(422, "body 须为对象")
    return ok(service.to_provider_dict(service.update_provider(
        db, provider_id, user=user,
        base_url=body.get("base_url"),
        model=body.get("model"),
        api_key=body.get("api_key") if "api_key" in body else None,
        timeout_seconds=body.get("timeout_seconds") if isinstance(body.get("timeout_seconds"), int) else None,
        note=body.get("note"),
    )))


@router.post("/providers/{provider_id}/enabled", summary="启停供应商（管理侧；停用默认时执行面回退 .env）")
def set_provider_enabled(
    provider_id: int, body: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    if not isinstance(body.get("enabled"), bool):
        raise BizError(422, "enabled 必填（布尔）")
    return ok(service.to_provider_dict(service.set_provider_enabled(
        db, provider_id, enabled=bool(body["enabled"]), user=user
    )))


@router.post("/providers/{provider_id}/default", summary="设默认执行面（管理侧；全表唯一）")
def set_default_provider(
    provider_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    return ok(service.to_provider_dict(service.set_default_provider(db, provider_id, user=user)))


# ---- Prompt 版本库 ----

@router.get("/prompts", summary="Prompt 版本列表（管理侧；可按 name 过滤）")
def list_prompts(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    name: str | None = Query(default=None),
):
    service._require_management(user)
    return ok([service.to_prompt_dict(r) for r in service.list_prompts(db, name=name)])


@router.post("/prompts", summary="新建 Prompt 草稿版本（管理侧；版本号 name 内自增）")
def create_prompt(body: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    return ok(service.to_prompt_dict(service.create_prompt(
        db,
        name=str(body.get("name", "")),
        content=str(body.get("content", "")),
        note=body.get("note"),
        user=user,
        created_by=user.id,
    )))


@router.patch("/prompts/{version_id}", summary="替换草稿内容（发布后 409）")
def update_prompt(
    version_id: int, body: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    return ok(service.to_prompt_dict(service.update_draft_prompt(
        db, version_id, content=str(body.get("content", "")), user=user
    )))


@router.post("/prompts/{version_id}/publish", summary="发布（同 name 其他发布版一并退役）")
def publish_prompt(version_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    return ok(service.to_prompt_dict(service.publish_prompt(db, version_id, user=user)))


# ---- 调用日志与看板 ----

@router.get("/usage", summary="用量/错误率看板（近 N 天按 purpose 聚合）")
def usage_summary(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
):
    service._require_management(user)
    return ok(service.usage_summary(db, days=days))


@router.get("/call-logs", summary="调用日志明细（看板数字可回查，验收③）")
def list_call_logs(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
    purpose: str | None = Query(default=None),
    result: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    service._require_management(user)
    return ok([
        service.to_call_log_dict(r)
        for r in service.list_call_logs(db, days=days, purpose=purpose, result=result, limit=limit)
    ])


@router.get("/agents/runs", summary="智能体运行看板（按工作流定义聚合，只读视图）")
def agent_run_dashboard(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
):
    service._require_management(user)
    return ok(service.agent_run_dashboard(db, days=days))
