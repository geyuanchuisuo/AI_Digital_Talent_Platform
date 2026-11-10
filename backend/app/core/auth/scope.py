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
    #: 账号生效角色代码集合（sys_user_roles 聚合；无记录时回退 users.role）。
    #: A06 职责门禁的权威来源；默认空元组以保持既有纯函数构造向后兼容。
    role_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileAccessDecision:
    """文件访问授权结果；deny 时 reason 统一，不区分「不存在/无权」。"""

    allowed: bool
    reason: str | None = None


def get_account_scope(user) -> AccountScope:
    """从 User 行（或同形对象：id/role/person_id）计算数据范围。

    纯函数，不查库；`user` 只读 `role` 与 `person_id` 两个属性。
    """
    role = (getattr(user, "role", "") or "").strip().lower()
    person_id = getattr(user, "person_id", None)

    if role == "personal":
        # 个人账号：仅本人（person_id 未绑定＝无任何数据范围）
        return AccountScope(
            role="personal",
            person_id=person_id,
            can_view_all_persons=False,
            knowledge_scope="public",
        )
    if role == "hr":
        # 阶段 1 管理侧：现有 hr 即全量；六类角色冻结后按职责收窄。
        # 组织范围扩展：管理侧也聚合其名下的授权记录（审计可见）。
        return AccountScope(
            role="hr",
            person_id=None,
            can_view_all_persons=True,
            knowledge_scope="admin",
            department_ids=_active_department_ids(user_id=getattr(user, "id", None)),
            granted_scope=_active_grant_summaries(getattr(user, "id", None)),
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


def get_role_codes(db, user_id: int | None) -> tuple[str, ...]:
    """聚合账号生效角色代码（sys_user_roles 优先；无记录则回退 users.role 简值）。

    - 有 sys_user_roles 记录：取全部启用角色 code（集合去重、排序稳定）。
    - 无记录：users.role == "hr" → ("hr",)；“personal” → ("personal",)；
      其余（含未知/空）按 deny-by-default 映射为 ("personal",)。
    纯查询，不写库；调用方负责事务边界。
    """
    if user_id is None:
        return ("personal",)
    try:
        from sqlalchemy import select

        from app.models import SysRole, SysUserRole, User

        rows = db.execute(
            select(SysRole.code)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)
            .where(SysUserRole.user_id == user_id, SysRole.status == "active")
        ).scalars().all()
        if rows:
            return tuple(sorted(set(rows)))
        user = db.get(User, user_id)
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


def _BizForbidden():
    from app.core.errors import BizError

    return BizError(403, DENY_MESSAGE)
