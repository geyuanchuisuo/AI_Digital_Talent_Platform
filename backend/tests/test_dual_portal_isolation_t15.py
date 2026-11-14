"""T-A15 · 双端隔离接口级反例（双端入口与数据范围回归）。

覆盖三条验收线：
1. **个人端不可见管理端功能**：personal 账号调 A 域管理接口（岗位配置/人才需求/
   筛选/匹配/JD 草稿）一律 403（ManagementDep，A06 统一口径）；
2. **不可读他人数据**：personal 伪造 talent_profile_id 查申请列表，后端
   AccountScope 强制收窄为本人（忽略前端参数，T-A60f 口径）；
3. **管理端不受影响**：hr 同接口回归 200。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

MANAGEMENT_GETS = [
    "/api/job-profiles",
    "/api/recruitment/demands",
    "/api/recruitment/demands/gap-summary",
    "/api/recruitment/jd-publications",
    "/api/recruitment/project-groups",
    "/api/screen/batches",
    "/api/match/batches",
]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


@pytest.fixture()
def personal_hdr(client: TestClient) -> dict[str, str]:
    return _login(client, "personal", "personal123456")


@pytest.fixture()
def hr_hdr(client: TestClient) -> dict[str, str]:
    return _login(client, "hr", "hr123456")


def test_personal_blocked_from_management_reads(client: TestClient, personal_hdr: dict):
    """个人账号访问管理端读接口 → 403（不可见管理端功能）。"""
    for url in MANAGEMENT_GETS:
        resp = client.get(url, headers=personal_hdr)
        assert resp.status_code == 403, f"{url}: {resp.status_code} {resp.text[:200]}"
        assert resp.json()["code"] == 403


def test_personal_blocked_from_management_writes(client: TestClient, personal_hdr: dict):
    """个人账号调管理端写接口 → 403（依赖门禁先于业务校验）。"""
    # POST jd-draft（合法最小 body）
    resp = client.post("/api/job-profiles/jd-draft", json={"title": "越权尝试"}, headers=personal_hdr)
    assert resp.status_code == 403, resp.text
    # 需求编辑（不存在的 id：403 必须先于 404/400 出现，证明门禁在依赖层生效）
    resp = client.patch("/api/recruitment/demands/999999", json={"headcount": 2}, headers=personal_hdr)
    assert resp.status_code == 403, resp.text


def test_hr_still_passes_management_reads(client: TestClient, hr_hdr: dict):
    """管理侧回归：hr 同批读接口 200（门禁不误伤）。"""
    for url in MANAGEMENT_GETS:
        resp = client.get(url, headers=hr_hdr)
        assert resp.status_code == 200, f"{url}: {resp.status_code} {resp.text[:200]}"


def test_personal_cannot_read_others_applications(client: TestClient, personal_hdr: dict):
    """个人端伪造 talent_profile_id → 后端忽略并收窄为本人（不可读他人数据）。"""
    FORGED_ID = 999999  # 不可能是本人（seed person_id 为 398）
    resp = client.get(
        "/api/recruitment/applications",
        params={"talent_profile_id": FORGED_ID, "page_size": 50},
        headers=personal_hdr,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    # 响应逐行校验：不含伪造 id；出参为个人侧脱敏结构（无 internal_stage）
    for row in items:
        assert row.get("talent_profile_id") != FORGED_ID
        assert "internal_stage" not in row  # _application_public_out 脱敏口径


def test_personal_applications_expose_only_public_fields(client: TestClient, personal_hdr: dict):
    """个人端申请列表对外字段白名单：无内部评分/推进原因等管理侧字段。"""
    resp = client.get("/api/recruitment/applications", headers=personal_hdr)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    allowed = {"id", "job_profile_id", "external_stage", "result_category", "result_published", "stage_updated_at"}
    for row in items:
        assert set(row.keys()) <= allowed, row.keys()
