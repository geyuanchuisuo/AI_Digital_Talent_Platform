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
    if role == "hr":
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
    from sqlalchemy import select,or_
    from app.database import SessionLocal
    from app.models.talent import TalentProfile
    from app.modules.system.models import SysRole,SysUserRole,SysDepartment
    from app.modules.recruitment.scope_models import UserScopeGrant
    if user_id is None:return True,(),None,()
    with SessionLocal() as db:
        assignments=list(db.scalars(select(SysUserRole).where(SysUserRole.user_id==user_id)))
        if not assignments:return True,(),None,()  # Explicit legacy compatibility, not a DB-error fallback.
        roles=list(db.scalars(select(SysRole).where(SysRole.id.in_([a.role_id for a in assignments]),SysRole.status=='active')))
        kinds={r.data_scope for r in roles}
        if not kinds:return False,(),(),()
        now=datetime.now()
        grants=list(db.scalars(select(UserScopeGrant).where(UserScopeGrant.grantee_user_id==user_id,
            UserScopeGrant.revoked_at.is_(None),UserScopeGrant.valid_from<=now,
            or_(UserScopeGrant.valid_until.is_(None),UserScopeGrant.valid_until>now))))
        summaries=tuple({'scope_type':g.scope_type,'scope_ref_id':g.scope_ref_id} for g in grants)
        if 'all' in kinds or any(g.scope_type=='all' for g in grants):return True,(),None,summaries
        departments={g.scope_ref_id for g in grants if g.scope_type=='department' and g.scope_ref_id is not None}
        if 'dept' in kinds and person_id:
            own=db.get(TalentProfile,person_id)
            if own and own.department_id:departments.add(own.department_id)
        departments=set(db.scalars(select(SysDepartment.id).where(SysDepartment.id.in_(departments),SysDepartment.status=='active')))
        people=set(db.scalars(select(TalentProfile.id).where(TalentProfile.department_id.in_(departments))))
        if person_id:people.add(person_id)
        return False,tuple(sorted(departments)),tuple(sorted(people)),summaries
