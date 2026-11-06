"""T-19 L2：语义检索接口集成测试（真实 MySQL；Embedding/Milvus 用假件）。

覆盖 todolist T-19 验收点：
1. 命中结果按相似度序回传、回查 MySQL 排除软删与已消失画像（残留向量兜底）；
2. `top_k` 透传 Milvus（默认 20、上限 100 由 Query 约束）；
3. 仅 Embedding 挂掉 → 503 而非空列表（本接口无业务写入，503 合法）；
4. 仅 Milvus 挂掉 → 503；
5. query 校验（空 / 超 500 字）。

「3 条自然语言查询相关性人工核验 + 相同查询二次调用命中缓存」属真实服务验收，
见 T-19 真实冒烟（开发日志 Layer 3）。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete

from app.core import embedding_client, milvus_store
from app.core.milvus_store import SearchHit
from app.database import SessionLocal
from app.models import TalentProfile, TalentTag
from app.service import talent_service as svc

pytestmark = pytest.mark.integration


class FakeStore:
    def __init__(self) -> None:
        self.down = False
        self.hits: list[SearchHit] = []
        self.last_top_k: int | None = None

    def search(self, vector, top_k: int = 20) -> list[SearchHit]:
        if self.down:
            raise RuntimeError("Milvus 不可用（测试模拟）")
        assert len(vector) == 1024
        self.last_top_k = top_k
        return self.hits[:top_k]


@pytest.fixture()
def fakes(monkeypatch):
    store = FakeStore()
    calls = {"queries": []}

    def fake_embed_query(text: str):
        calls["queries"].append(text)
        return [0.002] * 1024

    monkeypatch.setattr(milvus_store, "_default_store", store)
    monkeypatch.setattr(embedding_client, "embed_query", fake_embed_query)
    return SimpleNamespace(store=store, calls=calls)


@pytest.fixture()
def env():
    profile_ids: list[int] = []

    def make_profile(**fields) -> int:
        fields.setdefault("name", "张三")
        with SessionLocal() as db:
            row = TalentProfile(vector_status=fields.pop("vector_status", "synced"), **fields)
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id

    def make_tags(profile_id: int, rows: list[tuple[str, str, str]]) -> None:
        with SessionLocal() as db:
            for tag, tag_type, source in rows:
                db.add(
                    TalentTag(
                        talent_profile_id=profile_id, tag=tag, tag_type=tag_type, source=source
                    )
                )
            db.commit()

    yield SimpleNamespace(make_profile=make_profile, make_tags=make_tags)

    with SessionLocal() as db:
        db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
        db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
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


def test_search_order_excludes_deleted_and_missing(env, fakes, client, auth):
    p1 = env.make_profile(name="张三", current_title="后端工程师", degree="本科",
                          work_years=Decimal("4.0"), city="深圳")
    p2 = env.make_profile(name="李四", current_title="算法工程师", vector_status="pending")
    env.make_tags(p1, [("python", "skill", "llm"), (f"标签{uuid4().hex[:6]}", "general", "llm")])
    gone_id = 987654321  # Milvus 残留向量但 MySQL 无此行
    fakes.store.hits = [
        SearchHit(talent_profile_id=p1, score=0.83, text_hash="a" * 64),
        SearchHit(talent_profile_id=gone_id, score=0.9, text_hash="b" * 64),  # 必须被兜底过滤
        SearchHit(talent_profile_id=p2, score=0.66666, text_hash="c" * 64),
    ]

    resp = client.get("/api/talents/search", params={"query": "会 python 的后端"}, headers=auth)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert [item["id"] for item in items] == [p1, p2]  # gone 被排除，其余保持相似度序
    assert items[0]["similarity"] == 0.83
    assert items[1]["similarity"] == 0.6667  # clamp 后保留 4 位
    assert items[0]["tags"] and items[0]["tag_total"] == 2
    assert items[0]["vector_status"] == "synced"
    assert fakes.store.last_top_k == 20  # 默认 top_k 透传
    assert fakes.calls["queries"] == ["会 python 的后端"]


def test_search_top_k_passthrough(env, fakes, client, auth):
    p1 = env.make_profile(name="张三")
    fakes.store.hits = [SearchHit(talent_profile_id=p1, score=0.5, text_hash=None)]
    resp = client.get("/api/talents/search", params={"query": "任意", "top_k": 1}, headers=auth)
    assert resp.status_code == 200, resp.text
    assert fakes.store.last_top_k == 1
    assert len(resp.json()["data"]["items"]) == 1

    resp = client.get("/api/talents/search", params={"query": "任意", "top_k": 101}, headers=auth)
    assert resp.status_code == 422  # 上限 100 由 Query 约束


def test_search_query_validation(env, fakes, client, auth):
    resp = client.get("/api/talents/search", headers=auth)  # 缺 query
    assert resp.status_code == 422
    resp = client.get("/api/talents/search", params={"query": ""}, headers=auth)
    assert resp.status_code == 422
    resp = client.get("/api/talents/search", params={"query": "x" * 501}, headers=auth)
    assert resp.status_code == 422


def test_search_service_rejects_blank_query():
    from app.core.errors import BizError

    class _FakeSession:  # 不触库：校验先于任何查询
        pass

    with pytest.raises(BizError) as exc:
        svc.search_talents(_FakeSession(), query="   ", top_k=20)
    assert exc.value.code == 400


def test_search_embedding_down_503(env, fakes, client, auth, monkeypatch):
    def broken_embed_query(text: str):
        raise RuntimeError("Embedding 不可用（测试模拟）")

    monkeypatch.setattr(embedding_client, "embed_query", broken_embed_query)
    resp = client.get("/api/talents/search", params={"query": "任意"}, headers=auth)
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == 503  # 不是空列表伪装成功


def test_search_milvus_down_503(env, fakes, client, auth):
    fakes.store.down = True
    resp = client.get("/api/talents/search", params={"query": "任意"}, headers=auth)
    assert resp.status_code == 503, resp.text


def test_search_requires_token(env, fakes, client):
    assert client.get("/api/talents/search", params={"query": "任意"}).status_code == 401
