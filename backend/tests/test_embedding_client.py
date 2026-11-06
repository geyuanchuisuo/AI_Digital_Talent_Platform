"""L1 单元测试 · embedding_client（全部打桩，不触外部服务）。

对齐 `docs/运行配置.md` v1.3 与 `docs/架构设计.md` v1.4 §5.3：
批量分片、维度校验、查询短期缓存、`embedding_alive` 被动记录（只有真实调用才刷新）。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.api import health as health_api
from app.core import embedding_client as ec
from app.core.embedding_client import (
    EMBEDDING_BATCH_SIZE,
    EmbeddingClient,
    EmbeddingDimensionError,
    EmbeddingError,
)


class FakeEmbeddings:
    def __init__(self, dim: int = 1024, error: Exception | None = None) -> None:
        self.dim = dim
        self.error = error
        self.calls: list[list[str]] = []

    def create(self, *, model: str, input: list[str]):  # noqa: A002 - 对齐 SDK 形参名
        self.calls.append(list(input))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * self.dim) for _ in input])


class FakeOpenAI:
    def __init__(self, embeddings: FakeEmbeddings) -> None:
        self.embeddings = embeddings


@pytest.fixture(autouse=True)
def reset_state():
    ec.clear_query_cache()
    health_api._embedding_alive = None
    yield
    ec.clear_query_cache()
    health_api._embedding_alive = None


def make_client(dim: int = 1024, error: Exception | None = None):
    embeddings = FakeEmbeddings(dim=dim, error=error)
    client = EmbeddingClient(
        base_url="http://fake.local/v1", api_key="k", model="text-embedding-v3", dim=1024,
        client=FakeOpenAI(embeddings),
    )
    return client, embeddings


def test_embed_texts_batches_by_limit():
    client, embeddings = make_client()
    vectors = client.embed_texts([f"文本{i}" for i in range(EMBEDDING_BATCH_SIZE + 1)])
    assert len(vectors) == EMBEDDING_BATCH_SIZE + 1
    assert [len(call) for call in embeddings.calls] == [EMBEDDING_BATCH_SIZE, 1]
    assert all(len(vector) == 1024 for vector in vectors)


def test_embed_texts_empty_input_returns_empty_list_without_call():
    client, embeddings = make_client()
    assert client.embed_texts([]) == []
    assert embeddings.calls == []


def test_embed_blank_text_rejected():
    client, embeddings = make_client()
    with pytest.raises(EmbeddingError):
        client.embed_texts(["  "])
    assert embeddings.calls == []


def test_dimension_mismatch_raises_and_marks_alive_false():
    client, _ = make_client(dim=512)
    with pytest.raises(EmbeddingDimensionError):
        client.embed_one("简历文本")
    assert health_api.get_embedding_alive() is False


def test_success_marks_alive_true():
    client, _ = make_client()
    client.embed_one("简历文本")
    assert health_api.get_embedding_alive() is True


def test_query_cache_hits_without_second_request():
    client, embeddings = make_client()
    first = client.embed_query("Python 后端 上海")
    second = client.embed_query("Python 后端 上海")
    assert first == second
    assert len(embeddings.calls) == 1  # 第二次命中缓存，没有真实调用


def test_query_cache_miss_after_clear():
    client, embeddings = make_client()
    client.embed_query("同一查询")
    ec.clear_query_cache()
    client.embed_query("同一查询")
    assert len(embeddings.calls) == 2


def test_query_cache_disabled_when_ttl_zero(monkeypatch):
    monkeypatch.setattr(ec.settings, "embedding_query_cache_ttl", 0)
    client, embeddings = make_client()
    client.embed_query("查询")
    client.embed_query("查询")
    assert len(embeddings.calls) == 2


def test_cache_hit_does_not_refresh_alive_flag():
    client, _ = make_client()
    client.embed_query("缓存测试")
    health_api._embedding_alive = None  # 模拟进程内被重置
    client.embed_query("缓存测试")  # 命中缓存，不算真实调用
    assert health_api.get_embedding_alive() is None


def test_api_status_error_wrapped_and_alive_false():
    request = httpx.Request("POST", "http://fake.local/v1/embeddings")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate"}})
    client, _ = make_client(error=openai.RateLimitError("rate limited", response=response, body=None))
    with pytest.raises(EmbeddingError) as excinfo:
        client.embed_one("限频测试")
    assert "429" in str(excinfo.value)
    assert health_api.get_embedding_alive() is False


def test_unexpected_error_wrapped():
    client, _ = make_client(error=RuntimeError("boom"))
    with pytest.raises(EmbeddingError) as excinfo:
        client.embed_one("异常测试")
    assert "RuntimeError" in str(excinfo.value)


def test_returned_count_mismatch_raises():
    class ShortEmbeddings(FakeEmbeddings):
        def create(self, *, model: str, input: list[str]):  # noqa: A002
            self.calls.append(list(input))
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 1024)])

    embeddings = ShortEmbeddings()
    client = EmbeddingClient(base_url="x", api_key="k", dim=1024, client=FakeOpenAI(embeddings))
    with pytest.raises(EmbeddingError):
        client.embed_texts(["a", "b"])
