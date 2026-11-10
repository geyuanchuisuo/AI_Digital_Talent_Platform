"""L1 单元测试 · AccountScope 数据范围授权（REQ-B-20261106-02 阶段 1 契约）。

纯逻辑，不连库；`get_account_scope` 只读 role/person_id 两个属性。
覆盖：三值角色映射、未知角色 deny-by-default、个人越权拒绝、文件授权三态。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.auth.scope import (
    DENY_MESSAGE,
    AccountScope,
    authorize_file_access,
    authorize_profile_access,
    get_account_scope,
)


def _user(role: str, person_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=1, username="u", role=role, person_id=person_id)


@pytest.fixture(autouse=True)
def isolate_management_role_lookup(monkeypatch):
    monkeypatch.setattr('app.core.auth.scope._management_range',lambda *a:(True,(),None,()))


def test_hr_maps_to_admin_side():
    scope = get_account_scope(_user("hr"))
    assert scope.role == "hr"
    assert scope.can_view_all_persons is True
    assert scope.knowledge_scope == "admin"
    assert scope.person_id is None


def test_personal_bound_maps_to_self_scope():
    scope = get_account_scope(_user("personal", person_id=1024))
    assert scope.role == "personal"
    assert scope.person_id == 1024
    assert scope.can_view_all_persons is False
    assert scope.knowledge_scope == "public"


def test_personal_unbound_has_no_data_scope():
    scope = get_account_scope(_user("personal"))
    assert scope.person_id is None
    assert scope.can_view_all_persons is False


@pytest.mark.parametrize("unknown_role", ["", "guest", "employee", "SUPERADMIN"])
def test_unknown_role_is_deny_by_default(unknown_role):
    scope = get_account_scope(_user(unknown_role, person_id=999))
    assert scope.role == "personal"
    assert scope.person_id is None  # 未识别角色不继承 person_id
    assert scope.can_view_all_persons is False


def test_profile_access_hr_allowed_personal_self_allowed():
    hr = get_account_scope(_user("hr"))
    assert authorize_profile_access(hr, 42).allowed is True

    personal = get_account_scope(_user("personal", person_id=42))
    assert authorize_profile_access(personal, 42).allowed is True


def test_profile_access_denies_others_and_unbound():
    personal = get_account_scope(_user("personal", person_id=42))
    decision = authorize_profile_access(personal, 43)
    assert decision.allowed is False
    assert decision.reason == DENY_MESSAGE

    unbound = get_account_scope(_user("personal"))
    assert authorize_profile_access(unbound, 42).allowed is False

    none_scope = AccountScope(role="hr", person_id=None, can_view_all_persons=False, knowledge_scope="admin")
    assert authorize_profile_access(none_scope, 42).allowed is False


def test_file_access_ignores_action_and_unlinked_file_denied():
    personal = get_account_scope(_user("personal", person_id=7))
    assert authorize_file_access(personal, 7, action="view").allowed is True
    assert authorize_file_access(personal, 7, action="download").allowed is True
    # 未解析/未建档文件（无归属）对个人一律拒绝，不泄露存在性
    decision = authorize_file_access(personal, None, action="view")
    assert decision.allowed is False
    assert decision.reason == DENY_MESSAGE

    hr = get_account_scope(_user("hr"))
    assert authorize_file_access(hr, None, action="download").allowed is True


def test_scope_is_immutable():
    scope = get_account_scope(_user("hr"))
    with pytest.raises(Exception):
        scope.role = "personal"  # type: ignore[misc]
