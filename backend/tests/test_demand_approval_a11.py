"""L2 集成测试 · A11 岗位/人才需求审批链（Master 执行表 A11 / 需求细化 §6.11 步骤 1）。

真实 MySQL 隔离库 + TestClient。覆盖：
- 提交/审批/拒绝/退回/撤回的合法流转与必填校验；
- 职责分离（HR 发起、业务负责人审批）、四眼原则、越权与重复批准反例；
- 审批版本冻结：内容变更使旧审批自动失效，业务 status 推进不影响审批；
- 审批记录只追加、快照为值拷贝、读取门禁；
- 迁移对历史行的 server_default 兜底。

不碰主验收库 `ai_talent`；测试库须显式 `MYSQL_DB=ai_talent_a_probe`。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal
from app.main import app
from app.models import SysRole, SysUserRole, User
from app.service.auth_service import hash_password

_TEST_PASSWORD = "Test@123456"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _headers(client: TestClient, username: str, password: str = _TEST_PASSWORD) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def _ensure_role(db, code: str) -> int:
    row = db.execute(select(SysRole).where(SysRole.code == code)).scalar_one_or_none()
    if row is None:
        row = SysRole(code=code, name=code, data_scope="dept", status="active")
        db.add(row)
        db.flush()
    elif row.status != "active":
        row.status = "active"
        db.flush()
    return row.id


def _make_user(username: str, legacy_role: str, role_code: str | None):
    """幂等建账号并绑定 sys_user_roles（与 A06 角色门禁测试同一口径）。"""
    with SessionLocal() as db:
        row = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if row is None:
            row = User(
                username=username,
                password_hash=hash_password(_TEST_PASSWORD),
                display_name=username,
                role=legacy_role,
                is_active=1,
            )
            db.add(row)
            db.flush()
        else:
            row.is_active = 1
            row.password_hash = hash_password(_TEST_PASSWORD)
            db.flush()
        if role_code is not None:
            rid = _ensure_role(db, role_code)
            exists = db.execute(
                select(SysUserRole).where(
                    SysUserRole.user_id == row.id, SysUserRole.role_id == rid
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(SysUserRole(user_id=row.id, role_id=rid))
        db.commit()
        return row.id


@pytest.fixture()
def hr_auth(client: TestClient) -> dict[str, str]:
    return _headers(client, "hr", "hr123456")


@pytest.fixture()
def dept_auth(client: TestClient) -> dict[str, str]:
    _make_user("a11_deptmgr", "employee", "dept_manager")
    return _headers(client, "a11_deptmgr")


@pytest.fixture()
def admin_auth(client: TestClient) -> dict[str, str]:
    _make_user("a11_sysadmin", "system_admin", "system_admin")
    return _headers(client, "a11_sysadmin")


@pytest.fixture()
def admin2_auth(client: TestClient) -> dict[str, str]:
    _make_user("a11_sysadmin2", "system_admin", "system_admin")
    return _headers(client, "a11_sysadmin2")


@pytest.fixture()
def employee_auth(client: TestClient) -> dict[str, str]:
    _make_user("a11_employee", "employee", "employee")
    return _headers(client, "a11_employee")


def _new_demand(
    client: TestClient,
    auth: dict[str, str],
    *,
    with_anchor: bool = True,
    headcount: int = 2,
) -> dict:
    """创建一条带真实项目群/项目锚点的需求；无锚点用例用于必填项反例。"""
    name = f"A11-{uuid.uuid4().hex[:8]}"
    payload: dict = {"position_name": name, "headcount": headcount, "priority": "high"}
    if with_anchor:
        group = client.post(
            "/api/recruitment/project-groups", json={"name": f"群-{name}"}, headers=auth
        )
        assert group.status_code == 200, group.text
        project = client.post(
            "/api/recruitment/projects",
            json={"name": f"项目-{name}", "project_group_id": group.json()["data"]["id"]},
            headers=auth,
        )
        assert project.status_code == 200, project.text
        payload["project_id"] = project.json()["data"]["id"]
        payload["project_group_id"] = group.json()["data"]["id"]
        payload["job_profile_id"] = 1
    resp = client.post("/api/recruitment/demands", json=payload, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _submit(client: TestClient, auth: dict, demand_id: int, note: str | None = None):
    return client.post(
        f"/api/recruitment/demands/{demand_id}/submit",
        json={"note": note} if note is not None else {},
        headers=auth,
    )


def _decide(client: TestClient, auth: dict, demand_id: int, decision: str, reason: str | None = None):
    payload: dict = {"decision": decision}
    if reason is not None:
        payload["reason"] = reason
    return client.post(
        f"/api/recruitment/demands/{demand_id}/decision", json=payload, headers=auth
    )


def _withdraw(client: TestClient, auth: dict, demand_id: int, reason: str = "岗位取消"):
    return client.post(
        f"/api/recruitment/demands/{demand_id}/withdraw", json={"reason": reason}, headers=auth
    )


def _records(client: TestClient, auth: dict, demand_id: int) -> list[dict]:
    resp = client.get(
        f"/api/recruitment/demands/{demand_id}/approval-records", headers=auth
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


# --------------------------------------------------------------------------- #
# 初始状态与提交
# --------------------------------------------------------------------------- #


def test_new_demand_starts_as_draft_and_is_not_matchable(client, hr_auth):
    demand = _new_demand(client, hr_auth)
    assert demand["approval_status"] == "draft"
    assert demand["approval_version"] == 1
    assert demand["approved_version"] is None
    assert demand["matchable"] is False
    assert _records(client, hr_auth, demand["id"]) == []


def test_submit_rejects_incomplete_required_fields(client, hr_auth):
    demand = _new_demand(client, hr_auth, with_anchor=False)
    resp = _submit(client, hr_auth, demand["id"])
    assert resp.status_code == 400, resp.text
    assert "project_id" in resp.text
    assert _records(client, hr_auth, demand["id"]) == []

    # 补齐归属锚点后即可提交
    group = client.post(
        "/api/recruitment/project-groups", json={"name": f"群-{demand['id']}"}, headers=hr_auth
    ).json()["data"]
    patched = client.patch(
        f"/api/recruitment/demands/{demand['id']}",
        json={"project_group_id": group["id"]},
        headers=hr_auth,
    )
    assert patched.status_code == 200, patched.text
    assert _submit(client, hr_auth, demand["id"]).status_code == 200


def test_submit_is_not_allowed_twice(client, hr_auth):
    demand = _new_demand(client, hr_auth)
    assert _submit(client, hr_auth, demand["id"]).status_code == 200
    dup = _submit(client, hr_auth, demand["id"])
    assert dup.status_code == 409, dup.text
    assert len(_records(client, hr_auth, demand["id"])) == 1


def test_submit_gate_allows_only_hr_and_admin(client, hr_auth, dept_auth, employee_auth, admin_auth):
    demand = _new_demand(client, hr_auth)
    assert client.post(
        f"/api/recruitment/demands/{demand['id']}/submit", json={}, headers=dept_auth
    ).status_code == 403
    assert client.post(
        f"/api/recruitment/demands/{demand['id']}/submit", json={}, headers=employee_auth
    ).status_code == 403
    assert client.post(
        f"/api/recruitment/demands/{demand['id']}/submit", json={}
    ).status_code == 401
    assert _submit(client, hr_auth, demand["id"]).status_code == 200


# --------------------------------------------------------------------------- #
# 审批决定：合法流转、越权、四眼、重复批准
# --------------------------------------------------------------------------- #


def test_happy_path_submit_then_business_owner_approves(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth, headcount=3)
    submitted = _submit(client, hr_auth, demand["id"], note="请业务负责人确认编制")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["approval_status"] == "pending"
    assert submitted.json()["data"]["matchable"] is False

    approved = _decide(client, dept_auth, demand["id"], "approve")
    assert approved.status_code == 200, approved.text
    row = approved.json()["data"]
    assert row["approval_status"] == "approved"
    assert row["approval_version"] == 1 and row["approved_version"] == 1
    assert row["approved_by"] is not None and row["approved_at"] is not None
    assert row["matchable"] is True

    records = _records(client, hr_auth, demand["id"])
    assert [r["action"] for r in records] == ["submit", "approve"]
    assert [r["from_status"] for r in records] == ["draft", "pending"]
    assert [r["to_status"] for r in records] == ["pending", "approved"]
    assert all(r["demand_version"] == 1 for r in records)
    assert records[0]["reason"] == "请业务负责人确认编制"
    assert "hr" in records[0]["actor_roles"]
    assert "dept_manager" in records[1]["actor_roles"]
    assert records[0]["snapshot_json"]["schema_version"] == "A11-v1"
    assert records[0]["snapshot_json"]["headcount"] == 3


def test_approval_gate_rejects_non_business_owner_roles(client, hr_auth, employee_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    assert _decide(client, hr_auth, demand["id"], "approve").status_code == 403
    assert _decide(client, employee_auth, demand["id"], "approve").status_code == 403
    assert client.post(
        f"/api/recruitment/demands/{demand['id']}/decision",
        json={"decision": "approve"},
    ).status_code == 401
    # 被拒后仍是 pending，可由有权角色正常审批
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 200


def test_duplicate_approval_is_rejected(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 200
    dup = _decide(client, dept_auth, demand["id"], "approve")
    assert dup.status_code == 409, dup.text
    assert "重复批准" in dup.text
    assert len(_records(client, hr_auth, demand["id"])) == 2

    fresh = _new_demand(client, hr_auth)
    assert _decide(client, dept_auth, fresh["id"], "approve").status_code == 409


def test_four_eyes_blocks_same_user_self_approval(client, admin_auth, admin2_auth):
    demand = _new_demand(client, admin_auth)
    assert _submit(client, admin_auth, demand["id"]).status_code == 200
    denied = _decide(client, admin_auth, demand["id"], "approve")
    assert denied.status_code == 409, denied.text
    assert "四眼原则" in denied.text
    # 另一个同职责账号可以审批（按用户 id 而非角色判定）
    assert _decide(client, admin2_auth, demand["id"], "approve").status_code == 200


def test_reject_requires_reason_and_allows_resubmit(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    assert _decide(client, dept_auth, demand["id"], "reject").status_code == 400
    rejected = _decide(client, dept_auth, demand["id"], "reject", "编制未获批")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["approval_status"] == "rejected"
    assert rejected.json()["data"]["matchable"] is False
    assert _submit(client, hr_auth, demand["id"]).status_code == 200
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 200
    assert [r["action"] for r in _records(client, hr_auth, demand["id"])] == [
        "submit", "reject", "submit", "approve",
    ]


def test_return_for_supplement_goes_back_to_draft(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    assert _decide(client, dept_auth, demand["id"], "return").status_code == 400
    returned = _decide(client, dept_auth, demand["id"], "return", "请补目标到岗日期")
    assert returned.status_code == 200, returned.text
    assert returned.json()["data"]["approval_status"] == "draft"
    # 草稿态可改内容，且不会产生新版本（原审批从未通过）
    patched = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"headcount": 7}, headers=hr_auth
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["approval_version"] == 1
    assert _submit(client, hr_auth, demand["id"]).status_code == 200
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 200


def test_invalid_decision_literal_is_rejected(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    resp = _decide(client, dept_auth, demand["id"], "force_approve")
    assert resp.status_code in (400, 422), resp.text
    assert len(_records(client, hr_auth, demand["id"])) == 1


# --------------------------------------------------------------------------- #
# 撤回、版本冻结、记录不可变
# --------------------------------------------------------------------------- #


def test_withdraw_requires_reason_and_is_terminal(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    empty = client.post(
        f"/api/recruitment/demands/{demand['id']}/withdraw", json={"reason": ""}, headers=hr_auth
    )
    assert empty.status_code in (400, 422), empty.text
    _submit(client, hr_auth, demand["id"])
    assert _withdraw(client, hr_auth, demand["id"]).status_code == 200
    assert _withdraw(client, hr_auth, demand["id"]).status_code == 409
    assert _submit(client, hr_auth, demand["id"]).status_code == 400
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 409
    assert [r["action"] for r in _records(client, hr_auth, demand["id"])] == [
        "submit", "withdraw",
    ]


def test_withdraw_is_not_open_to_business_owner(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    assert _withdraw(client, dept_auth, demand["id"]).status_code == 403
    assert _withdraw(client, hr_auth, demand["id"]).status_code == 200


def test_content_edit_after_approval_invalidates_version(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth, headcount=2)
    _submit(client, hr_auth, demand["id"])
    _decide(client, dept_auth, demand["id"], "approve")
    patched = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"headcount": 8}, headers=hr_auth
    )
    assert patched.status_code == 200, patched.text
    row = patched.json()["data"]
    assert row["approval_version"] == 2
    assert row["approval_status"] == "draft"
    assert row["approved_by"] is None and row["approved_at"] is None
    assert row["matchable"] is False

    records = _records(client, hr_auth, demand["id"])
    assert [r["action"] for r in records] == ["submit", "approve", "revision"]
    assert records[-1]["from_status"] == "approved"
    assert records[-1]["to_status"] == "draft"
    assert records[-1]["demand_version"] == 2
    # 快照是值拷贝：历史决策仍记录当时的 headcount=2，新版本记录 8
    assert records[0]["snapshot_json"]["headcount"] == 2
    assert records[1]["snapshot_json"]["headcount"] == 2
    assert records[-1]["snapshot_json"]["headcount"] == 8
    assert records[0]["snapshot_json"]["approval_version"] == 1
    assert records[-1]["snapshot_json"]["approval_version"] == 2


def test_edit_while_pending_is_blocked(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"headcount": 9}, headers=hr_auth
    )
    assert resp.status_code == 409, resp.text
    detail = client.get(
        f"/api/recruitment/demands/{demand['id']}", headers=hr_auth
    ).json()["data"]
    assert detail["headcount"] == 2 and detail["approval_status"] == "pending"
    assert _decide(client, dept_auth, demand["id"], "approve").status_code == 200


def test_business_status_flow_does_not_invalidate_approval(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    _decide(client, dept_auth, demand["id"], "approve")
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}",
        json={"status": "in_progress"},
        headers=hr_auth,
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"]
    assert row["status"] == "in_progress"
    assert row["approval_status"] == "approved"
    assert row["approval_version"] == 1
    assert row["matchable"] is True
    assert len(_records(client, hr_auth, demand["id"])) == 2


def test_approval_status_cannot_be_set_via_patch(client, hr_auth):
    demand = _new_demand(client, hr_auth)
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}",
        json={"approval_status": "approved"},
        headers=hr_auth,
    )
    assert resp.status_code in (400, 422), resp.text
    detail = client.get(
        f"/api/recruitment/demands/{demand['id']}", headers=hr_auth
    ).json()["data"]
    assert detail["approval_status"] == "draft" and detail["matchable"] is False


def test_approval_records_are_append_only_and_read_gated(client, hr_auth, dept_auth, employee_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    _decide(client, dept_auth, demand["id"], "approve")
    url = f"/api/recruitment/demands/{demand['id']}/approval-records"
    assert client.get(url, headers=employee_auth).status_code == 403
    assert client.get(url).status_code == 401
    assert client.get(url, headers=hr_auth).status_code == 200
    assert client.get(url, headers=dept_auth).status_code == 200
    before = _records(client, hr_auth, demand["id"])
    # 记录端点只提供 GET：任何写入方法都不得改动已落库的历史行。
    # 不同 FastAPI 版本对未注册方法可能返回 400/404/405，统一断言「非 2xx + 记录不变」。
    assert client.patch(url, json={"action": "approve"}, headers=hr_auth).status_code >= 400
    assert client.delete(url, headers=hr_auth).status_code >= 400
    assert _records(client, hr_auth, demand["id"]) == before
    records = before
    assert records == sorted(records, key=lambda r: r["id"])
    assert all(r["actor_user_id"] and r["actor_roles"] for r in records)


def test_list_and_detail_expose_approval_fields(client, hr_auth, dept_auth):
    demand = _new_demand(client, hr_auth)
    _submit(client, hr_auth, demand["id"])
    _decide(client, dept_auth, demand["id"], "approve")
    detail = client.get(
        f"/api/recruitment/demands/{demand['id']}", headers=hr_auth
    ).json()["data"]
    assert detail["matchable"] is True and detail["approval_status"] == "approved"
    listed = client.get("/api/recruitment/demands", headers=hr_auth).json()["data"]["items"]
    hit = next(item for item in listed if item["id"] == demand["id"])
    assert hit["matchable"] is True and hit["approved_version"] == 1


def test_legacy_row_gets_migration_server_defaults(client, hr_auth):
    """模拟迁移前历史行：只写旧列，DB server_default 必须给出 draft/1。"""
    name = f"A11-legacy-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO project_demands "
                "(position_name, headcount, priority, status) "
                "VALUES (:name, 1, 'medium', 'open')"
            ),
            {"name": name},
        )
        db.commit()
        demand_id = db.execute(
            text("SELECT id FROM project_demands WHERE position_name = :name"), {"name": name}
        ).scalar_one()
    row = client.get(
        f"/api/recruitment/demands/{demand_id}", headers=hr_auth
    ).json()["data"]
    assert row["approval_status"] == "draft"
    assert row["approval_version"] == 1
    assert row["approved_version"] is None
    assert row["matchable"] is False
