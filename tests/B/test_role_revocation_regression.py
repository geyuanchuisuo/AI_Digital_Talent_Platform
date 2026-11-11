"""B11 consumer regressions against persisted shared authorization records."""
from contextlib import contextmanager

import pytest
from sqlalchemy import DefaultClause, Integer, MetaData, text

from app.core.auth import scope as auth
from app.models.user import User
from app.modules.recruitment.scope_models import UserScopeGrant
from app.modules.system.models import SysDepartment, SysRole, SysUserRole
from app.modules.system.service import widest_data_scope
from test_round1_versions import db


@pytest.fixture
def account(db, monkeypatch):
    metadata = MetaData()
    for model in (User, SysRole, SysUserRole, SysDepartment, UserScopeGrant):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if column.primary_key or column.name == "is_active":
                column.type = Integer()
            if column.name == "updated_at":
                column.server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    metadata.create_all(db.get_bind())
    user = User(id=71, username="synthetic_revocation", password_hash="unit-test", role="hr")
    db.add(user)
    db.commit()

    @contextmanager
    def local_session():
        yield db

    monkeypatch.setattr("app.database.SessionLocal", local_session)
    return db, user


def observed_scope(entry, db, user):
    if entry == "shared_data_scope":
        return auth.resolve_data_scope(db, user)
    if entry == "system_data_scope":
        return widest_data_scope(db, user.id)
    return "all" if auth.get_account_scope(user).can_view_all_persons else "self"


@pytest.mark.parametrize("entry", ["shared_data_scope", "system_data_scope", "b_account_scope"])
def test_never_configured_legacy_hr_remains_compatible(account, entry):
    db, user = account
    assert observed_scope(entry, db, user) == "all"


@pytest.mark.parametrize("entry", ["shared_data_scope", "system_data_scope", "b_account_scope"])
@pytest.mark.parametrize("revocation", ["remove_last_binding", "disable_last_role"])
def test_revocation_does_not_restore_legacy_all(account, entry, revocation):
    db, user = account
    user.data_scope_mode = "scoped"
    role = SysRole(id=72, code="hr", name="Synthetic limited HR", data_scope="self")
    link = SysUserRole(user_id=user.id, role_id=role.id)
    db.add_all([role, link])
    db.commit()
    assert observed_scope(entry, db, user) == "self"

    if revocation == "remove_last_binding":
        db.delete(link)
    else:
        role.status = "disabled"
    db.commit()
    db.expire_all()
    assert observed_scope(entry, db, user) == "self"


@pytest.mark.parametrize("mode", ["scoped", "unknown"])
def test_empty_or_unknown_mode_does_not_grant_management(account, mode):
    db, user = account
    user.data_scope_mode = mode
    db.commit()
    assert auth.get_role_codes(db, user.id) == ("personal",)
    assert not auth.account_is_management(db, user)
    assert not auth.get_account_scope(user).can_view_all_persons


def test_disabled_role_does_not_restore_management_in_legacy_mode(account):
    db, user = account
    db.add_all([
        SysRole(id=72, code="hr", name="Disabled synthetic HR", status="disabled", data_scope="all"),
        SysUserRole(user_id=user.id, role_id=72),
    ])
    db.commit()
    assert auth.get_role_codes(db, user.id) == ("personal",)
    assert not auth.account_is_management(db, user)
    assert auth.resolve_data_scope(db, user) == "self"
