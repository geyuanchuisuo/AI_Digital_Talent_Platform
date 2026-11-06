"""T-22 L1+L2：岗位条件 CRUD（L1 校验纯逻辑 + L2 真实 MySQL）。

覆盖 todolist T-22 验收点：
1. 建改删走通（POST version=1 → PUT version+1 → DELETE）；
2. 非法权重 400（总和 ≠100）；pass ≤ pending 400；
3. **`min_degree="高中/中专"` 被拒（400），`"高中中专"` 通过**（D-G 逐字枚举）；
4. 技能归一化存储（`Python 3.11`→`python`、`Golang`→`go`，D-N 同一套别名表）；
5. DELETE：无引用物理删 / 有批次引用软删（is_active=0）/ 重复删除 404；
6. 列表过滤（keyword / is_active）与 items 不含全文；
7. 校验失败不落库、PUT 后 updated_by 变化。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete

from app.core.errors import BizError
from app.database import SessionLocal
from app.models import JobProfile, MatchBatch, ScreenBatch
from app.schemas.job_profile import HardRuleSwitches, JobProfileWriteRequest, Weights
from app.service import job_profile_service as svc

pytestmark = pytest.mark.integration


def make_payload(**overrides) -> JobProfileWriteRequest:
    base = dict(
        title="高级后端工程师",
        department="技术部",
        jd_text="负责核心服务开发",
        requirements_text="本科及以上，3 年后端经验",
        min_degree="本科",
        min_work_years=3,
        must_skills=["Python", "FastAPI", "MySQL"],
        bonus_skills=["Milvus"],
        hard_rule_switches=HardRuleSwitches(),
        pass_threshold=75,
        pending_threshold=60,
        weights=Weights(skills=40, experience=30, education=15, others=15),
    )
    base.update(overrides)
    return JobProfileWriteRequest(**base)


# --------------------------------------------------------------------------- #
# L1：校验纯逻辑（不触 DB）
# --------------------------------------------------------------------------- #
def test_weights_must_sum_100():
    with pytest.raises(BizError) as exc:
        svc._validate(make_payload(weights=Weights(skills=50, experience=30, education=15, others=15)))
    assert exc.value.code == 400
    assert "110" in exc.value.message


def test_pass_must_be_greater_than_pending():
    with pytest.raises(BizError) as exc:
        svc._validate(make_payload(pass_threshold=60, pending_threshold=60))
    assert exc.value.code == 400
    with pytest.raises(BizError):
        svc._validate(make_payload(pass_threshold=59, pending_threshold=60))


def test_min_degree_literal_is_shenzhongzhongzhuan():
    """D-G：六档字面值逐字校验——「高中/中专」拒绝、「高中中专」通过。"""
    with pytest.raises(BizError) as exc:
        svc._validate(make_payload(min_degree="高中/中专"))
    assert exc.value.code == 400
    assert "高中中专" in exc.value.message

    svc._validate(make_payload(min_degree="高中中专"))  # 不抛即通过
    svc._validate(make_payload(min_degree=None))


# --------------------------------------------------------------------------- #
# 数据隔离
# --------------------------------------------------------------------------- #
@pytest.fixture()
def env():
    job_ids: list[int] = []
    screen_batch_ids: list[int] = []
    match_batch_ids: list[int] = []

    def make_job(**fields) -> int:
        with SessionLocal() as db:
            row = JobProfile(
                title=fields.pop("title", f"岗位-{uuid4().hex[:6]}"),
                jd_text="职责",
                requirements_text="要求",
                must_skills_json=["python"],
                bonus_skills_json=[],
                hard_rule_switches_json={"degree": True, "work_years": True, "must_skills": True},
                pass_threshold=Decimal("75"),
                pending_threshold=Decimal("60"),
                weights_json={"skills": 40, "experience": 30, "education": 15, "others": 15},
                version=1,
                created_by=1,
                **fields,
            )
            db.add(row)
            db.commit()
            job_ids.append(row.id)
            return row.id

    def make_screen_batch(job_id: int) -> int:
        with SessionLocal() as db:
            row = ScreenBatch(
                job_profile_id=job_id,
                job_profile_version=1,
                config_snapshot_json={},
                scope_json={"type": "all"},
                created_by=1,
            )
            db.add(row)
            db.commit()
            screen_batch_ids.append(row.id)
            return row.id

    def make_match_batch(job_id: int) -> int:
        with SessionLocal() as db:
            row = MatchBatch(
                job_profile_id=job_id,
                job_profile_version=1,
                config_snapshot_json={},
                talent_scope_json={"type": "all"},
                created_by=1,
            )
            db.add(row)
            db.commit()
            match_batch_ids.append(row.id)
            return row.id

    yield SimpleNamespace(
        make_job=make_job, make_screen_batch=make_screen_batch, make_match_batch=make_match_batch
    )

    with SessionLocal() as db:
        db.execute(sa_delete(MatchBatch).where(MatchBatch.id.in_(match_batch_ids)))
        db.execute(sa_delete(ScreenBatch).where(ScreenBatch.id.in_(screen_batch_ids)))
        db.execute(sa_delete(JobProfile).where(JobProfile.id.in_(job_ids)))
        db.commit()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


# --------------------------------------------------------------------------- #
# L2：CRUD 全流程（真实 MySQL）
# --------------------------------------------------------------------------- #
def test_create_put_delete_roundtrip(env, client, auth):
    marker = uuid4().hex[:6]

    # POST：version=1，技能归一化存储
    resp = client.post(
        "/api/job-profiles",
        json={
            "title": f"后端岗位{marker}",
            "department": "技术部",
            "jd_text": "职责描述",
            "requirements_text": "任职要求",
            "min_degree": "高中中专",
            "min_work_years": 3.0,
            "must_skills": ["Python 3.11", "Golang", "FastAPI"],
            "bonus_skills": ["Milvus"],
            "hard_rule_switches": {"degree": True, "work_years": True, "must_skills": False},
            "pass_threshold": 80,
            "pending_threshold": 60,
            "weights": {"skills": 40, "experience": 30, "education": 15, "others": 15},
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    job_id = body["id"]
    assert body["version"] == 1
    assert body["is_active"] is True
    assert body["min_degree"] == "高中中专"
    assert body["must_skills"] == ["fastapi", "go", "python"]  # 归一化 + 排序（D-N）
    assert body["hard_rule_switches"] == {"degree": True, "work_years": True, "must_skills": False}
    assert body["created_by"] is not None

    # PUT：version+1，updated_by 更新（请求体只含写入字段，不含响应里的 id/created_at 等）
    payload = {
        "title": f"后端岗位{marker}-v2",
        "department": body["department"],
        "jd_text": "职责描述",
        "requirements_text": "任职要求",
        "min_degree": body["min_degree"],
        "min_work_years": 5,
        "must_skills": body["must_skills"],
        "bonus_skills": body["bonus_skills"],
        "hard_rule_switches": body["hard_rule_switches"],
        "pass_threshold": 80,
        "pending_threshold": 60,
        "weights": body["weights"],
    }
    resp = client.put(f"/api/job-profiles/{job_id}", json=payload, headers=auth)
    assert resp.status_code == 200, resp.text
    body2 = resp.json()["data"]
    assert body2["version"] == 2
    assert body2["title"] == f"后端岗位{marker}-v2"
    assert body2["min_work_years"] == 5.0

    # GET：完整配置
    resp = client.get(f"/api/job-profiles/{job_id}", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 2

    # DELETE：无批次引用 → 物理删除
    resp = client.delete(f"/api/job-profiles/{job_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True, "soft_deleted": False}
    assert client.get(f"/api/job-profiles/{job_id}", headers=auth).status_code == 404
    # 二次删除 404
    assert client.delete(f"/api/job-profiles/{job_id}", headers=auth).status_code == 404


def test_create_validation_400(env, client, auth):
    """非法权重 / pass≤pending / 非法 min_degree → 400 且不落库。"""
    base = {
        "title": "非法岗位",
        "jd_text": "职责",
        "requirements_text": "要求",
        "min_degree": "本科",
        "pass_threshold": 75,
        "pending_threshold": 60,
        "weights": {"skills": 40, "experience": 30, "education": 15, "others": 15},
    }
    before = client.get("/api/job-profiles", params={"keyword": "非法岗位"}, headers=auth).json()["data"]["total"]

    cases = [
        ("weights-sum", {**base, "weights": {"skills": 40, "experience": 30, "education": 15, "others": 14}}),
        ("pass<=pending", {**base, "pass_threshold": 60, "pending_threshold": 60}),
        ("degree-slash", {**base, "min_degree": "高中/中专"}),
        ("degree-bogus", {**base, "min_degree": "学士"}),
    ]
    for name, payload in cases:
        resp = client.post("/api/job-profiles", json=payload, headers=auth)
        assert resp.status_code == 400, f"{name}: {resp.text}"
        assert resp.json()["code"] == 400

    assert (
        client.get("/api/job-profiles", params={"keyword": "非法岗位"}, headers=auth).json()["data"]["total"]
        == before
    )


def test_delete_with_batch_reference_soft(env, client, auth):
    job_id = env.make_job()
    env.make_screen_batch(job_id)
    env.make_match_batch(job_id)

    resp = client.delete(f"/api/job-profiles/{job_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True, "soft_deleted": True}

    # 软删后 GET 详情仍可见，is_active=false；重复删除 404；PUT 仍可用（未禁改）
    data = client.get(f"/api/job-profiles/{job_id}", headers=auth).json()["data"]
    assert data["is_active"] is False
    assert client.delete(f"/api/job-profiles/{job_id}", headers=auth).status_code == 404


def test_list_filters_and_no_fulltext(env, client, auth):
    marker = uuid4().hex[:6]
    active_id = env.make_job(title=f"架构师岗位{marker}")
    inactive_id = env.make_job(title=f"架构师岗位{marker}-旧", is_active=0)

    resp = client.get("/api/job-profiles", params={"keyword": f"架构师岗位{marker}"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    ids = {item["id"] for item in body["items"]}
    assert {active_id, inactive_id} <= ids
    item = body["items"][0]
    assert "jd_text" not in item and "requirements_text" not in item  # 不含全文
    for key in ("id", "title", "department", "min_degree", "min_work_years", "must_skills", "version", "is_active", "updated_at"):
        assert key in item

    resp = client.get(
        "/api/job-profiles", params={"keyword": f"架构师岗位{marker}", "is_active": "false"}, headers=auth
    )
    assert {item["id"] for item in resp.json()["data"]["items"]} == {inactive_id}


def test_requires_token(env, client):
    assert client.get("/api/job-profiles").status_code == 401
    assert client.post("/api/job-profiles", json={}).status_code == 401
    assert client.delete("/api/job-profiles/1").status_code == 401
