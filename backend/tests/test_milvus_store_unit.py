"""L1 单元测试 · milvus_store（假 client，不连真实 Milvus）。

覆盖：生产 schema 自检（主键/维度/text_hash）、维度不符报错、upsert 载荷、
COSINE 检索解析与 topK 上限、删除、`vector_status` 读写、同步路径整体限时（半写口径）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.dialects import mysql

from app.core import milvus_store as ms
from app.core.milvus_store import (
    CollectionDimMismatch,
    MilvusStore,
    MilvusStoreError,
)

DEFAULT_FIELDS = [
    {"name": "talent_profile_id", "is_primary": True, "params": {}},
    {"name": "vector", "is_primary": False, "params": {"dim": 1024}},
    {"name": "text_hash", "is_primary": False, "params": {"max_length": 64}},
]


class FakeMilvusClient:
    def __init__(self, *, exists: bool = False, fields: list[dict] | None = None) -> None:
        self.exists = exists
        self.fields = fields if fields is not None else DEFAULT_FIELDS
        self.created: list[tuple] = []
        self.loaded: list = []
        self.upserts: list[list[dict]] = []
        self.deleted: list[list[int]] = []
        self.search_calls: list[dict] = []
        self.rows: dict[int, str] = {}
        self.fail: Exception | None = None
        self.search_result = [
            {"talent_profile_id": 7, "distance": 0.9123, "entity": {"text_hash": "h" * 64}}
        ]

    def has_collection(self, name):
        return self.exists

    def create_collection(self, name, schema=None, index_params=None):
        if self.fail:
            raise self.fail
        self.created.append((name, schema, index_params))
        self.exists = True

    def load_collection(self, name=None):
        self.loaded.append(name)

    def describe_collection(self, collection_name):
        if self.fail:
            raise self.fail
        return {"collection_name": collection_name, "fields": self.fields}

    def upsert(self, collection_name, data):
        if self.fail:
            raise self.fail
        self.upserts.append(list(data))
        for row in data:
            self.rows[int(row["talent_profile_id"])] = row["text_hash"]

    def delete(self, collection_name, ids):
        if self.fail:
            raise self.fail
        self.deleted.append(list(ids))
        for item in ids:
            self.rows.pop(int(item), None)

    def search(self, **kwargs):
        if self.fail:
            raise self.fail
        self.search_calls.append(kwargs)
        return [self.search_result]  # MilvusClient.search 返回 list[list[hit]]

    def query(self, **kwargs):
        if self.fail:
            raise self.fail
        output_fields = kwargs.get("output_fields") or []
        if "count(*)" in output_fields:
            return [{"count(*)": len(self.rows)}]
        pid = int(str(kwargs.get("filter", "")).split("==")[-1].strip())
        return [{"text_hash": self.rows[pid]}] if pid in self.rows else []

    def drop_collection(self, name):
        self.exists = False


def make_store(**kwargs) -> tuple[MilvusStore, FakeMilvusClient]:
    client = kwargs.pop("client", None) or FakeMilvusClient(**kwargs)
    return MilvusStore(collection="talent_vectors", dim=1024, client=client), client


def test_ensure_creates_collection_with_production_schema():
    store, client = make_store(exists=False)
    store.ensure_collection()
    assert len(client.created) == 1
    name, schema, index_params = client.created[0]
    assert name == "talent_vectors"
    field_names = [field.name for field in schema.fields]
    assert field_names == ["talent_profile_id", "vector", "text_hash"]
    assert schema.auto_id is False
    primary = [field for field in schema.fields if field.is_primary][0]
    assert primary.name == "talent_profile_id"
    assert client.loaded == ["talent_vectors"]


def test_ensure_is_cached_until_force():
    store, client = make_store(exists=True)
    store.ensure_collection()
    store.ensure_collection()
    assert client.created == []  # 已存在，不重建
    store.ensure_collection(force=True)
    assert client.created == []  # force 只重校验，不删除已有 collection


def test_dimension_mismatch_raises_with_rebuild_hint():
    fields = [dict(item) for item in DEFAULT_FIELDS]
    fields[1]["params"] = {"dim": 512}
    store, _ = make_store(exists=True, fields=fields)
    with pytest.raises(CollectionDimMismatch) as excinfo:
        store.ensure_collection()
    assert "512" in str(excinfo.value) and "1024" in str(excinfo.value)
    assert "rebuild_vectors" in str(excinfo.value)


def test_missing_primary_key_raises():
    fields = [
        {"name": "id", "is_primary": True, "params": {}},
        dict(DEFAULT_FIELDS[1]),
        dict(DEFAULT_FIELDS[2]),
    ]
    store, _ = make_store(exists=True, fields=fields)
    with pytest.raises(CollectionDimMismatch):
        store.ensure_collection()


def test_missing_text_hash_field_raises():
    fields = [dict(DEFAULT_FIELDS[0]), dict(DEFAULT_FIELDS[1])]
    store, _ = make_store(exists=True, fields=fields)
    with pytest.raises(CollectionDimMismatch):
        store.ensure_collection()


def test_upsert_payload_and_same_pk_overwrite():
    store, client = make_store(exists=True)
    store.upsert(1001, [0.01] * 1024, "a" * 64)
    store.upsert(1001, [0.02] * 1024, "c" * 64)
    assert client.upserts[0][0] == {
        "talent_profile_id": 1001,
        "vector": [0.01] * 1024,
        "text_hash": "a" * 64,
    }
    # 同 PK 二次 upsert 覆盖（不新增行）
    assert client.rows[1001] == "c" * 64
    assert len(client.rows) == 1


def test_upsert_rejects_wrong_dimension_without_calling_milvus():
    store, client = make_store(exists=True)
    with pytest.raises(CollectionDimMismatch):
        store.upsert(1001, [0.1] * 8, "a" * 64)
    assert client.upserts == []


def test_upsert_many_returns_count_and_skips_empty():
    store, client = make_store(exists=True)
    assert store.upsert_many([]) == 0
    count = store.upsert_many([(1, [0.1] * 1024, "a" * 64), (2, [0.2] * 1024, "b" * 64)])
    assert count == 2
    assert len(client.upserts[-1]) == 2


def test_search_parses_hits_and_clamps_top_k():
    store, client = make_store(exists=True)
    hits = store.search([0.1] * 1024, top_k=999)
    assert hits[0].talent_profile_id == 7
    assert hits[0].score == pytest.approx(0.9123)
    assert hits[0].text_hash == "h" * 64
    assert client.search_calls[-1]["limit"] == 100  # 上限 100（架构 §5.3）
    assert client.search_calls[-1]["search_params"] == {"metric_type": "COSINE"}
    assert client.search_calls[-1]["consistency_level"] == "Strong"


def test_search_empty_vector_raises():
    store, client = make_store(exists=True)
    with pytest.raises(MilvusStoreError):
        store.search([])
    assert client.search_calls == []


def test_search_failure_wrapped():
    store, client = make_store(exists=True)
    client.fail = RuntimeError("milvus down")
    with pytest.raises(MilvusStoreError):
        store.search([0.1] * 1024)


def test_query_hash_and_delete():
    store, client = make_store(exists=True)
    store.upsert(1001, [0.1] * 1024, "a" * 64)
    assert store.query_hash(1001) == "a" * 64
    store.delete(1001)
    assert store.query_hash(1001) is None
    assert client.deleted == [[1001]]


def test_count_uses_count_star():
    store, client = make_store(exists=True)
    assert store.count() == 0
    client.rows = {1: "a" * 64, 2: "b" * 64}
    assert store.count() == 2


def test_collection_dim_status_semantics():
    store, client = make_store(exists=False)
    assert store.collection_dim_status() == (True, None)  # 未创建不算故障

    fields = [dict(item) for item in DEFAULT_FIELDS]
    fields[1]["params"] = {"dim": 512}
    store2, _ = make_store(exists=True, fields=fields)
    ok, detail = store2.collection_dim_status()
    assert ok is False
    assert "512" in detail


def test_collection_dim_status_swallows_errors():
    store, client = make_store(exists=True)
    client.fail = RuntimeError("describe down")
    assert store.collection_dim_status() == (True, None)


def test_drop_resets_state():
    store, client = make_store(exists=True)
    store.ensure_collection()
    store.drop()
    assert client.exists is False
    assert store._ensured is False


# --------------------------------------------------------------------------- #
# vector_status 读写
# --------------------------------------------------------------------------- #
class FakeSession:
    def __init__(self) -> None:
        self.statements: list = []

    def execute(self, statement):
        self.statements.append(statement)

    def commit(self) -> None:  # pragma: no cover - 仅接口占位
        pass


def _params(statement) -> dict:
    return statement.compile(dialect=mysql.dialect()).params


def test_set_vector_status_synced_writes_hash():
    session = FakeSession()
    ms.set_vector_status(12, "synced", text_hash="a" * 64, session=session)
    params = _params(session.statements[0])
    assert params["vector_status"] == "synced"
    assert params["profile_text_hash"] == "a" * 64


def test_set_vector_status_failed_keeps_previous_hash():
    session = FakeSession()
    ms.set_vector_status(12, "failed", text_hash="b" * 64, session=session)
    params = _params(session.statements[0])
    assert params["vector_status"] == "failed"
    assert "profile_text_hash" not in params  # 失败不覆盖「上次成功同步的文本」


# --------------------------------------------------------------------------- #
# 同步写入：整体限时（半写口径）
# --------------------------------------------------------------------------- #
def test_sync_vector_by_text_success(monkeypatch):
    store, client = make_store(exists=True)
    monkeypatch.setattr("app.core.embedding_client.embed_one", lambda text: [0.5] * 1024)
    monkeypatch.setattr(ms, "get_store", lambda: store)

    result = ms.sync_vector_by_text(1001, "画像文本", persist_status=False)
    assert result.ok is True
    assert result.vector_status == "synced"
    assert len(result.text_hash) == 64
    assert client.rows[1001] == result.text_hash


def test_sync_vector_by_text_timeout_returns_failure_not_block(monkeypatch):
    store, _ = make_store(exists=True)

    def slow_embed(text: str) -> list[float]:
        time.sleep(0.5)
        return [0.5] * 1024

    monkeypatch.setattr("app.core.embedding_client.embed_one", slow_embed)
    monkeypatch.setattr(ms, "get_store", lambda: store)

    started = time.monotonic()
    result = ms.sync_vector_by_text(1001, "画像文本", budget=0.05, persist_status=False)
    elapsed = time.monotonic() - started
    assert result.ok is False
    assert result.vector_status == "failed"
    assert "超时" in result.error
    assert elapsed < 0.4  # 1.5s 预算语义：超预算立即失败，不阻塞请求


def test_sync_vector_by_text_embedding_error(monkeypatch):
    store, _ = make_store(exists=True)

    def boom(text: str) -> list[float]:
        raise RuntimeError("embedding down")

    monkeypatch.setattr("app.core.embedding_client.embed_one", boom)
    monkeypatch.setattr(ms, "get_store", lambda: store)
    result = ms.sync_vector_by_text(1001, "画像文本", budget=1.0, persist_status=False)
    assert result.ok is False
    assert "embedding down" in result.error


def test_delete_profile_vector_never_raises(monkeypatch):
    store, _ = make_store(exists=True)
    monkeypatch.setattr(ms, "get_store", lambda: store)
    assert ms.delete_profile_vector(1) is True

    class BrokenStore:
        def delete(self, profile_id):
            raise MilvusStoreError("down")

    monkeypatch.setattr(ms, "get_store", lambda: BrokenStore())
    assert ms.delete_profile_vector(1) is False  # 软删除不阻塞
