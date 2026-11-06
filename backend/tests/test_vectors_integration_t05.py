"""L2 集成测试 · T-05 Embedding + Milvus 适配层（真实 Milvus / 真实 Embedding / 真实 MySQL）。

覆盖 todolist T-05 验收点：
1. **按生产 schema 补验 upsert 语义**：`talent_profile_id` INT64 主键非自增，同 PK 二次 upsert
   覆盖而非新增（T-37 只验了 `auto_id=True` + insert）；
2. upsert → search → delete 往返；
3. 维度不符（`EMBEDDING_DIM` 与 collection 不一致）必须报错；
4. 真实 Embedding 调用成功后 `embedding_alive=true`（被动探活）；
5. 同步路径超 `VECTOR_SYNC_TIMEOUT` 返回失败而非阻塞；
6. `rebuild_vectors` 按 `profile_text_hash` 差异决定是否重嵌（hash 未变零 embedding）。

运行前置：Milvus 19530 在监听（挂了按《开发日志·环境坑复现速查卡》卡 B 恢复）。
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.api import health as health_api
from app.config import settings
from app.core import milvus_store as ms
from app.core.embedding_client import EmbeddingClient, EmbeddingError, clear_query_cache
from app.core.milvus_store import CollectionDimMismatch, MilvusStore
from app.core.profile_text import compute_text_hash
from app.database import SessionLocal
from app.models.talent import TalentProfile, TalentTag
from scripts.rebuild_vectors import run_rebuild

pytestmark = pytest.mark.integration

COLLECTION = "aitalent_t05_it"
DIM = settings.embedding_dim


@pytest.fixture(scope="module")
def store() -> MilvusStore:
    probe = MilvusStore(uri=settings.milvus_uri, collection=COLLECTION, dim=DIM)
    try:
        probe.drop()
        probe.ensure_collection()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus 未就绪（{type(exc).__name__}: {exc}），按速查卡 B 恢复后重跑")
    yield probe
    probe.drop()
    probe.close()


@pytest.fixture(autouse=True)
def _reset_alive():
    health_api._embedding_alive = None
    clear_query_cache()
    yield
    health_api._embedding_alive = None
    clear_query_cache()


def test_upsert_search_delete_roundtrip_with_production_schema(store: MilvusStore):
    base = [0.001] * DIM
    for offset, pid in enumerate((90001, 90002, 90003), start=1):
        vector = [value * offset for value in base]
        store.upsert(pid, vector, f"{offset:064d}")
    assert store.count() == 3

    # 同 PK 二次 upsert：覆盖（count 不变、text_hash 更新）
    updated = [0.002] * DIM
    store.upsert(90001, updated, "f" * 64)
    assert store.count() == 3
    assert store.query_hash(90001) == "f" * 64

    hits = store.search(updated, top_k=2)
    assert len(hits) == 2
    assert hits[0].talent_profile_id == 90001  # 同向量必然最相似
    assert hits[0].score > 0.99
    assert hits[0].text_hash == "f" * 64

    store.delete(90001)
    assert store.count() == 2
    assert store.query_hash(90001) is None


def test_dimension_mismatch_raises_on_existing_collection(store: MilvusStore):
    wrong = MilvusStore(uri=settings.milvus_uri, collection=COLLECTION, dim=64)
    with pytest.raises(CollectionDimMismatch) as excinfo:
        wrong.ensure_collection()
    assert "64" in str(excinfo.value) and str(DIM) in str(excinfo.value)


def test_real_embedding_call_sets_embedding_alive():
    client = EmbeddingClient()
    vector = client.embed_one("Python 后端工程师，5 年经验，熟悉 FastAPI 与 MySQL")
    assert len(vector) == DIM
    assert health_api.get_embedding_alive() is True


def test_sync_vector_by_text_real_and_timeout(store: MilvusStore, monkeypatch):
    monkeypatch.setattr(ms, "get_store", lambda: store)

    ok_result = ms.sync_vector_by_text(90010, "画像文本·正常预算", budget=10.0, persist_status=False)
    assert ok_result.ok is True
    assert store.query_hash(90010) == ok_result.text_hash

    started = time.monotonic()
    timeout_result = ms.sync_vector_by_text(90011, "画像文本·极小预算", budget=0.001, persist_status=False)
    elapsed = time.monotonic() - started
    assert timeout_result.ok is False
    assert "超时" in timeout_result.error
    assert elapsed < 1.0  # 超预算立即返回，不阻塞请求

    store.delete(90010)


def test_rebuild_uses_hash_diff_to_skip_unchanged(store: MilvusStore):
    with SessionLocal() as db:
        profile = TalentProfile(
            name="T05重建测试",
            current_title="后端工程师",
            expected_title="高级后端工程师",
            city="杭州",
            degree="本科",
            work_years=Decimal("6.0"),
            skills_json=["Python", "MySQL"],
            tags_json=["后端", "互联网"],
            source="manual",
        )
        db.add(profile)
        db.flush()
        profile_id = int(profile.id)
        db.add(TalentTag(talent_profile_id=profile_id, tag="后端", tag_type="skill", source="llm"))
        db.add(TalentTag(talent_profile_id=profile_id, tag="互联网", tag_type="industry", source="llm"))
        db.commit()

    try:
        first = run_rebuild(store=store)
        assert first.total_profiles >= 1
        assert first.failed == 0
        assert first.embedded >= 1
        assert first.upserted >= 1

        with SessionLocal() as db:
            row = db.execute(select(TalentProfile).where(TalentProfile.id == profile_id)).scalar_one()
            assert row.vector_status == "synced"
            assert row.vector_synced_at is not None
            assert row.profile_text_hash
            stored_hash = row.profile_text_hash
        assert len(stored_hash) == 64
        assert store.query_hash(profile_id) == stored_hash

        # 第二次：hash 未变 → 零 embedding 调用
        second = run_rebuild(store=store)
        assert second.skipped_same_hash >= 1
        assert second.embedded == 0
        assert second.upserted == 0

        # 改 city → hash 必变（§6.3 任何画像字段变更都重算）
        with SessionLocal() as db:
            row = db.execute(select(TalentProfile).where(TalentProfile.id == profile_id)).scalar_one()
            row.city = "深圳"
            row.vector_status = "pending"
            db.commit()
        third = run_rebuild(store=store)
        assert third.embedded >= 1
        with SessionLocal() as db:
            row = db.execute(select(TalentProfile).where(TalentProfile.id == profile_id)).scalar_one()
            assert row.profile_text_hash != stored_hash
    finally:
        with SessionLocal() as db:
            db.execute(delete(TalentTag).where(TalentTag.talent_profile_id == profile_id))
            db.execute(delete(TalentProfile).where(TalentProfile.id == profile_id))
            db.commit()
        try:
            store.delete(profile_id)
        except Exception:  # noqa: BLE001 - 清理失败不影响结论
            pass


def test_embedding_failure_degrades_health_while_milvus_ok(store: MilvusStore):
    """验收点：**仅 Embedding 挂掉（Milvus 正常）** → `embedding_alive=false` 且 health degraded。"""
    bad = EmbeddingClient(api_key="sk-invalid-t05-probe", dim=DIM, timeout=15)
    with pytest.raises(EmbeddingError):
        bad.embed_one("用错误密钥触发一次真实失败调用")
    assert health_api.get_embedding_alive() is False

    body = health_api.health()
    assert body["milvus"] is True  # Milvus 正常
    assert body["db"] is True
    assert body["embedding_alive"] is False
    assert body["status"] == "degraded"


def test_rebuild_dry_run_makes_no_external_calls(store: MilvusStore, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应调用 embedding / milvus")

    monkeypatch.setattr("scripts.rebuild_vectors.embed_texts", boom)
    monkeypatch.setattr(store, "upsert_many", boom)
    summary = run_rebuild(store=store, dry_run=True)
    assert summary.dry_run is True
    assert summary.embedded == 0
    assert summary.upserted == 0


def test_profile_text_hash_matches_milvus_text_hash(store: MilvusStore):
    text = "张三｜后端工程师｜期望:｜上海\n学历:本科 5年经验"
    digest = compute_text_hash(text)
    store.upsert(90020, [0.003] * DIM, digest)
    assert store.query_hash(90020) == digest
    store.delete(90020)
