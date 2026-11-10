"""系统管理域·账号花名册（A06-2：部门行级过滤，V2.6 冻结口径 / design §8.3）。

门禁（A06-1 角色门禁）：
- 账号列表 ``GET /api/system/accounts`` 接 ``require_roles("system_admin", "dept_manager")``：
  * system_admin：看全部账号；
  * dept_manager：仅看本部门（users.department_id 在本人可见部门集合内，含跨部门授权）。
- 部门指派 ``PUT /api/system/accounts/{user_id}/department`` 仅 system_admin 可执行
  （延续 A06-4 整域 system_admin 写口径，dept_manager 只读本部门）。

部门行级过滤的口径来自 core/auth/scope 的 resolve_data_scope / get_visible_department_ids，
是下游（B/C/D）数据范围过滤的可复用基础；本路由是其在 A 自有账号管理面的首个落地。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination, require_roles
from app.core.auth.scope import (
    apply_department_filter,
    get_visible_department_ids,
    resolve_visible_fields,
    serialize_fields,
)
from app.core.errors import BizError
from app.core.response import ok, paginated
from app.database import get_db
from app.models import User
from app.modules.system.audit_service import record_operation
from app.modules.system.models import SysDepartment

router = APIRouter(prefix="/api/system/accounts", tags=["system-accounts"])

# 门禁依赖（仅起鉴权副作用；返回值不消费）
_GATE_ADMIN_DEPT = Depends(require_roles("system_admin", "dept_manager"))
_GATE_ADMIN = Depends(require_roles("system_admin"))


class DepartmentAssign(BaseModel):
    department_id: int


# A06-3：账号花名册响应按角色字段白名单裁剪（system_admin 全量、dept_manager 基础字段），
# 且 SENSITIVE_FIELDS（password_hash 等）是任何白名单都不可覆盖的硬底线。


@router.get("")
def list_accounts(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    _gate: Annotated[tuple[str, ...], _GATE_ADMIN_DEPT],
    page_params: PageParams = Depends(pagination),
    keyword: str | None = Query(None, description="按用户名/显示名模糊过滤"),
):
    """账号花名册（部门行级过滤）。

    system_admin 看全部；dept_manager 仅看本部门（含跨部门授权）。
    """
    visible = get_visible_department_ids(db, current_user)
    visible_fields = resolve_visible_fields(db, current_user, "accounts")
    base = select(User)
    base = apply_department_filter(base, User, visible)
    if keyword:
        like = f"%{keyword}%"
        base = base.where(User.username.like(like) | User.display_name.like(like))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(User.id.asc()).offset(page_params.offset).limit(page_params.limit)
    ).scalars().all()
    return paginated(
        [serialize_fields(r, visible_fields) for r in rows],
        total,
        page_params.page,
        page_params.page_size,
    )


@router.put("/{user_id}/department")
def set_user_department(
    user_id: int,
    payload: DepartmentAssign,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    _gate: Annotated[tuple[str, ...], _GATE_ADMIN],
):
    """指派账号所属部门（仅 system_admin）。无物理外键，service 层一致性保证。"""
    target = db.get(User, user_id)
    if target is None:
        raise BizError(404, "账号不存在")
    if db.get(SysDepartment, payload.department_id) is None:
        raise BizError(400, "部门不存在")
    target.department_id = payload.department_id
    db.commit()
    db.refresh(target)
    record_operation(
        db,
        user_id=current_user.id,
        username=getattr(current_user, "username", None),
        module="system.account",
        action="edit",
        method="PUT",
        path=f"/api/system/accounts/{user_id}/department",
        status="success",
        params_summary=f"department_id={payload.department_id}",
    )
    visible_fields = resolve_visible_fields(db, current_user, "accounts")
    return ok(serialize_fields(target, visible_fields))
