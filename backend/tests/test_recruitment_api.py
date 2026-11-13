"""L2 集成测试 · 招聘域接口（M3-V2.6，T-A60b/c/d）。

真实 MySQL（隔离库）+ TestClient；覆盖：项目群/项目/需求 CRUD 与状态流转白名单、
重复需求 409、申请创建/五阶段自动映射/终态确认/同岗在途 409、JD 投放/撤回、鉴权反例。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_requires_token(client: TestClient):
    assert client.get("/api/recruitment/demands").status_code == 401
    assert client.post("/api/recruitment/demands", json={"position_name": "x", "headcount": 1}).status_code == 401


def test_group_project_demand_crud_and_status_flow(client: TestClient, auth: dict):
    # 项目群 → 项目 → 需求
    group = client.post(
        "/api/recruitment/project-groups", json={"name": _unique("项目群"), "description": "测试群"}, headers=auth
    ).json()["data"]
    project = client.post(
        "/api/recruitment/projects",
        json={"name": _unique("项目"), "project_group_id": group["id"]},
        headers=auth,
    ).json()["data"]
    assert project["project_group_id"] == group["id"]

    job_id = 1  # seed 岗位
    demand_name = _unique("后端工程师需求")
    resp = client.post(
        "/api/recruitment/demands",
        json={
            "position_name": demand_name,
            "headcount": 3,
            "project_id": project["id"],
            "project_group_id": group["id"],
            "job_profile_id": job_id,
            "priority": "high",
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    demand = resp.json()["data"]
    assert demand["status"] == "open" and demand["headcount"] == 3

    # 重复（同岗位同项目在途）→ 409
    resp = client.post(
        "/api/recruitment/demands",
        json={"position_name": demand_name, "headcount": 1, "project_id": project["id"]},
        headers=auth,
    )
    assert resp.status_code == 409, resp.text

    # 状态流转白名单：open → fulfilled 不允许（未推进不可直接满足；open→closed 取消合法）
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"status": "fulfilled"}, headers=auth
    )
    assert resp.status_code == 400, resp.text

    # open → in_progress → fulfilled
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"status": "in_progress", "headcount": 5}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "in_progress"
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"status": "fulfilled"}, headers=auth
    )
    assert resp.status_code == 200
    # fulfilled 终态不回退
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"status": "open"}, headers=auth
    )
    assert resp.status_code == 400

    # 非法优先级 → 422（Literal）
    resp = client.patch(
        f"/api/recruitment/demands/{demand['id']}", json={"priority": "urgent"}, headers=auth
    )
    assert resp.status_code == 422

    # 列表过滤
    resp = client.get("/api/recruitment/demands", params={"status": "fulfilled"}, headers=auth)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert any(d["id"] == demand["id"] for d in items)


def test_application_lifecycle_five_stage_mapping(client: TestClient, auth: dict):
    """V2.6 已确认映射逐段验证：创建→人工推进→对外阶段自动更新→终态确认。"""
    job_id = 1
    talent_id = 398  # seed 演示个人账号绑定的主档（虚构数据）

    # 无 token → 401
    assert client.post(
        "/api/recruitment/applications", json={"talent_profile_id": talent_id, "job_profile_id": job_id}
    ).status_code == 401

    resp = client.post(
        "/api/recruitment/applications",
        json={"talent_profile_id": talent_id, "job_profile_id": job_id, "resume_file_id": None},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    app_row = resp.json()["data"]
    assert app_row["internal_stage"] == "new" and app_row["external_stage"] == "submitted"

    # 同岗在途 → 409
    resp = client.post(
        "/api/recruitment/applications",
        json={"talent_profile_id": talent_id, "job_profile_id": job_id},
        headers=auth,
    )
    assert resp.status_code == 409

    # 推进链：screening → invited → deciding，对外阶段自动跟随
    for stage, external in (("screening", "screening"), ("invited", "interviewing"), ("deciding", "deciding")):
        resp = client.patch(
            f"/api/recruitment/applications/{app_row['id']}/stage",
            json={"internal_stage": stage, "reason": f"推进到 {stage}"},
            headers=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["external_stage"] == external, (stage, data)
        assert data["stage_reason"] == f"推进到 {stage}"

    # 终态：offer_accepted → closed + result_category=hired（未确认时 result_published=False）
    resp = client.patch(
        f"/api/recruitment/applications/{app_row['id']}/stage",
        json={"internal_stage": "offer_accepted"},
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["external_stage"] == "closed" and data["result_category"] == "hired"
    assert data["result_published"] is False  # HR 确认前不对外发布

    # 终态再流转 → 400
    resp = client.patch(
        f"/api/recruitment/applications/{app_row['id']}/stage",
        json={"internal_stage": "rejected"},
        headers=auth,
    )
    assert resp.status_code == 400

    # HR 确认 → result_published=True；重复确认幂等
    resp = client.post(f"/api/recruitment/applications/{app_row['id']}/result-confirm", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["data"]["result_published"] is True
    resp = client.post(f"/api/recruitment/applications/{app_row['id']}/result-confirm", headers=auth)
    assert resp.status_code == 200

    # 非法阶段 → 400
    resp = client.patch(
        f"/api/recruitment/applications/{app_row['id']}/stage",
        json={"internal_stage": "flying"},
        headers=auth,
    )
    assert resp.status_code == 400


def test_jd_publication_withdraw(client: TestClient, auth: dict):
    job_id = 1
    title = _unique("JD 投放")
    resp = client.post(
        "/api/recruitment/jd-publications",
        json={
            "job_profile_id": job_id,
            "title": title,
            "content": {"title": title, "jd_text": "测试 JD 正文", "requirements": ["python"]},
            "channel": "internal-portal",
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    pub = resp.json()["data"]
    assert pub["status"] == "published" and pub["published_at"]

    # 撤回 → 留痕
    resp = client.post(f"/api/recruitment/jd-publications/{pub['id']}/withdraw", headers=auth)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "withdrawn" and data["withdrawn_by"] is not None

    # 二次撤回 → 400
    resp = client.post(f"/api/recruitment/jd-publications/{pub['id']}/withdraw", headers=auth)
    assert resp.status_code == 400

    # 缺必填 → 400
    resp = client.post("/api/recruitment/jd-publications", json={"job_profile_id": job_id}, headers=auth)
    assert resp.status_code == 400


def test_demand_gap_summary_t_a24(client: TestClient, auth: dict):
    """T-A24 缺口统计：gap = headcount − HR 确认已录用数（offer_accepted + result_confirmed_by 非空）。

    独立岗位隔离（不与 seed 岗位 1 的其他用例数据互相污染）；覆盖：无申请、
    录用未确认不计入、确认后计入、未录用终态不计入、无关联岗位 demand、状态过滤。
    """
    # 无 token → 401
    assert client.get("/api/recruitment/demands/gap-summary").status_code == 401

    # 独立岗位（合法权重合计 100，pass>pending）
    job_title = _unique("缺口统计岗位")
    resp = client.post(
        "/api/job-profiles",
        json={
            "title": job_title,
            "jd_text": "职责：缺口统计验证",
            "requirements_text": "要求：python",
            "min_degree": "本科",
            "pass_threshold": 75,
            "pending_threshold": 60,
            "weights": {"skills": 40, "experience": 30, "education": 15, "others": 15},
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]

    # 项目群 → 项目
    group = client.post(
        "/api/recruitment/project-groups", json={"name": _unique("缺口群")}, headers=auth
    ).json()["data"]
    project = client.post(
        "/api/recruitment/projects", json={"name": _unique("缺口项目"), "project_group_id": group["id"]}, headers=auth
    ).json()["data"]

    # 两条需求：A 关联岗位 headcount=3；B 无关联岗位 headcount=2（无录用路径）
    demand_a = client.post(
        "/api/recruitment/demands",
        json={
            "position_name": _unique("缺口需求A"),
            "headcount": 3,
            "project_id": project["id"],
            "project_group_id": group["id"],
            "job_profile_id": job_id,
        },
        headers=auth,
    ).json()["data"]
    demand_b = client.post(
        "/api/recruitment/demands",
        json={"position_name": _unique("缺口需求B"), "headcount": 2, "project_id": project["id"]},
        headers=auth,
    ).json()["data"]

    def _gap_row(demand_id: int) -> dict:
        resp = client.get(
            "/api/recruitment/demands/gap-summary", params={"project_id": project["id"]}, headers=auth
        )
        assert resp.status_code == 200, resp.text
        rows = {r["demand_id"]: r for r in resp.json()["data"]["items"]}
        return rows[demand_id]

    # 无申请：A gap=3、B gap=2
    row_a, row_b = _gap_row(demand_a["id"]), _gap_row(demand_b["id"])
    assert (row_a["headcount"], row_a["hired_count"], row_a["gap"]) == (3, 0, 3)
    assert (row_b["headcount"], row_b["hired_count"], row_b["gap"]) == (2, 0, 2)

    talent_id = 398  # seed 演示个人账号绑定的主档（虚构数据）
    # 推进到 offer_accepted 但未 HR 确认 → 不计入已录用
    resp = client.post(
        "/api/recruitment/applications", json={"talent_profile_id": talent_id, "job_profile_id": job_id}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    app_row = resp.json()["data"]
    resp = client.patch(
        f"/api/recruitment/applications/{app_row['id']}/stage", json={"internal_stage": "offer_accepted"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    row_a = _gap_row(demand_a["id"])
    assert (row_a["hired_count"], row_a["gap"]) == (0, 3)

    # HR 确认录用 → 计入，gap=2
    resp = client.post(f"/api/recruitment/applications/{app_row['id']}/result-confirm", headers=auth)
    assert resp.status_code == 200, resp.text
    row_a = _gap_row(demand_a["id"])
    assert (row_a["hired_count"], row_a["gap"]) == (1, 2)

    # 终态后重新申请同岗 → 推进 rejected + 确认 not_hired → 不计入已录用
    resp = client.post(
        "/api/recruitment/applications", json={"talent_profile_id": talent_id, "job_profile_id": job_id}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    app2 = resp.json()["data"]
    for stage in ("screening", "deciding", "rejected"):
        resp = client.patch(
            f"/api/recruitment/applications/{app2['id']}/stage",
            json={"internal_stage": stage, "reason": f"推进 {stage}"},
            headers=auth,
        )
        assert resp.status_code == 200, (stage, resp.text)
    resp = client.post(f"/api/recruitment/applications/{app2['id']}/result-confirm", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["result_category"] == "not_hired"
    row_a = _gap_row(demand_a["id"])
    assert (row_a["hired_count"], row_a["gap"]) == (1, 2)

    # 状态过滤：A/B 仍 open；过滤到 closed 时本组两条均不出现
    resp = client.get(
        "/api/recruitment/demands/gap-summary", params={"status": "open", "project_id": project["id"]}, headers=auth
    )
    assert resp.status_code == 200
    assert {r["demand_id"] for r in resp.json()["data"]["items"]} == {demand_a["id"], demand_b["id"]}
    resp = client.get(
        "/api/recruitment/demands/gap-summary", params={"status": "closed", "project_id": project["id"]}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []

    # 非法 status → 400（与 list_demands 同口径）
    resp = client.get("/api/recruitment/demands/gap-summary", params={"status": "bogus"}, headers=auth)
    assert resp.status_code == 400
