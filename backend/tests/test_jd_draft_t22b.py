"""T-A22 · AI JD 草稿端点（POST /api/job-profiles/jd-draft）。

口径验证：真实 LLM 调用走 mock 注入（本套件 L1/L2 不依赖外部服务）；
模型失败/空返回 → 503 明确报错，**不返回样例文案**；成功响应带
model_version / prompt_version / generated_at 留痕（需求 D-04 验收点）。
草稿不落库，保存走岗位 POST/PUT（已有测试覆盖）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.llm_client import LLMError
from app.main import app

JD_DRAFT_URL = "/api/job-profiles/jd-draft"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


class _FakeClient:
    def __init__(self, *, text: str = "", error: Exception | None = None, model: str = "fake-jd-model"):
        self._text = text
        self._error = error
        self.model = model

    def chat_text(self, prompt: str, **kwargs):  # noqa: ANN001, ANN003 —— 测试替身
        if self._error is not None:
            raise self._error
        return self._text


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("app.core.llm_client.get_llm_client", lambda: fake)


def test_jd_draft_requires_token(client: TestClient):
    assert client.post(JD_DRAFT_URL, json={"title": "后端工程师"}).status_code == 401


def test_jd_draft_success_returns_markdown_with_versions(client: TestClient, monkeypatch):
    """成功：draft_md 为模型原文 + model/prompt 版本与时间留痕；不落库。"""
    auth = _auth(client)
    draft_text = "## 岗位职责\n1. 负责招聘平台后端开发。\n\n## 任职要求\n1. 本科及以上。\n\n## 加分项\n1. 熟悉 Milvus。\n"
    _patch_client(monkeypatch, _FakeClient(text=draft_text))

    resp = client.post(
        JD_DRAFT_URL,
        json={"title": "后端工程师", "department": "平台部", "must_skills": ["python", "mysql"]},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["draft_md"] == draft_text.strip()  # 原文透传（仅 strip 首尾空白），不加样例、不裁剪
    assert data["model_version"] == "fake-jd-model"
    assert data["prompt_version"] == "jd-v1"
    assert data["generated_at"]


def test_jd_draft_llm_failure_returns_503_without_sample(client: TestClient, monkeypatch):
    """模型失败：503 明确报错；响应中不含任何 JD 样例正文（验收点「不返回样例」）。"""
    auth = _auth(client)
    _patch_client(monkeypatch, _FakeClient(error=LLMError("上游网关超时")))

    resp = client.post(JD_DRAFT_URL, json={"title": "后端工程师"}, headers=auth)
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == 503
    assert "JD 草稿生成失败" in body["message"]
    assert "上游网关超时" in body["message"]
    assert not body["data"]  # 无样例兜底


def test_jd_draft_empty_output_returns_503(client: TestClient, monkeypatch):
    """空返回：同样 503 明确报错，不用空白或样例顶替。"""
    auth = _auth(client)
    _patch_client(monkeypatch, _FakeClient(text="   \n  "))

    resp = client.post(JD_DRAFT_URL, json={"title": "后端工程师"}, headers=auth)
    assert resp.status_code == 503, resp.text
    assert "空内容" in resp.json()["message"]


def test_jd_draft_missing_title_422(client: TestClient):
    """title 缺失 → 422（schema 校验），不触发模型调用。"""
    auth = _auth(client)
    resp = client.post(JD_DRAFT_URL, json={"department": "平台部"}, headers=auth)
    assert resp.status_code == 422, resp.text
