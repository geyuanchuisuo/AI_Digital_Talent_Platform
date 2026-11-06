"""T-07 L1：登录/JWT 纯逻辑单测（不连库、不出网）。

覆盖：bcrypt 校验边界、JWT 签发/解码、过期/篡改/错误密钥、get_current_user 的
401/403 分支、authenticate 的三条失败路径、login 是否提交并回写 last_login_at。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from app.api.deps import get_current_user
from app.config import settings
from app.core.errors import BizError
from app.service import auth_service

# 模块级只算一次 bcrypt（cost=12 约 0.25s/次），避免每个用例重复付这份开销
PWD = "pwd123456"
PWD_HASH = auth_service.hash_password(PWD)


def _make_user(user_id: int = 7, active: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username="u_test",
        display_name="测试用户",
        role="hr",
        is_active=active,
        password_hash=PWD_HASH,
        last_login_at=None,
    )


class _FakeDB:
    """只实现 deps / service 用到的方法，避免单测连 MySQL。"""

    def __init__(self, user: SimpleNamespace | None):
        self._user = user
        self.commits = 0
        self.refreshed = 0

    def get(self, _model, _pk):  # noqa: ANN001
        return self._user

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _obj) -> None:  # noqa: ANN001
        self.refreshed += 1


# ---------------------------------------------------------------- bcrypt
def test_hash_and_verify_roundtrip():
    hashed = auth_service.hash_password("hr123456")
    assert hashed.startswith("$2b$")
    assert auth_service.verify_password("hr123456", hashed)
    assert not auth_service.verify_password("hr1234567", hashed)


def test_verify_password_tolerates_overlong_and_bad_hash():
    # bcrypt 4.x 对 >72 字节直接抛 ValueError；实现须吞掉并按失败处理
    assert auth_service.verify_password("x" * 200, auth_service.DUMMY_HASH) is False
    assert auth_service.verify_password("x", "not-a-bcrypt-hash") is False


# ---------------------------------------------------------------- JWT
def test_create_token_payload_matches_doc():
    user = _make_user(user_id=3)
    token, expires_in = auth_service.create_access_token(user)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["sub"] == "3"  # 文档 §四：sub 为字符串
    assert payload["username"] == "u_test"
    assert payload["role"] == "hr"
    assert payload["exp"] - payload["iat"] == settings.jwt_expire_hours * 3600
    assert expires_in == settings.jwt_expire_hours * 3600


def test_decode_returns_typed_payload():
    token, _ = auth_service.create_access_token(_make_user(user_id=11))
    payload = auth_service.decode_access_token(token)
    assert payload.user_id == 11
    assert payload.username == "u_test"


def test_expired_token_is_401():
    issued = datetime.now(timezone.utc) - timedelta(hours=settings.jwt_expire_hours + 1)
    token, _ = auth_service.create_access_token(_make_user(), now=issued)
    with pytest.raises(BizError) as exc:
        auth_service.decode_access_token(token)
    assert exc.value.code == 401
    assert exc.value.message == auth_service.TOKEN_INVALID_MESSAGE


def test_tampered_token_is_401():
    token, _ = auth_service.create_access_token(_make_user())
    with pytest.raises(BizError) as exc:
        auth_service.decode_access_token(token[:-2] + ("aa" if token[-2:] != "aa" else "bb"))
    assert exc.value.code == 401


def test_token_signed_with_other_secret_is_401():
    forged = jwt.encode(
        {"sub": "1", "username": "hr", "role": "hr", "exp": 9999999999},
        "wrong-secret",
        algorithm="HS256",
    )
    with pytest.raises(BizError) as exc:
        auth_service.decode_access_token(forged)
    assert exc.value.code == 401


def test_token_with_non_int_sub_is_401():
    forged = jwt.encode({"sub": "abc", "exp": 9999999999}, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(BizError) as exc:
        auth_service.decode_access_token(forged)
    assert exc.value.code == 401


# ---------------------------------------------------------------- get_current_user
def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_no_credentials_is_401():
    with pytest.raises(BizError) as exc:
        get_current_user(creds=None, db=_FakeDB(_make_user()))
    assert exc.value.code == 401


def test_valid_token_returns_user():
    user = _make_user()
    token, _ = auth_service.create_access_token(user)
    assert get_current_user(creds=_creds(token), db=_FakeDB(user)) is user


def test_token_user_missing_is_401():
    token, _ = auth_service.create_access_token(_make_user(user_id=99))
    with pytest.raises(BizError) as exc:
        get_current_user(creds=_creds(token), db=_FakeDB(None))
    assert exc.value.code == 401


def test_disabled_user_is_403_even_with_valid_token():
    user = _make_user(active=0)
    token, _ = auth_service.create_access_token(user)
    with pytest.raises(BizError) as exc:
        get_current_user(creds=_creds(token), db=_FakeDB(user))
    assert exc.value.code == 403
    assert exc.value.message == "账号已停用"


# ---------------------------------------------------------------- authenticate / login
def test_authenticate_unknown_user_is_401(monkeypatch):
    monkeypatch.setattr(auth_service.users_dao, "get_by_username", lambda db, name: None)
    with pytest.raises(BizError) as exc:
        auth_service.authenticate(_FakeDB(None), "ghost", "whatever")
    assert exc.value.code == 401
    assert exc.value.message == "用户名或密码错误"


def test_authenticate_wrong_password_is_401(monkeypatch):
    user = _make_user()
    monkeypatch.setattr(auth_service.users_dao, "get_by_username", lambda db, name: user)
    with pytest.raises(BizError) as exc:
        auth_service.authenticate(_FakeDB(user), "u_test", "wrong-password")
    assert exc.value.code == 401


def test_authenticate_disabled_is_403(monkeypatch):
    user = _make_user(active=0)
    monkeypatch.setattr(auth_service.users_dao, "get_by_username", lambda db, name: user)
    with pytest.raises(BizError) as exc:
        auth_service.authenticate(_FakeDB(user), "u_test", PWD)
    assert exc.value.code == 403


def test_login_commits_and_updates_last_login(monkeypatch):
    user = _make_user()
    db = _FakeDB(user)
    monkeypatch.setattr(auth_service.users_dao, "get_by_username", lambda _db, _name: user)
    returned, token, expires_in = auth_service.login(db, "u_test", PWD)
    assert returned is user
    assert db.commits == 1 and db.refreshed == 1
    assert isinstance(user.last_login_at, datetime)
    assert token and expires_in == settings.jwt_expire_hours * 3600
