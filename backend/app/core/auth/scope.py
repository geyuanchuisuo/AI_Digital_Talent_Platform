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

#: 统一拒绝文案：不泄露对象是否存在（REQ-02 验收口径）
DENY_MESSAGE = "无权访问该资源"


@dataclass(frozen=True)
class AccountScope:
    """账号可访问的数据范围；B/C/D 按此先过滤再查询/召回。"""

    role: ScopeRole
    person_id: int | None
    can_view_all_persons: bool
    knowledge_scope: KnowledgeScope


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
        # 阶段 1 管理侧：现有 hr 即全量；六类角色冻结后按职责收窄
        return AccountScope(
            role="hr",
            person_id=None,
            can_view_all_persons=True,
            knowledge_scope="admin",
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
