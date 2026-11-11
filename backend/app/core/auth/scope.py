"""账号数据范围授权（REQ-B-20261106-02 阶段 1 契约，A 维护；B/C/D 只消费）。

阶段 1 三值角色：`admin`/`hr` 管理侧（可看全量）、`personal` 个人账号（仅本人）。
`users.role` 现值只有 "hr"；"personal" 由 seed 样例账号与后续双端入口引入。
未知 role 值按 deny-by-default 处理（映射为未绑定 personal），不越权放行。

扩展路径（V2.5 六类角色冻结后）：`AccountScope` 增 `department_ids` 与
`granted_scope`（跨部门授权记录表 `user_scope_grants` 由 A 建表），本文件的
映射函数是唯一改动点，消费方（B/C/D）接口形状不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ScopeRole = Literal["admin", "hr", "personal"]
KnowledgeScope = Literal["public", "admin"]

#: 六类角色代码（与 `scripts/init_db.py::SEED_ROLES` 及 sys_roles.code 对齐；
#: 服务端判定以 `sys_user_roles` 聚合为准，users.role 仅作无角色记录时的回退）。
ROLE_CODES = ("system_admin", "hr", "dept_manager", "trainer", "employee", "candidate")

#: 管理侧角色：系统管理员 + HR。A06 职责门禁的统一口径（替代散落的
#: `can_view_all_persons` / `ADMIN_ROLES` 字符串判断），修复 system_admin 因
#: users.role 映射为 personal 被误拒的漏洞。
MANAGEMENT_ROLES = ("system_admin", "hr")

#: 统一拒绝文案：不泄露对象是否存在（REQ-02 验收口径）
DENY_MESSAGE = "无权访问该资源"


@dataclass(frozen=True)
class AccountScope:
    """账号可访问的数据范围；B/C/D 按此先过滤再查询/召回。

    组织范围扩展（V2.6 冻结解锁，2026-11-07）：`department_ids` 为账号可看的
    部门集合（含跨部门授权聚合）；`granted_scope` 为授权记录摘要（供审计与
    D 的组织范围问数）。阶段 1 三值 role 不变；六类角色矩阵随 sys_* 体系细化。
    """

    role: ScopeRole
    person_id: int | None
    can_view_all_persons: bool
    knowledge_scope: KnowledgeScope
    department_ids: tuple[int, ...] = ()
    granted_scope: tuple[dict, ...] = ()
    allowed_person_ids: tuple[int, ...] | None = None
    #: 账号生效角色代码集合（sys_user_roles 聚合；空=未绑定 sys 角色，回退 users.role 简值）。
    #: 统一采用公共 A06 `get_role_codes` 的命名口径（原合并候选的 `roles` 同义字段已并入此处）。
    #: A06 职责门禁的权威来源；默认空元组以保持既有纯函数构造向后兼容。
    role_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileAccessDecision:
    """文件访问授权结果；deny 时 reason 统一，不区分「不存在/无权」。"""

    allowed: bool
    reason: str | None = None


def get_account_scope(user) -> AccountScope:
    """从 User 行（或同形对象：id/role/person_id）计算数据范围。

    个人范围直接取绑定；管理范围通过公共A06角色/部门查询及B人员范围适配计算。
    """
    role = (getattr(user, "role", "") or "").strip().lower()
    person_id = getattr(user, "person_id", None)
    if not getattr(user,'is_active',True):
        return AccountScope('personal',None,False,'public',allowed_person_ids=())

    if role == "personal":
        # 个人账号：仅本人（person_id 未绑定＝无任何数据范围）
        return AccountScope(
            role="personal",
            person_id=person_id,
            can_view_all_persons=False,
            knowledge_scope="public",
        )
    codes = ()
    if role in ('hr', 'system_admin'):
        try:
            from app.database import SessionLocal
            with SessionLocal() as db:
                codes = get_role_codes(db, getattr(user, 'id', None))
        except Exception:
            codes = ()
    if role == "hr" or (role == 'system_admin' and set(codes) & set(MANAGEMENT_ROLES)):
        try:
            all_people,departments,people,grants=_management_range(getattr(user,'id',None),person_id)
        except Exception:
            all_people,departments,people,grants=False,(),(),()
        # 阶段 1 管理侧：现有 hr 即全量；六类角色冻结后按职责收窄。
        # 组织范围扩展：管理侧也聚合其名下的授权记录（审计可见）。
        return AccountScope(
            role="hr",
            person_id=person_id if not all_people else None,
            can_view_all_persons=all_people,
            knowledge_scope="admin",
            department_ids=departments,
            granted_scope=grants,
            allowed_person_ids=people,
            role_codes=codes,
        )
    # 未知角色 deny-by-default：映射为未绑定 personal
    return AccountScope(
        role="personal",
        person_id=None,
        can_view_all_persons=False,
        knowledge_scope="public",
    )


def authorize_profile_access(scope: AccountScope, talent_profile_id: int | None) -> FileAccessDecision:
    """判断账号是否可访问某人才主档（person_id = talent_profiles.id）。"""
    if scope.can_view_all_persons:
        return FileAccessDecision(allowed=True)
    if scope.allowed_person_ids is not None:
        return FileAccessDecision(allowed=talent_profile_id in scope.allowed_person_ids,reason=DENY_MESSAGE)
    if scope.person_id is not None and talent_profile_id is not None and scope.person_id == talent_profile_id:
        return FileAccessDecision(allowed=True)
    return FileAccessDecision(allowed=False, reason=DENY_MESSAGE)


def authorize_file_access(
    scope: AccountScope,
    talent_profile_id: int | None,
    action: Literal["view", "download"] = "view",
) -> FileAccessDecision:
    """判断账号是否可查看/下载某简历原件。

    `talent_profile_id` 为该文件关联的主档 id（调用方从 resume_files →
    parsed_resumes.talent_profile_id 解析；未解析/未建档的文件传 None，
    个人账号一律拒绝——无归属信息不开放）。管理侧不受 action 限制；
    个人账号 view/download 同权限（均为本人原件）。
    """
    del action  # 阶段 1 两种 action 权限一致；保留参数为后续细分留位
    return authorize_profile_access(scope, talent_profile_id)


def _active_department_ids(user_id: int | None) -> tuple[int, ...]:
    """聚合账号当前有效的部门范围授权（未撤销且在有效期内）；不连库时返回空。"""
    if user_id is None:
        return ()
    try:
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.modules.recruitment.scope_models import UserScopeGrant

        now = datetime.now()
        with SessionLocal() as db:
            rows = db.execute(
                select(UserScopeGrant.scope_ref_id).where(
                    UserScopeGrant.grantee_user_id == user_id,
                    UserScopeGrant.scope_type == "department",
                    UserScopeGrant.revoked_at.is_(None),
                    UserScopeGrant.valid_from <= now,
                    (UserScopeGrant.valid_until.is_(None) | (UserScopeGrant.valid_until > now)),
                )
            ).scalars().all()
        return tuple(sorted({int(r) for r in rows if r is not None}))
    except Exception:
        return ()


def _active_grant_summaries(user_id: int | None) -> tuple[dict, ...]:
    """账号当前有效授权记录摘要（scope_type/ref/valid_until），供审计展示。"""
    if user_id is None:
        return ()
    try:
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.modules.recruitment.scope_models import UserScopeGrant

        now = datetime.now()
        with SessionLocal() as db:
            rows = db.execute(
                select(UserScopeGrant).where(
                    UserScopeGrant.grantee_user_id == user_id,
                    UserScopeGrant.revoked_at.is_(None),
                    UserScopeGrant.valid_from <= now,
                    (UserScopeGrant.valid_until.is_(None) | (UserScopeGrant.valid_until > now)),
                )
            ).scalars().all()
        return tuple(
            {
                "scope_type": r.scope_type,
                "scope_ref_id": r.scope_ref_id,
                "valid_until": r.valid_until.isoformat(timespec="seconds") if r.valid_until else None,
            }
            for r in rows
        )
    except Exception:
        return ()


def _management_range(user_id, person_id):
    """B person allow-list adapter; shared A06 functions own role/department policy."""
    from sqlalchemy import select, or_
    from app.database import SessionLocal
    from app.models.user import User
    from app.models.talent import TalentProfile
    from app.modules.system.models import SysRole, SysUserRole, SysDepartment
    from app.modules.recruitment.scope_models import UserScopeGrant
    if user_id is None:
        return True, (), None, ()  # Legacy lightweight callers; not a lookup-error fallback.
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active or user.data_scope_mode not in ('legacy', 'scoped'):
            return False, (), (), ()
        assignments = list(db.scalars(select(SysUserRole).where(SysUserRole.user_id == user_id)))
        active_roles = list(db.scalars(select(SysRole).where(
            SysRole.id.in_([a.role_id for a in assignments]), SysRole.status == 'active')))
        if not active_roles and (assignments or user.data_scope_mode != 'legacy'):
            return False, (), (), ()
        mode = resolve_data_scope(db, user)
        now = datetime.now()
        grants = list(db.scalars(select(UserScopeGrant).where(
            UserScopeGrant.grantee_user_id == user_id, UserScopeGrant.revoked_at.is_(None),
            UserScopeGrant.valid_from <= now,
            or_(UserScopeGrant.valid_until.is_(None), UserScopeGrant.valid_until > now))))
        summaries = tuple({'scope_type': g.scope_type, 'scope_ref_id': g.scope_ref_id} for g in grants)
        if mode == 'all' or any(g.scope_type == 'all' for g in grants):
            return True, (), None, summaries
        departments = set(get_visible_department_ids(db, user) or ()) if mode == 'dept' else set()
        departments.update(g.scope_ref_id for g in grants if g.scope_type == 'department' and g.scope_ref_id is not None)
        departments = set(db.scalars(select(SysDepartment.id).where(
            SysDepartment.id.in_(departments), SysDepartment.status == 'active')))
        people = set(db.scalars(select(TalentProfile.id).where(TalentProfile.department_id.in_(departments))))
        if person_id is not None:
            people.add(person_id)
        return False, tuple(sorted(departments)), tuple(sorted(people)), summaries



def resolve_data_scope(db, user) -> Literal["all", "dept", "self"]:
    """聚合账号生效角色的 data_scope，解析出最终数据范围（design §8.3 sys_roles）。

    取账号所有启用角色 data_scope 的「最宽松」口径：
    - 任一角色为 ``all`` → ``all``；
    - 否则任一为 ``dept`` → ``dept``；
    - 否则 ``self``。
    仅 active、data_scope_mode=legacy 且从无当前绑定的账号可回退 users.role。
    scoped 账号无生效角色返回 self；未知模式/停用账号同样关闭权限。
    这是 A06-2 部门行级过滤与下游（B/C/D）数据范围的权威来源。
    """
    uid = getattr(user, "id", None)
    if uid is None:
        return "self"
    try:
        from sqlalchemy import select

        from app.models import SysRole, SysUserRole, User

        u = db.get(User, uid)
        if u is None or not u.is_active or u.data_scope_mode not in ('legacy', 'scoped'):
            return "self"
        scopes = db.execute(
            select(SysRole.data_scope)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)
            .where(SysUserRole.user_id == uid, SysRole.status == "active")
        ).scalars().all()
        if scopes:
            if "all" in scopes:
                return "all"
            if "dept" in scopes:
                return "dept"
            return "self"
        # 回退：legacy users.role 简值（无 sys_user_roles 绑定）
        if u.data_scope_mode != 'legacy' or db.scalar(select(SysUserRole.id).where(SysUserRole.user_id == uid).limit(1)) is not None:
            return "self"
        r = (getattr(u, "role", "") or "").strip().lower() if u else ""
        return "all" if r == "hr" else "self"
    except Exception:
        return "self"


def get_visible_department_ids(db, user) -> tuple[int, ...] | None:
    """账号可看的部门 id 集合；返回 ``None`` 表示「全部可见」（data_scope=all）。

    - ``all`` → ``None``（不限制部门）；
    - ``dept`` → ``(users.department_id,)`` ∪ 跨部门授权（user_scope_grants）去重；
      若账号本身无 ``department_id`` 且无授权，返回空元组（本部门=无，即无数据）；
    - ``self`` → ``None``（self 范围不按部门过滤，由调用方做本人行级判定）。

    配合 :func:`apply_department_filter` 使用。
    """
    scope = resolve_data_scope(db, user)
    if scope in ("all", "self"):
        return None
    # dept：本人所属部门 + 显式跨部门授权
    base: list[int] = []
    dept = getattr(user, "department_id", None)
    if dept is not None:
        base.append(int(dept))
    base.extend(_active_department_ids(getattr(user, "id", None)))
    return tuple(sorted(set(base)))


def apply_department_filter(stmt, model, visible_dept_ids):
    """在查询上叠加部门行级过滤；``visible_dept_ids`` 为 ``None`` 表示不过滤。

    模型无 ``department_id`` 列时防御性地返回原 stmt（不误杀、不抛错）。
    """
    if visible_dept_ids is None:
        return stmt
    col = getattr(model, "department_id", None)
    if col is None:
        return stmt
    return stmt.where(col.in_(visible_dept_ids))


def get_role_codes(db, user_id: int | None) -> tuple[str, ...]:
    """聚合账号生效角色代码（sys_user_roles 优先；无记录则回退 users.role 简值）。

    - 有 sys_user_roles 记录：取全部启用角色 code（集合去重、排序稳定）。
    - 无记录且 data_scope_mode=legacy：users.role == "hr" → ("hr",)；
      scoped 无生效角色、停用/未知模式账号均返回 ("personal",)。
      其余（含未知/空）按 deny-by-default 映射为 ("personal",)。
    纯查询，不写库；调用方负责事务边界。
    """
    if user_id is None:
        return ("personal",)
    try:
        from sqlalchemy import select

        from app.models import SysRole, SysUserRole, User

        user = db.get(User, user_id)
        if user is None or not user.is_active or user.data_scope_mode not in ('legacy', 'scoped'):
            return ("personal",)
        rows = db.execute(
            select(SysRole.code)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)
            .where(SysUserRole.user_id == user_id, SysRole.status == "active")
        ).scalars().all()
        if rows:
            return tuple(sorted(set(rows)))
        if user.data_scope_mode != 'legacy' or db.scalar(select(SysUserRole.id).where(SysUserRole.user_id == user_id).limit(1)) is not None:
            return ("personal",)
        if user is not None:
            r = (getattr(user, "role", "") or "").strip().lower()
            if r in ("hr", "personal"):
                return (r,)
    except Exception:
        return ("personal",)
    return ("personal",)


def account_is_management(db, user) -> bool:
    """管理侧判定（A06 统一口径）：账号角色集合与 MANAGEMENT_ROLES 有交集。

    替代散落的 `can_view_all_persons` 与 inventory 的 `ADMIN_ROLES` 字符串判断：
    使 system_admin（经 sys_user_roles 绑定）也能正确获得管理侧权限，
    而 dept_manager / trainer / employee / candidate / personal 一律非管理侧。
    """
    uid = getattr(user, "id", None)
    if uid is None:
        return False
    return bool(set(get_role_codes(db, uid)) & set(MANAGEMENT_ROLES))


def require_management_guard(user) -> None:
    """管理侧门禁（无 db 可用时的兼容入口）：内部开 SessionLocal 计算。

    与 workflows/inventory 既有 `_require_management` / `_require_admin` 同形；
    统一走 account_is_management，避免各模块各自判断造成口径漂移。
    """
    from app.database import SessionLocal

    with SessionLocal() as db:
        if not account_is_management(db, user):
            raise _BizForbidden()


# --------------------------------------------------------------------------- #
# A06-3 字段级权限：按角色裁剪序列化字段（deny-by-default）
# --------------------------------------------------------------------------- #

#: 任何角色、任何资源都不得出现在响应中的敏感列（硬底线，白名单也不能覆盖）。
SENSITIVE_FIELDS = frozenset({"password_hash", "password", "token", "secret", "api_key"})

#: 账号花名册基础字段：能进入 /api/system/accounts 的角色都可见的最小集合。
ACCOUNT_BASE_FIELDS = ("id", "username", "display_name", "role", "department_id", "is_active")

#: 资源 → 角色 code → 可见字段白名单；``None`` 表示「全部非敏感列」。
#: 当前为服务端策略常量（V2.6 §8.3 的 sys_role_permissions 只有 menu×action×data_scope，
#: 无字段维度）；后续可迁到配置表，调用方形状不变。
FIELD_POLICIES: dict[str, dict[str, tuple[str, ...] | None]] = {
    "accounts": {
        "system_admin": None,  # 全部非敏感列（含 person_id / last_login_at / 时间戳）
        "dept_manager": ACCOUNT_BASE_FIELDS,  # 仅基础字段，审计/绑定类不可见
    },
}


def resolve_visible_fields(db, user, resource: str) -> tuple[str, ...] | None:
    """字段级权限：返回该账号对某资源可见的字段白名单。

    - ``None``：全部非敏感列（如 system_admin）；
    - 非空元组：只允许这些列；
    - ``()``：**deny-by-default**（未知资源 / 无任何匹配角色，一律不给字段）。
    """
    policy = FIELD_POLICIES.get(resource)
    if policy is None:
        return ()
    codes = set(get_role_codes(db, getattr(user, "id", None)))
    if "system_admin" in codes and "system_admin" in policy:
        return policy["system_admin"]
    for code in sorted(codes):
        if code in policy:
            return policy[code]
    return ()


def filter_columns(columns, visible) -> tuple[str, ...]:
    """把 ORM 列名集合按白名单裁剪，并始终剔除 :data:`SENSITIVE_FIELDS`。"""
    safe = tuple(c for c in columns if c not in SENSITIVE_FIELDS)
    if visible is None:
        return safe
    allowed = set(visible)
    return tuple(c for c in safe if c in allowed)


def serialize_fields(obj, visible) -> dict:
    """按字段级白名单序列化 ORM 行；敏感列永不出现（防御纵深）。"""
    cols = [c.name for c in obj.__table__.columns]
    return {name: getattr(obj, name) for name in filter_columns(cols, visible)}


def _BizForbidden():
    from app.core.errors import BizError

    return BizError(403, DENY_MESSAGE)
