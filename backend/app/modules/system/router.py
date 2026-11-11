"""系统管理域 CRUD 路由（A06-4，V2.6 冻结口径 / design §8.3 sys_* 表体系）。

门禁：**整域 `require_roles("system_admin")`**（在 `api/deps.py` 的 A06-1 角色门禁上），
仅系统管理员可操作部门/角色/菜单/权限/账号绑定；hr/dept_manager/personal 一律 403，
未登录 401。这同时验证 A06-1 的精细角色门禁在真实 API 边界生效。

实现约束（架构 §5.2）：Controller 只做参数接收 + 统一返回（`ok`/`paginated`）；
高影响写操作经 `audit_service.record_operation` 落 `sys_operation_logs`（F09）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination, require_roles
from app.core.errors import BizError
from app.core.response import ok, paginated
from app.database import get_db
from app.models import User
from app.modules.system.audit_service import record_operation
from app.modules.system.models import (
    ACTIONS,
    DATA_SCOPES,
    MENU_TYPES,
    SysDepartment,
    SysMenu,
    SysOperationLog,
    SysRole,
    SysRolePermission,
    SysUserRole,
)
from app.modules.system.schemas import (
    DepartmentCreate,
    DepartmentUpdate,
    MenuCreate,
    MenuUpdate,
    RoleCreate,
    RolePermissionSet,
    RoleUpdate,
    UserRoleBind,
)

router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_roles("system_admin"))],
)


def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _audit(
    db: Session,
    user: User,
    module: str,
    action: str,
    path: str,
    method: str,
    summary: str | None = None,
) -> None:
    # 审计写失败不得影响业务响应（audit_service 内部已吞异常）
    record_operation(
        db,
        user_id=user.id,
        username=getattr(user, "username", None),
        module=module,
        action=action,
        method=method,
        path=path,
        status="success",
        params_summary=summary,
    )


def _get_or_404(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if obj is None:
        raise BizError(404, "记录不存在")
    return obj


# ---------------- 部门树 ----------------
@router.get("/departments")
def list_departments(
    db: Annotated[Session, Depends(get_db)],
    page_params: PageParams = Depends(pagination),
    status: str | None = Query(None, description="按状态过滤：active/disabled"),
):
    base = select(SysDepartment)
    if status:
        base = base.where(SysDepartment.status == status)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(SysDepartment.sort.asc(), SysDepartment.id.asc())
        .offset(page_params.offset)
        .limit(page_params.limit)
    ).scalars().all()
    return paginated([_row_to_dict(r) for r in rows], total, page_params.page, page_params.page_size)


@router.post("/departments")
def create_department(payload: DepartmentCreate, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    if db.scalar(select(func.count()).select_from(SysDepartment).where(SysDepartment.name == payload.name)):
        raise BizError(409, "部门名称已存在")
    obj = SysDepartment(**payload.model_dump())
    db.add(obj)
    db.flush()
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.department", "create", "/api/system/departments", "POST", payload.name)
    return ok(_row_to_dict(obj))


@router.get("/departments/{department_id}")
def get_department(department_id: int, db: Annotated[Session, Depends(get_db)]):
    return ok(_row_to_dict(_get_or_404(db, SysDepartment, department_id)))


@router.put("/departments/{department_id}")
def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    obj = _get_or_404(db, SysDepartment, department_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != obj.name:
        if db.scalar(select(func.count()).select_from(SysDepartment).where(SysDepartment.name == data["name"])):
            raise BizError(409, "部门名称已存在")
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.department", "edit", f"/api/system/departments/{department_id}", "PUT", payload.name)
    return ok(_row_to_dict(obj))


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    obj = _get_or_404(db, SysDepartment, department_id)
    db.delete(obj)
    db.commit()
    _audit(db, user, "system.department", "delete", f"/api/system/departments/{department_id}", "DELETE", obj.name)
    return ok({"id": department_id, "deleted": True})


# ---------------- 角色 ----------------
@router.get("/roles")
def list_roles(
    db: Annotated[Session, Depends(get_db)],
    page_params: PageParams = Depends(pagination),
    status: str | None = Query(None),
):
    base = select(SysRole)
    if status:
        base = base.where(SysRole.status == status)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(SysRole.id.asc()).offset(page_params.offset).limit(page_params.limit)
    ).scalars().all()
    return paginated([_row_to_dict(r) for r in rows], total, page_params.page, page_params.page_size)


@router.post("/roles")
def create_role(payload: RoleCreate, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    if payload.data_scope not in DATA_SCOPES:
        raise BizError(400, "data_scope 仅允许 all/dept/self")
    if db.scalar(select(func.count()).select_from(SysRole).where(SysRole.code == payload.code)):
        raise BizError(409, "角色编码已存在")
    obj = SysRole(**payload.model_dump())
    db.add(obj)
    db.flush()
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.role", "create", "/api/system/roles", "POST", payload.code)
    return ok(_row_to_dict(obj))


@router.get("/roles/{role_id}")
def get_role(role_id: int, db: Annotated[Session, Depends(get_db)]):
    return ok(_row_to_dict(_get_or_404(db, SysRole, role_id)))


@router.put("/roles/{role_id}")
def update_role(role_id: int, payload: RoleUpdate, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    obj = _get_or_404(db, SysRole, role_id)
    data = payload.model_dump(exclude_unset=True)
    if "data_scope" in data and data["data_scope"] not in DATA_SCOPES:
        raise BizError(400, "data_scope 仅允许 all/dept/self")
    if "code" in data and data["code"] != obj.code:
        if db.scalar(select(func.count()).select_from(SysRole).where(SysRole.code == data["code"])):
            raise BizError(409, "角色编码已存在")
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.role", "edit", f"/api/system/roles/{role_id}", "PUT", payload.code)
    return ok(_row_to_dict(obj))


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    obj = _get_or_404(db, SysRole, role_id)
    # 内置关键角色保护：系统管理员不可删，避免锁死管理入口
    if obj.code == "system_admin":
        raise BizError(409, "内置系统管理员角色不可删除")
    db.delete(obj)
    db.commit()
    _audit(db, user, "system.role", "delete", f"/api/system/roles/{role_id}", "DELETE", obj.code)
    return ok({"id": role_id, "deleted": True})


# ---------------- 账号↔角色 ----------------
@router.get("/roles/{role_id}/users")
def list_role_users(role_id: int, db: Annotated[Session, Depends(get_db)]):
    _get_or_404(db, SysRole, role_id)
    rows = db.execute(
        select(SysUserRole).where(SysUserRole.role_id == role_id).order_by(SysUserRole.id.asc())
    ).scalars().all()
    return ok([_row_to_dict(r) for r in rows])


@router.post("/roles/{role_id}/users")
def bind_user(role_id: int, payload: UserRoleBind, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    _get_or_404(db, SysRole, role_id)
    target_user = db.get(User, payload.user_id)
    if target_user is None:
        raise BizError(404, "账号不存在")
    if db.scalar(
        select(func.count())
        .select_from(SysUserRole)
        .where(SysUserRole.user_id == payload.user_id, SysUserRole.role_id == role_id)
    ):
        raise BizError(409, "该账号已绑定此角色")
    link = SysUserRole(user_id=payload.user_id, role_id=role_id)
    target_user.data_scope_mode = "scoped"
    db.add(link)
    db.commit()
    _audit(db, user, "system.role_user", "create", f"/api/system/roles/{role_id}/users", "POST", str(payload.user_id))
    return ok(_row_to_dict(link))


@router.delete("/roles/{role_id}/users/{user_id}")
def unbind_user(role_id: int, user_id: int, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    _get_or_404(db, SysRole, role_id)
    link = db.scalar(
        select(SysUserRole).where(SysUserRole.role_id == role_id, SysUserRole.user_id == user_id)
    )
    if link is None:
        raise BizError(404, "绑定关系不存在")
    target_user = db.get(User, user_id)
    if target_user is not None:
        target_user.data_scope_mode = "scoped"
    db.delete(link)
    db.commit()
    _audit(db, user, "system.role_user", "delete", f"/api/system/roles/{role_id}/users/{user_id}", "DELETE", str(user_id))
    return ok({"role_id": role_id, "user_id": user_id, "deleted": True})


# ---------------- 角色权限矩阵 ----------------
@router.get("/roles/{role_id}/permissions")
def list_role_permissions(role_id: int, db: Annotated[Session, Depends(get_db)]):
    _get_or_404(db, SysRole, role_id)
    rows = db.execute(
        select(SysRolePermission).where(SysRolePermission.role_id == role_id).order_by(SysRolePermission.id.asc())
    ).scalars().all()
    return ok([_row_to_dict(r) for r in rows])


@router.put("/roles/{role_id}/permissions")
def set_role_permissions(
    role_id: int,
    payload: RolePermissionSet,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    _get_or_404(db, SysRole, role_id)
    for item in payload.permissions:
        if item.action not in ACTIONS:
            raise BizError(400, f"action 仅允许 {', '.join(ACTIONS)}")
        if item.data_scope not in DATA_SCOPES:
            raise BizError(400, "data_scope 仅允许 all/dept/self")
    # 全量替换（幂等）：先清后写
    existing = db.execute(
        select(SysRolePermission).where(SysRolePermission.role_id == role_id)
    ).scalars().all()
    for e in existing:
        db.delete(e)
    for item in payload.permissions:
        db.add(
            SysRolePermission(
                role_id=role_id,
                menu_id=item.menu_id,
                action=item.action,
                data_scope=item.data_scope,
            )
        )
    db.commit()
    _audit(db, user, "system.role_perm", "edit", f"/api/system/roles/{role_id}/permissions", "PUT", f"{len(payload.permissions)} 条")
    rows = db.execute(
        select(SysRolePermission).where(SysRolePermission.role_id == role_id)
    ).scalars().all()
    return ok([_row_to_dict(r) for r in rows])


# ---------------- 菜单树 ----------------
@router.get("/menus")
def list_menus(
    db: Annotated[Session, Depends(get_db)],
    page_params: PageParams = Depends(pagination),
    status: str | None = Query(None),
):
    base = select(SysMenu)
    if status:
        base = base.where(SysMenu.status == status)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(SysMenu.sort.asc(), SysMenu.id.asc()).offset(page_params.offset).limit(page_params.limit)
    ).scalars().all()
    return paginated([_row_to_dict(r) for r in rows], total, page_params.page, page_params.page_size)


@router.post("/menus")
def create_menu(payload: MenuCreate, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    if payload.menu_type not in MENU_TYPES:
        raise BizError(400, "menu_type 仅允许 catalog/menu/button")
    obj = SysMenu(**payload.model_dump())
    db.add(obj)
    db.flush()
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.menu", "create", "/api/system/menus", "POST", payload.name)
    return ok(_row_to_dict(obj))


@router.get("/menus/{menu_id}")
def get_menu(menu_id: int, db: Annotated[Session, Depends(get_db)]):
    return ok(_row_to_dict(_get_or_404(db, SysMenu, menu_id)))


@router.put("/menus/{menu_id}")
def update_menu(menu_id: int, payload: MenuUpdate, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    obj = _get_or_404(db, SysMenu, menu_id)
    data = payload.model_dump(exclude_unset=True)
    if "menu_type" in data and data["menu_type"] not in MENU_TYPES:
        raise BizError(400, "menu_type 仅允许 catalog/menu/button")
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    _audit(db, user, "system.menu", "edit", f"/api/system/menus/{menu_id}", "PUT", payload.name)
    return ok(_row_to_dict(obj))


@router.delete("/menus/{menu_id}")
def delete_menu(menu_id: int, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    obj = _get_or_404(db, SysMenu, menu_id)
    db.delete(obj)
    db.commit()
    _audit(db, user, "system.menu", "delete", f"/api/system/menus/{menu_id}", "DELETE", obj.name)
    return ok({"id": menu_id, "deleted": True})


# ---------------- 操作审计（只读） ----------------
@router.get("/operation-logs")
def list_operation_logs(
    db: Annotated[Session, Depends(get_db)],
    page_params: PageParams = Depends(pagination),
    module: str | None = Query(None),
    action: str | None = Query(None),
):
    base = select(SysOperationLog)
    if module:
        base = base.where(SysOperationLog.module == module)
    if action:
        base = base.where(SysOperationLog.action == action)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(SysOperationLog.id.desc()).offset(page_params.offset).limit(page_params.limit)
    ).scalars().all()
    return paginated([_row_to_dict(r) for r in rows], total, page_params.page, page_params.page_size)
