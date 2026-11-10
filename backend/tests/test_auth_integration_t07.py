"""T-07 L2：登录鉴权集成测试（真实 MySQL，不外呼 LLM/Embedding）。

对应 todolist T-07 验收点：
- 种子账号 `hr/hr123456` 登录成功（含 last_login_at 回写）
- 错误密码 401 统一提示（不区分用户不存在）
- 停用账号 403
- 无 token / 过期 / 篡改访问业务接口 401

隔离方式：临时用户 `t07_<随机>` 建/删都在本文件内完成，不碰种子账号的 is_active。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "t07-pass-123456"


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def temp_user():
    """建一个启用状态的临时账号；用例结束物理删除（DB 里不留垃圾数据）。"""
    username = f"t07_{uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = User(
            username=username,
            password_hash=hash_password(PWD),
            display_name="T07 临时用户",
            role="hr",
            is_active=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id

    yield {"username": username, "password": PWD, "id": user_id}

    with SessionLocal() as db:
        row = db.get(User, user_id)
        if row is not None:
            db.delete(row)
            db.commit()


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_seed_account_login_ok(client: TestClient):
    """验收点：种子账号能登录，返回结构与接口 §2 一致。"""
    resp = _login(client, "hr", "hr123456")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "success"
    data = body["data"]
    assert data["expires_in"] == settings.jwt_expire_hours * 3600
    # 2026-11-07 起含 person_id（REQ-B-20261106-02 贯通；hr 账号为 null）
    # 2026-11-10 起含 is_management（后端权威权限判定，替代前端 role 自猜）
    assert set(data["user"]) == {
        "id",
        "username",
        "display_name",
        "role",
        "person_id",
        "is_management",
    }
    assert data["user"]["person_id"] is None
    assert data["user"]["username"] == "hr"
    assert data["token"].count(".") == 2  # JWT 三段式

    with SessionLocal() as db:
        row = db.execute(select(User).where(User.username == "hr")).scalar_one()
        assert row.last_login_at is not None


def test_login_updates_last_login_at(client: TestClient, temp_user):
    before = datetime.now()
    resp = _login(client, temp_user["username"], temp_user["password"])
    assert resp.status_code == 200

    with SessionLocal() as db:
        row = db.get(User, temp_user["id"])
        assert row is not None and row.last_login_at is not None
        assert row.last_login_at >= before.replace(microsecond=0) - timedelta(seconds=1)


def test_wrong_password_is_401_with_unified_body(client: TestClient, temp_user):
    resp = _login(client, temp_user["username"], "definitely-wrong")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == 401
    assert body["message"] == "用户名或密码错误"
    assert body["data"] is None
    assert body["request_id"]  # 错误体必须带 request_id


def test_unknown_user_is_401_same_message(client: TestClient):
    """防账号枚举：用户不存在与密码错误必须同码同文案。"""
    resp = _login(client, f"ghost_{uuid4().hex[:6]}", "whatever")
    assert resp.status_code == 401
    assert resp.json()["message"] == "用户名或密码错误"


def test_missing_field_is_422(client: TestClient):
    resp = client.post("/api/auth/login", json={"username": "hr"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_me_returns_current_user(client: TestClient, temp_user):
    token = _login(client, temp_user["username"], temp_user["password"]).json()["data"]["token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["id"] == temp_user["id"]
    assert body["data"]["username"] == temp_user["username"]


def test_me_without_token_is_401(client: TestClient):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["message"] == "未登录或登录已过期"


def test_me_with_tampered_token_is_401(client: TestClient, temp_user):
    token = _login(client, temp_user["username"], temp_user["password"]).json()["data"]["token"]
    broken = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {broken}"})
    assert resp.status_code == 401


def test_me_with_expired_token_is_401(client: TestClient, temp_user):
    now = datetime.now(timezone.utc) - timedelta(hours=settings.jwt_expire_hours + 2)
    expired = jwt.encode(
        {
            "sub": str(temp_user["id"]),
            "username": temp_user["username"],
            "role": "hr",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["message"] == "未登录或登录已过期"


def test_disabled_user_login_is_403(client: TestClient, temp_user):
    """验收点：停用账号登录 403（密码正确也要拦）。"""
    with SessionLocal() as db:
        db.get(User, temp_user["id"]).is_active = 0
        db.commit()

    resp = _login(client, temp_user["username"], temp_user["password"])
    assert resp.status_code == 403
    assert resp.json()["code"] == 403
    assert resp.json()["message"] == "账号已停用"


def test_disabled_user_old_token_is_403(client: TestClient, temp_user):
    """token 未过期但账号被停用 → 访问也必须 403（不能只拦登录）。"""
    # 上一个用例可能已把该账号停用，这里先恢复再登录，保证用例独立可单独运行
    with SessionLocal() as db:
        db.get(User, temp_user["id"]).is_active = 1
        db.commit()

    token = _login(client, temp_user["username"], temp_user["password"]).json()["data"]["token"]
    with SessionLocal() as db:
        db.get(User, temp_user["id"]).is_active = 0
        db.commit()

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["message"] == "账号已停用"
