"""D21 Embedding 系统化单元测试（T-D-SELF-06）——离线可验证，不依赖真实模型/网络。

验收对照（清单 T-D-SELF-06「D21 本地 Embedding 系统化验收」）：
- 批量/单条：>EMBEDDING_BATCH_SIZE 自动分片，返回与输入同序；空输入/空串明确拒绝；
- 维度强校验：返回维度 != EMBEDDING_DIM → EmbeddingDimensionError，不写入下游；
- 条数不匹配：返回条数 != 请求条数 → EmbeddingError；
- 模式切换：api/local 即时生效；local 缺路径/缺包/加载失败明确报错；
- 失败降级：API 异常统一转 EmbeddingError，不静默返回零向量；
- 凭据隔离：api_key 不进日志；
- 查询缓存：embed_query 命中缓存不产生第二次真实调用；TTL=0 时停用缓存；
- 特殊字符/超长输入：原样透传给供应商，不被截断或篡改；
- 被动探活：成功/失败都刷新 embedding_alive（缓存命中不算真实调用）。

运行：pytest tests/D/test_embedding_unit.py -q
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

import app.models  # noqa: F401 先行完成全量模型注册（app.config → models 链）
from app.core import embedding_client
from app.core.embedding_client import (
    EMBEDDING_BATCH_SIZE,
    EmbeddingClient,
    EmbeddingDimensionError,
    EmbeddingError,
    clear_query_cache,
)

DIM = 4


class _Vec:
    def __init__(self, vec):
        self.embedding = vec


class _Resp:
    def __init__(self, vectors):
        self.data = [_Vec(v) for v in vectors]


def _fake_client(vectors=None, *, raises=None, calls=None):
    """构造假 OpenAI 客户端；vectors 为定长向量或按批生成的可调用对象。"""

    class FakeEmbeddings:
        def create(self, model, input):
            if calls is not None:
                calls.append({"model": model, "input": list(input)})
            if raises is not None:
                raise raises
            batch = list(input)
            if callable(vectors):
                return _Resp(vectors(batch))
            return _Resp((vectors or [[0.1] * DIM for _ in batch])[: len(batch)])

    class FakeClient:
        embeddings = FakeEmbeddings()

    return FakeClient()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每例隔离：清缓存 + 复位全局探活状态。"""
    clear_query_cache()
    monkeypatch.setattr("app.config.settings.embedding_mode", "api")
    monkeypatch.setattr("app.config.settings.embedding_dim", DIM)
    monkeypatch.setattr("app.config.settings.embedding_query_cache_ttl", 300)
    yield
    clear_query_cache()


def _client(client=None, **kw) -> EmbeddingClient:
    return EmbeddingClient(client=client, **kw)


# ---- 批量 / 单条 / 空输入 ----

def test_embed_texts_splits_into_batches_and_preserves_order(monkeypatch):
    """超过单批上限按 EMBEDDING_BATCH_SIZE 分片，返回与输入同序。"""
    calls: list = []

    def gen(batch):
        # 每批返回与批次等长、值等于全局序号的向量，便于核对顺序
        # 注意：calls 在调用前已 append 当前批，故去掉最后一条再求基址
        base = sum(len(c["input"]) for c in calls[:-1])
        return [[float(base + i)] * DIM for i in range(len(batch))]

    client = _client(_fake_client(gen, calls=calls))
    texts = [f"t{i}" for i in range(EMBEDDING_BATCH_SIZE * 2 + 3)]
    vecs = client.embed_texts(texts)

    assert len(vecs) == len(texts)
    assert len(calls) == 3, f"应分 3 批（{EMBEDDING_BATCH_SIZE}+{EMBEDDING_BATCH_SIZE}+3），实得 {len(calls)}"
    assert [len(c["input"]) for c in calls] == [EMBEDDING_BATCH_SIZE, EMBEDDING_BATCH_SIZE, 3]
    # 顺序：第 i 条文本的向量首元素 == i
    assert [v[0] for v in vecs] == [float(i) for i in range(len(texts))]


def test_embed_texts_empty_input_returns_empty():
    assert _client(_fake_client()).embed_texts([]) == []


def test_embed_texts_blank_text_rejected():
    client = _client(_fake_client())
    with pytest.raises(EmbeddingError, match="空文本"):
        client.embed_texts(["有效文本", "   "])


def test_embed_one_and_query_reject_empty():
    client = _client(_fake_client())
    with pytest.raises(EmbeddingError, match="空"):
        client.embed_one("  ")
    with pytest.raises(EmbeddingError, match="空"):
        client.embed_query("")


def test_special_chars_and_long_text_passed_through_unchanged():
    """引号/换行/emoji/CJK 与超长文本原样透传，不被截断或改写。"""
    calls: list = []
    weird = '引号"\'\n换行\t制表 emoji🚀 中文 '
    long_text = "长" * 5000
    client = _client(_fake_client(calls=calls))
    client.embed_texts([weird, long_text])
    assert calls[0]["input"] == [weird, long_text], "文本必须原样透传"


# ---- 维度 / 条数强校验 ----

def test_dimension_mismatch_raises_and_does_not_return():
    bad = _fake_client([[0.1] * (DIM + 2) for _ in range(1)])
    client = _client(bad)
    with pytest.raises(EmbeddingDimensionError, match="EMBEDDING_DIM"):
        client.embed_texts(["x"])


def test_count_mismatch_raises():
    """返回条数少于请求：明确报错，不静默补齐。"""
    short = _fake_client([[0.1] * DIM])  # 只回 1 条
    client = _client(short)
    with pytest.raises(EmbeddingError, match="条数不匹配"):
        client.embed_texts(["a", "b", "c"])


def test_partial_batch_dimension_mismatch_raises():
    """分片后某一批维度不符：整批失败，不返回半截结果。"""
    calls: list = []

    def gen(batch):
        calls.append(list(batch))
        if len(calls) == 2:
            return [[0.1] * (DIM + 1) for _ in batch]
        return [[0.1] * DIM for _ in batch]

    client = _client(_fake_client(gen))
    with pytest.raises(EmbeddingDimensionError):
        client.embed_texts([f"t{i}" for i in range(EMBEDDING_BATCH_SIZE + 1)])


# ---- 失败降级 / 凭据隔离 / 探活 ----

def test_api_failure_raises_not_silent_zero(monkeypatch):
    """供应商异常统一转 EmbeddingError，绝不返回零向量冒充成功。"""
    from openai import APIStatusError

    def boom(model, input):
        raise APIStatusError("provider exploded", response=None, body=None)  # type: ignore[arg-type]

    class FakeEmbeddings:
        create = boom

    class FakeClient:
        embeddings = FakeEmbeddings()

    client = _client(FakeClient())
    with pytest.raises(EmbeddingError, match="Embedding 调用失败"):
        client.embed_texts(["x"])


def test_api_key_never_logged_on_failure(monkeypatch, caplog):
    """凭据隔离：api_key 不进日志（成功/失败路径均无）。"""
    secret = "sk-SECRET-abc123"
    client = EmbeddingClient(api_key=secret, base_url="http://127.0.0.1:9")

    class FakeEmbeddings:
        def create(self, model, input):
            raise RuntimeError("provider exploded")

    class FakeClient:
        embeddings = FakeEmbeddings()

    monkeypatch.setattr(client, "_client", FakeClient())
    with caplog.at_level(logging.DEBUG, logger="app.core.embedding_client"):
        with pytest.raises(EmbeddingError):
            client.embed_texts(["x"])
        with pytest.raises(EmbeddingError):
            client.embed_query("查询")
    assert secret not in caplog.text, "api_key 不得出现在日志中"


def test_probe_refreshed_on_success_and_failure(monkeypatch):
    """被动探活：真实调用成功 → True；失败 → False；缓存命中不刷新。"""
    from app.api.health import get_embedding_alive

    ok = _client(_fake_client([[0.1] * DIM]))
    ok.embed_texts(["x"])
    assert get_embedding_alive() is True

    bad = _client(_fake_client(raises=RuntimeError("boom")))
    with pytest.raises(EmbeddingError):
        bad.embed_texts(["y"])
    assert get_embedding_alive() is False


# ---- 查询缓存 ----

def test_query_cache_hit_avoids_second_real_call():
    calls: list = []
    client = _client(_fake_client(calls=calls))
    a = client.embed_query("向量数据库")
    b = client.embed_query("向量数据库")
    assert a == b
    assert len(calls) == 1, "缓存命中不得产生第二次真实调用"


def test_query_cache_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setattr("app.config.settings.embedding_query_cache_ttl", 0)
    calls: list = []
    client = _client(_fake_client(calls=calls))
    client.embed_query("向量数据库")
    client.embed_query("向量数据库")
    assert len(calls) == 2, "TTL=0 时停用缓存，两次都应真实调用"


def test_embed_one_bypasses_cache():
    """后台单条不走查询缓存（与检索查询口径分离）。"""
    calls: list = []
    client = _client(_fake_client(calls=calls))
    client.embed_one("同文本")
    client.embed_one("同文本")
    assert len(calls) == 2


# ---- 模式切换 / local 明确报错 ----

def test_local_mode_missing_path_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "")
    client = EmbeddingClient()
    with pytest.raises(EmbeddingError, match="EMBEDDING_LOCAL_MODEL_PATH"):
        client.embed_texts(["x"])


def test_local_mode_missing_package_raises_install_hint(monkeypatch):
    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "E:/fake-model")
    import builtins

    real_import = builtins.__import__

    def no_st(name, *a, **k):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_st)
    client = EmbeddingClient()
    with pytest.raises(EmbeddingError, match="sentence-transformers"):
        client.embed_texts(["x"])


def test_local_mode_model_load_failure_raises(monkeypatch):
    """模型目录不存在/损坏：明确报错，不静默零向量。"""

    class FakeST:
        def __init__(self, *a, **k):
            raise RuntimeError("model files missing")

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "E:/broken-model")
    client = EmbeddingClient()
    with pytest.raises(EmbeddingError, match="模型加载失败"):
        client.embed_texts(["x"])


def test_local_mode_dimension_mismatch_raises(monkeypatch):
    """本地推理同样受 EMBEDDING_DIM 强校验（与 API 模式同口径）。"""

    class WrongDim:
        def encode(self, batch, **k):
            return [[0.1] * (DIM + 3) for _ in batch]

    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "E:/fake-model")
    client = EmbeddingClient()
    monkeypatch.setattr(client, "_ensure_local_model", lambda: WrongDim())
    with pytest.raises(EmbeddingDimensionError, match="Milvus collection"):
        client.embed_texts(["x"])
