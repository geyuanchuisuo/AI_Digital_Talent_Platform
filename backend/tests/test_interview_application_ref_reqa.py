"""REQ-A-20261114-220500-C：interview_rounds / ai_interview_sessions 落库前校验 application_id。

A 侧请求（T-A23 邀约口径核对产物）要求 C 在面试引用申请时保证引用完整性：
- application_id 非空时必须在 applications 中存在，否则 400「application_id 不存在」；
- 与轮次侧 talent_profile_id 都非空时归属一致，否则 400（防止挂到他人申请上）；
- 人工轮次（POST /api/interviews/rounds）与 AI 面试自动安排路径
  （POST /api/ai-interviews/sessions）同口径。

真实 MySQL（integration 标记），造数沿用 A 域 test_application_snapshot_a08 的
直插 Application 方式（无物理外键到人才表）。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import User
from app.modules.interviews.ai_models import AiInterviewEvent, AiInterviewSession
from app.modules.interviews.models import InterviewRound, InterviewRoundEvent
from app.modules.recruitment.models import Application
from app.modules.system.models import SysRole, SysUserRole
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "reqa-pass-123456"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def env():
    tag = uuid.uuid4().hex[:8]
    round_ids: list[int] = []
    session_ids: list[int] = []
    app_ids: list[int] = []
    with SessionLocal() as db:
        sysadmin = User(
            username=f"reqa_sys_{tag}", password_hash=hash_password(PWD),
            display_name="REQA 管理员", role="system_admin", is_active=1,
        )
        db.add(sysadmin)
        db.flush()
        admin_id = sysadmin.id
        # A06 管理侧判定走 sys_user_roles 聚合，仅 users.role 不够
        admin_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "system_admin"))
        db.add(SysUserRole(user_id=admin_id, role_id=admin_role_id))
        db.commit()

        # T1/T2 人才主档 id：applications.talent_profile_id 无物理外键，整数即可（tag 派生防撞）
        t1 = 900000 + int(tag, 16) % 100000
        t2 = t1 + 1
        app_ok = Application(
            talent_profile_id=t1, job_profile_id=t1 + 100, job_profile_version=1,
            internal_stage="new", external_stage="submitted", created_by=admin_id,
        )
        app_other = Application(
            talent_profile_id=t2, job_profile_id=t2 + 100, job_profile_version=1,
            internal_stage="new", external_stage="submitted", created_by=admin_id,
        )
        db.add_all([app_ok, app_other])
        db.commit()
        ids = {"admin": admin_id, "t1": t1, "t2": t2,
               "app_ok": app_ok.id, "app_other": app_other.id}
        app_ids = [app_ok.id, app_other.id]

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/login", json={"username": f"reqa_sys_{tag}", "password": PWD})
        assert r.status_code == 200, r.text
        ctx = {
            "client": client, "ids": ids, "round_ids": round_ids, "session_ids": session_ids,
            "admin_hdr": _hdr(r.json()["data"]["token"]),
        }
        yield ctx

    with SessionLocal() as db:
        if round_ids:
            db.execute(delete(InterviewRoundEvent).where(InterviewRoundEvent.round_id.in_(round_ids)))
            db.execute(delete(InterviewRound).where(InterviewRound.id.in_(round_ids)))
        if session_ids:
            db.execute(delete(AiInterviewEvent).where(AiInterviewEvent.session_id.in_(session_ids)))
            db.execute(delete(AiInterviewSession).where(AiInterviewSession.id.in_(session_ids)))
        if app_ids:
            db.execute(delete(Application).where(Application.id.in_(app_ids)))
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == ids["admin"]))
        db.execute(delete(User).where(User.id == ids["admin"]))
        db.commit()


def _round_payload(env: dict, **overrides) -> dict:
    ids = env["ids"]
    payload = {
        "talent_profile_id": ids["t1"],
        "round_name": "REQA 一面",
        "interviewer_user_ids": [ids["admin"]],
    }
    payload.update(overrides)
    return payload


def _create_round(env: dict, **overrides):
    return env["client"].post("/api/interviews/rounds", headers=env["admin_hdr"],
                              json=_round_payload(env, **overrides))


# --------------------------------------------------------------------------- #
# 1. 人工轮次 POST /api/interviews/rounds
# --------------------------------------------------------------------------- #

def test_round_ok_with_existing_application(env):
    """正例：申请存在且归属一致 → 创建成功并正确落库 application_id。"""
    ids = env["ids"]
    r = _create_round(env, application_id=ids["app_ok"], talent_profile_id=ids["t1"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["application_id"] == ids["app_ok"]
    assert data["talent_profile_id"] == ids["t1"]
    env["round_ids"].append(data["id"])


def test_round_rejects_missing_application(env):
    """负例：application_id 不存在 → 400「application_id 不存在」（旧行为：500 IntegrityError）。"""
    r = _create_round(env, application_id=999999999)
    assert r.status_code == 400, r.text
    assert r.json()["message"] == "application_id 不存在"


def test_round_rejects_ownership_mismatch(env):
    """负例：申请归属他人主档 → 400（防止把面试挂到别人的申请上）。"""
    ids = env["ids"]
    r = _create_round(env, application_id=ids["app_other"], talent_profile_id=ids["t1"])
    assert r.status_code == 400, r.text
    assert "归属不一致" in r.json()["message"]


def test_round_mismatch_never_persists(env):
    """DB 级核对：归属不一致的请求不产生任何落库行。"""
    ids = env["ids"]

    def _count() -> int:
        r = env["client"].get("/api/interviews/rounds", headers=env["admin_hdr"])
        assert r.status_code == 200, r.text
        return r.json()["data"]["total"]

    before = _count()
    r = _create_round(env, application_id=ids["app_other"], talent_profile_id=ids["t1"])
    assert r.status_code == 400, r.text
    assert _count() == before


def test_round_allows_application_without_talent_profile(env):
    """边界：轮次侧未关联人才主档（talent_profile_id=None，用候选人账号满足关联要求）
    → 只校验申请存在性，不比对归属。"""
    ids = env["ids"]
    r = _create_round(env, application_id=ids["app_ok"],
                      talent_profile_id=None, candidate_user_id=ids["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["application_id"] == ids["app_ok"]
    env["round_ids"].append(r.json()["data"]["id"])


def test_round_allows_null_application(env):
    """边界：不带 application_id → 现状保持，创建成功落库 NULL。"""
    r = _create_round(env)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["application_id"] is None
    env["round_ids"].append(r.json()["data"]["id"])


def test_round_idempotent_hit_skips_validation(env):
    """幂等命中：同 client_request_id 重复创建返回已有轮次，不重复校验。"""
    ids = env["ids"]
    cri = f"reqa-idem-{env['ids']['t1']}"
    first = _create_round(env, application_id=ids["app_ok"], client_request_id=cri)
    assert first.status_code == 200, first.text
    second = _create_round(env, application_id=ids["app_ok"], client_request_id=cri)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    env["round_ids"].append(first.json()["data"]["id"])


# --------------------------------------------------------------------------- #
# 2. AI 面试路径 POST /api/ai-interviews/sessions
# --------------------------------------------------------------------------- #

def test_ai_session_ok_with_existing_application(env):
    ids = env["ids"]
    r = env["client"].post("/api/ai-interviews/sessions", headers=env["admin_hdr"],
                           json={"application_id": ids["app_ok"], "talent_profile_id": ids["t1"]})
    assert r.status_code == 200, r.text
    env["session_ids"].append(r.json()["data"]["id"])


def test_ai_session_rejects_missing_application(env):
    r = env["client"].post("/api/ai-interviews/sessions", headers=env["admin_hdr"],
                           json={"application_id": 999999999})
    assert r.status_code == 400, r.text
    assert r.json()["message"] == "application_id 不存在"


def test_ai_session_rejects_ownership_mismatch(env):
    ids = env["ids"]
    r = env["client"].post("/api/ai-interviews/sessions", headers=env["admin_hdr"],
                           json={"application_id": ids["app_other"], "talent_profile_id": ids["t1"]})
    assert r.status_code == 400, r.text
    assert "归属不一致" in r.json()["message"]
