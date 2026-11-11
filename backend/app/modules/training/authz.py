"""C 域职责与负责范围门禁（消费 A06 角色/部门范围契约，C 不自行放宽）。

三档视角：管理侧（system_admin/hr）、部门经理（dept_manager）、员工本人。
部门范围一律取 A 的 `get_visible_department_ids`：`None` 表示全量，空元组表示
无可见部门（deny-by-default），不在此处另造范围规则。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.scope import get_role_codes, get_visible_department_ids, resolve_data_scope
from app.core.errors import BizError
from app.models import User

MANAGEMENT = "management"
MANAGER = "manager"
EMPLOYEE = "employee"

DENY_MESSAGE = "无权访问该资源"
OUT_OF_SCOPE_MESSAGE = "超出负责部门范围"


def viewer_tier(db: Session, user: User) -> str:
    codes = set(get_role_codes(db, user.id))
    if codes & {"system_admin", "hr"}:
        return MANAGEMENT
    if "dept_manager" in codes:
        return MANAGER
    return EMPLOYEE


def is_management(db: Session, user: User) -> bool:
    return viewer_tier(db, user) == MANAGEMENT


def is_manager(db: Session, user: User) -> bool:
    return viewer_tier(db, user) == MANAGER


def can_view_internal(db: Session, user: User) -> bool:
    """内部评级/九宫格可见性：管理侧与负责部门经理可见，员工不可见（F10）。"""
    return viewer_tier(db, user) in (MANAGEMENT, MANAGER)


def visible_department_ids(db: Session, user: User) -> tuple[int, ...] | None:
    """`None`=全量授权；空元组=无可见部门。

    A 契约里 `self` 范围也返回 None（由调用方做本人判定），这里不能把经理的
    self 回退当成全量放行：self → 空元组，跨人访问一律拒绝。
    """
    scope = resolve_data_scope(db, user)
    if scope == "all":
        return None
    if scope == "self":
        return ()
    return get_visible_department_ids(db, user)


def require_employee_in_scope(db: Session, user: User, employee_user_id: int) -> User:
    """校验调用者对目标员工的负责范围；员工仅本人，经理仅本部门，管理侧按授权范围。"""
    emp = db.get(User, employee_user_id)
    if emp is None:
        raise BizError(404, "员工账号不存在")
    tier = viewer_tier(db, user)
    if tier == EMPLOYEE:
        if emp.id != user.id:
            raise BizError(403, DENY_MESSAGE)
        return emp
    visible = visible_department_ids(db, user)
    if visible is None:
        return emp
    dept = getattr(emp, "department_id", None)
    if dept is None or int(dept) not in visible:
        raise BizError(403, OUT_OF_SCOPE_MESSAGE)
    return emp


def scoped_employee_ids(db: Session, user: User) -> list[int] | None:
    """列表查询用的员工 id 白名单；`None` 表示不限（管理侧全量授权）。"""
    visible = visible_department_ids(db, user)
    if visible is None:
        return None
    if not visible:
        return []
    return list(db.scalars(select(User.id).where(User.department_id.in_(visible))))