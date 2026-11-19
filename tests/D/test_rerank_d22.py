"""D22 重排双模式 + Embedding 本地模式用例（离线可验证；本地模型真实冒烟独立标记）。

验收对照（执行表 D22 / G05 本地推理交付项）：
- 三模式门控：off（默认）→ rerank() 返回 None（召回序原样，行为不变）；api/local
  缺配置明确报错（RerankError），不静默假装重排过；
- api 模式：HTTP 端点适配（httpx 假传输）、results 形状校验、按分降序；
- local 模式：未装包/未配路径明确报错含安装指引；真实模型推理标记 slow（本机有
  E:/AI_sentence_transformers 模型时跑，验证 1024 维一致性）；
- embedding local 分流：mode=local 走进程内推理（monkeypatch 假模型），维度校验与
  /health 探活同口径。

运行：pytest tests/D/test_rerank_d22.py -v（默认跳过 slow；--run-slow 全跑）。
"""

from __future__ import annotations

import pytest

import app.models  # noqa: F401 先行完成全量模型注册（app.config → models 链）
from app.core import embedding_client, rerank_client
from app.core.rerank_client import (
    ApiReranker,
    LocalReranker,
    RerankCandidate,
    RerankError,
    RerankOutcome,
    rank_candidates,
    rerank,
    rerank_texts,
    reset_reranker,
)

DOCS = ["Python 后端工程师，5 年 FastAPI 经验", "前端工程师，React", "算法工程师，Milvus 向量检索"]


@pytest.fixture(autouse=True)
def _env_guard(monkeypatch):
    """每例隔离：清单例 + 复位 rerank/embedding 模式为 off/api。"""
    reset_reranker()
    monkeypatch.setattr("app.config.settings.rerank_mode", "off")
    monkeypatch.setattr("app.config.settings.embedding_mode", "api")
    yield
    reset_reranker()


# ---- 门控 ----

def test_off_mode_returns_none_by_default(monkeypatch):
    monkeypatch.setattr("app.config.settings.rerank_mode", "off")
    assert rerank("q", DOCS) is None, "off（默认）不重排，召回序原样返回"
    assert rerank_texts("q", DOCS) is None


def test_api_mode_missing_config_raises_clearly(monkeypatch):
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", None)
    monkeypatch.setattr("app.config.settings.embedding_base_url", None)
    monkeypatch.setattr("app.config.settings.llm_base_url", "")
    with pytest.raises(RerankError, match="RERANK_BASE_URL"):
        rerank("q", DOCS)


def test_local_mode_missing_path_or_package_raises_clearly(monkeypatch):
    """local 模式缺配置/缺包都须明确报错（报错顺序取决于包是否已装，两者均合法）。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "local")
    monkeypatch.setattr("app.config.settings.rerank_local_model_path", "")
    with pytest.raises(RerankError) as ei:
        rerank("q", DOCS)
    msg = str(ei.value)
    assert "RERANK_LOCAL_MODEL_PATH" in msg or "sentence-transformers" in msg, (
        f"缺路径或缺包必须二有一，得到：{msg}"
    )


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.rerank_mode", "bogus")
    with pytest.raises(RerankError, match="RERANK_MODE 非法"):
        rerank("q", DOCS)


def test_empty_inputs(monkeypatch):
    assert rerank("q", []) is None  # 空 docs 直接跳过（不启执行器）
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")
    with pytest.raises(RerankError, match="query 为空"):
        rerank("  ", DOCS)
    with pytest.raises(RerankError, match="空文本"):
        rerank("q", ["a", " "])


# ---- rank_candidates 接入框架（T-D-SELF-02）----

def _cands(*texts: str) -> list[RerankCandidate]:
    return [RerankCandidate(id=f"id{i}", text=t, score=float(len(texts) - i))
            for i, t in enumerate(texts)]


def test_rank_candidates_off_mode_returns_original(monkeypatch):
    """off 模式：原序返回，reranked=False，零模型调用。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "off")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("off 不应调用 HTTP"))
    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands)
    assert out.reranked is False and out.error is None
    assert [c.id for c in out.candidates] == ["id0", "id1", "id2"]


def test_rank_candidates_empty_and_single(monkeypatch):
    """空候选与单候选：直接原序返回，不触发重排。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    assert rank_candidates("q", []).candidates == []
    one = _cands("only")
    out = rank_candidates("q", one)
    assert out.reranked is False and [c.id for c in out.candidates] == ["id0"]


def test_rank_candidates_success_sorted(monkeypatch):
    """成功重排：按分降序，候选集合不变。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.7},
            ]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResp())
    reset_reranker()

    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands)
    assert out.reranked is True and out.error is None
    assert [c.id for c in out.candidates] == ["id2", "id1", "id0"]
    assert [c.rerank_score for c in out.candidates] == [0.9, 0.7, 0.5]
    assert {c.id for c in out.candidates} == {c.id for c in cands}  # 集合不变


def test_rank_candidates_failure_falls_back_to_original(monkeypatch):
    """重排失败：原序返回，reranked=False，error 说明原因（不静默重排）。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")

    class BadResp:
        status_code = 500
        text = "boom"
        def json(self):
            return {}

    monkeypatch.setattr("httpx.post", lambda *a, **k: BadResp())
    reset_reranker()

    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands)
    assert out.reranked is False
    assert [c.id for c in out.candidates] == ["id0", "id1", "id2"]
    assert out.error is not None and "HTTP 500" in out.error


def test_rank_candidates_inconsistent_count_falls_back(monkeypatch):
    """重排结果数量与候选不一致：降级为原序。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")

    class PartialResp:
        status_code = 200
        text = ""
        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9}]}  # 只返回 1 条

    monkeypatch.setattr("httpx.post", lambda *a, **k: PartialResp())
    reset_reranker()

    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands)
    assert out.reranked is False
    assert [c.id for c in out.candidates] == ["id0", "id1", "id2"]
    assert out.error is not None and "数量" in out.error


def test_rank_candidates_top_n_truncation(monkeypatch):
    """top_n 截断在重排后应用。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.7},
            ]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResp())
    reset_reranker()

    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands, top_n=2)
    assert out.reranked is True
    assert [c.id for c in out.candidates] == ["id2", "id1"]


def test_rank_candidates_explicit_mode_overrides_settings(monkeypatch):
    """显式 mode 参数覆盖 settings。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    cands = _cands("a", "b", "c")
    out = rank_candidates("q", cands, mode="off")
    assert out.reranked is False
    assert [c.id for c in out.candidates] == ["id0", "id1", "id2"]


# ---- 候选集合完整性校验（REQ-MASTER-20261111-233731-D）----

def _stub_rerank(monkeypatch, results):
    """替身重排器：直接返回指定 [(index, score)]，绕过 API 层形状校验。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr(rerank_client, "rerank", lambda q, docs: results)


def _assert_original_order(out, cands, error_keyword):
    assert out.reranked is False, "异常结果必须明确降级，不得静默重排"
    assert [c.id for c in out.candidates] == [c.id for c in cands], "降级保持原序原集合"
    assert out.error is not None and error_keyword in out.error


def test_rank_candidates_duplicate_index_falls_back(monkeypatch):
    """本轮复现：候选 A/B，替身返回 [(0,0.9),(0,0.8)] → 原序 A/B 且明确降级。"""
    cands = _cands("a", "b")
    _stub_rerank(monkeypatch, [(0, 0.9), (0, 0.8)])
    out = rank_candidates("q", cands)
    _assert_original_order(out, cands, "重复")


def test_rank_candidates_missing_index_falls_back(monkeypatch):
    """遗漏候选：数量不足或索引未完整覆盖 0..n-1 → 降级。"""
    cands = _cands("a", "b", "c")
    _stub_rerank(monkeypatch, [(0, 0.9), (1, 0.8)])
    _assert_original_order(rank_candidates("q", cands), cands, "数量")
    # 数量相等但覆盖不全（重复占位）同样降级
    _stub_rerank(monkeypatch, [(0, 0.9), (0, 0.8), (1, 0.7)])
    _assert_original_order(rank_candidates("q", cands), cands, "重复")


def test_rank_candidates_out_of_range_index_falls_back(monkeypatch):
    cands = _cands("a", "b")
    _stub_rerank(monkeypatch, [(0, 0.9), (2, 0.8)])
    _assert_original_order(rank_candidates("q", cands), cands, "越界")
    _stub_rerank(monkeypatch, [(0, 0.9), (-1, 0.8)])
    _assert_original_order(rank_candidates("q", cands), cands, "越界")


def test_rank_candidates_non_integer_index_falls_back(monkeypatch):
    cands = _cands("a", "b")
    for bad_idx in ("1", 1.0, True, None):
        _stub_rerank(monkeypatch, [(0, 0.9), (bad_idx, 0.8)])
        _assert_original_order(rank_candidates("q", cands), cands, "非整数")


def test_rank_candidates_non_finite_score_falls_back(monkeypatch):
    cands = _cands("a", "b")
    for bad_score in (float("nan"), float("inf"), float("-inf")):
        _stub_rerank(monkeypatch, [(0, bad_score), (1, 0.8)])
        _assert_original_order(rank_candidates("q", cands), cands, "有限数")


def test_rank_candidates_malformed_element_falls_back(monkeypatch):
    """元素形状非法（非二元组）→ 降级而非崩溃。"""
    cands = _cands("a", "b")
    _stub_rerank(monkeypatch, [(0, 0.9, 0.5), (1, 0.8)])
    _assert_original_order(rank_candidates("q", cands), cands, "形状")


def test_rank_candidates_valid_reverse_order(monkeypatch):
    """合法完整集合的逆序返回：按分降序重排，集合不变。"""
    cands = _cands("a", "b")
    _stub_rerank(monkeypatch, [(1, 0.9), (0, 0.8)])
    out = rank_candidates("q", cands)
    assert out.reranked is True and out.error is None
    assert [c.id for c in out.candidates] == ["id1", "id0"]
    assert [c.rerank_score for c in out.candidates] == [0.9, 0.8]


def test_rank_candidates_stable_sort_on_equal_scores(monkeypatch):
    """同分稳定排序：保持返回结果中的相对顺序。"""
    cands = _cands("a", "b", "c")
    _stub_rerank(monkeypatch, [(1, 0.5), (0, 0.5), (2, 0.9)])
    out = rank_candidates("q", cands)
    assert out.reranked is True
    assert [c.id for c in out.candidates] == ["id2", "id1", "id0"]


def test_rank_candidates_top_n_after_full_validation(monkeypatch):
    """合法集合先按分排序再按 top_n 截断；非法集合不受 top_n 掩盖。"""
    cands = _cands("a", "b", "c")
    _stub_rerank(monkeypatch, [(2, 0.9), (0, 0.5), (1, 0.7)])
    out = rank_candidates("q", cands, top_n=2)
    assert out.reranked is True
    assert [c.id for c in out.candidates] == ["id2", "id1"]
    # 重复索引即使 top_n 小于候选数也必须降级（校验在截断前）
    _stub_rerank(monkeypatch, [(0, 0.9), (0, 0.8), (1, 0.7)])
    out = rank_candidates("q", cands, top_n=1)
    _assert_original_order(out, cands, "重复")


def test_rank_candidates_duplicate_business_ids_are_legal(monkeypatch):
    """重复业务 id ≠ 索引重复：合法完整集合正常重排，不按 id 合并候选。"""
    cands = [RerankCandidate(id="same", text="a", score=1.0),
             RerankCandidate(id="same", text="b", score=0.5)]
    _stub_rerank(monkeypatch, [(1, 0.9), (0, 0.8)])
    out = rank_candidates("q", cands)
    assert out.reranked is True and out.error is None
    assert [c.text for c in out.candidates] == ["b", "a"]


def test_rank_candidates_duplicate_index_via_api_layer(monkeypatch):
    """端到端：API 层返回重复索引（形状合法）→ 框架层降级原序。"""
    monkeypatch.setattr("app.config.settings.rerank_mode", "api")
    monkeypatch.setattr("app.config.settings.rerank_base_url", "http://127.0.0.1:9")

    class DupResp:
        status_code = 200
        text = ""
        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9},
                                {"index": 0, "relevance_score": 0.8}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: DupResp())
    reset_reranker()
    cands = _cands("a", "b")
    out = rank_candidates("q", cands)
    _assert_original_order(out, cands, "重复")


# ---- API 模式（httpx 假传输）----

def test_api_reranker_parses_results_and_sorts(monkeypatch):
    calls: list[dict] = []

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.7},
            ]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        return FakeResp()

    monkeypatch.setattr("httpx.post", fake_post)
    rk = ApiReranker(base_url="http://rerank.local/v1", api_key="sk-t", model="gte-rerank", timeout=5)
    out = rk.rerank("向量检索", DOCS)
    assert [i for i, _ in out] == [2, 1, 0], "按分降序"
    assert out[0][1] == 0.9
    body = calls[0]["body"]
    assert body["model"] == "gte-rerank" and body["query"] == "向量检索"
    assert body["documents"] == list(DOCS)
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-t"
    assert calls[0]["url"] == "http://rerank.local/v1/rerank"


def test_api_reranker_error_shapes(monkeypatch):
    class BadResp:
        status_code = 500
        text = "boom"
        def json(self):
            return {}

    monkeypatch.setattr("httpx.post", lambda *a, **k: BadResp())
    rk = ApiReranker(base_url="http://x/v1", api_key="k", model="m", timeout=1)
    with pytest.raises(RerankError, match="HTTP 500"):
        rk.rerank("q", DOCS)

    class NoResults:
        status_code = 200
        text = ""
        def json(self):
            return {"data": "unexpected"}

    monkeypatch.setattr("httpx.post", lambda *a, **k: NoResults())
    with pytest.raises(RerankError, match="results"):
        rk.rerank("q", DOCS)


# ---- 本地模式（无真实模型：报错路径；真实推理见 slow）----

def test_local_without_package_raises_install_hint(monkeypatch):
    monkeypatch.setattr("app.config.settings.rerank_mode", "local")
    monkeypatch.setattr("app.config.settings.rerank_local_model_path", "E:/nonexistent-model")
    import builtins
    real_import = builtins.__import__

    def no_st(name, *a, **k):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_st)
    with pytest.raises(RerankError, match="pip install sentence-transformers"):
        rerank("q", DOCS)


@pytest.mark.slow
def test_local_real_model_smoke(monkeypatch):
    """真实本地模型冒烟：本机 E:/AI_sentence_transformers 的 bge-reranker-base。"""
    import os
    path = r"E:/AI_sentence_transformers/models/BAAI--bge-reranker-base"
    if not os.path.isdir(path):
        pytest.skip(f"本机无本地重排模型：{path}")
    pytest.importorskip("sentence_transformers")
    monkeypatch.setattr("app.config.settings.rerank_mode", "local")
    monkeypatch.setattr("app.config.settings.rerank_local_model_path", path)
    monkeypatch.setattr("app.config.settings.rerank_local_device", "cpu")
    out = rerank("向量数据库选型", DOCS)
    assert out is not None and len(out) == 3
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True), "按分降序"
    assert all(0.0 <= s <= 1.0 for s in scores), "CrossEncoder sigmoid 分数在 0-1"


# ---- Embedding local 分流 ----

def test_embedding_local_mode_uses_local_inference(monkeypatch):
    captured: list[list[str]] = []

    class FakeSTModel:
        def encode(self, batch, batch_size=0, normalize_embeddings=False, show_progress_bar=False):
            captured.append(list(batch))
            return [[0.1] * 4 for _ in batch]  # 4 维假向量

    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "E:/fake-model")
    monkeypatch.setattr("app.config.settings.embedding_dim", 4)

    client = embedding_client.EmbeddingClient()
    monkeypatch.setattr(client, "_ensure_local_model", lambda: FakeSTModel())
    vecs = client.embed_texts(["你好", "世界"])
    assert vecs == [[0.1] * 4, [0.1] * 4]
    assert captured == [["你好", "世界"]]
    # 本地推理同样刷新探活
    from app.api.health import get_embedding_alive
    assert get_embedding_alive() is True


def test_embedding_local_dim_mismatch_raises(monkeypatch):
    class WrongDimModel:
        def encode(self, batch, **k):
            return [[0.1] * 8 for _ in batch]  # 8 维 ≠ 配置 4 维

    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", "E:/fake-model")
    monkeypatch.setattr("app.config.settings.embedding_dim", 4)
    client = embedding_client.EmbeddingClient()
    monkeypatch.setattr(client, "_ensure_local_model", lambda: WrongDimModel())
    with pytest.raises(embedding_client.EmbeddingDimensionError, match="Milvus collection"):
        client.embed_texts(["你好"])


@pytest.mark.slow
def test_embedding_local_real_model_smoke(monkeypatch):
    """真实本地 embedding 冒烟：bge-large-zh-v1.5（1024 维 = Milvus 冻结维度）。"""
    import os
    path = r"E:/AI_sentence_transformers/models/BAAI--bge-large-zh-v1.5/snapshots/master"
    if not os.path.isdir(path):
        pytest.skip(f"本机无本地 embedding 模型：{path}")
    pytest.importorskip("sentence_transformers")
    monkeypatch.setattr("app.config.settings.embedding_mode", "local")
    monkeypatch.setattr("app.config.settings.embedding_local_model_path", path)
    monkeypatch.setattr("app.config.settings.embedding_local_device", "cpu")
    monkeypatch.setattr("app.config.settings.embedding_dim", 1024)
    client = embedding_client.EmbeddingClient()
    vecs = client.embed_texts(["Python 后端工程师，5 年 FastAPI 经验", "前端工程师，React"])
    assert len(vecs) == 2 and all(len(v) == 1024 for v in vecs), "维度必须 == EMBEDDING_DIM=1024"
    # 同文本重复推理结果一致（确定性，温度为 0 语义）
    again = client.embed_texts(["Python 后端工程师，5 年 FastAPI 经验"])
    assert max(abs(a - b) for a, b in zip(vecs[0], again[0])) < 1e-6
    # 查询缓存复用（不再真实推理）
    q1 = client.embed_query("向量数据库")
    q2 = client.embed_query("向量数据库")
    assert q1 == q2
    from app.api.health import get_embedding_alive
    assert get_embedding_alive() is True


def test_embedding_api_mode_untouched(monkeypatch):
    """API 模式行为零变化：仍走 OpenAI HTTP 路径。"""
    monkeypatch.setattr("app.config.settings.embedding_mode", "api")
    client = embedding_client.EmbeddingClient(api_key="k", base_url="http://127.0.0.1:9")

    class FakeResp:
        data = [type("D", (), {"embedding": [0.2] * 4})()]
    class FakeEmbeddings:
        def create(self, model, input):
            assert model == "text-embedding-v3"
            return FakeResp()
    class FakeClient:
        embeddings = FakeEmbeddings()

    monkeypatch.setattr(client, "_client", FakeClient())
    monkeypatch.setattr(client, "dim", 4)
    vecs = client.embed_texts(["测试"])
    assert vecs == [[0.2] * 4]
    from app.api.health import get_embedding_alive
    assert get_embedding_alive() is True
