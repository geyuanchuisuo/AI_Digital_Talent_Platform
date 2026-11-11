"""Embedding 适配层（T-05）· 全项目唯一 embedding 调用入口。

实现口径：
- `docs/运行配置.md` v1.3：`EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY`（缺省回退 LLM）、
  `EMBEDDING_MODEL=text-embedding-v3`、`EMBEDDING_DIM=1024`、
  `EMBEDDING_QUERY_CACHE_TTL=300`、`EMBEDDING_TIMEOUT=10`、`VECTOR_SYNC_TIMEOUT=1.5`
- `docs/架构设计.md` v1.4 §5.3：语义检索「相同查询短期缓存」；
  `/health` 的 `embedding_alive` **只由真实调用结果被动刷新**，health 自身不得主动调用
- 决策 D-L：Embedding 走阿里百炼 `text-embedding-v3`（OpenAI 兼容），与 LLM 不同供应商

两个超时的分工（不要混用）：
| 场景 | 超时 | 入口 |
|---|---|---|
| 后台批量（解析/重建）与语义检索 | `EMBEDDING_TIMEOUT`（10s） | `embed_texts` / `embed_query` |
| 同步写入路径（PATCH/merge/restore/revector）的「embedding + upsert」总预算 | `VECTOR_SYNC_TIMEOUT`（1.5s） | `milvus_store.sync_vector_by_text` 内整体限时 |

⚠️ 公共部分（开发日志 §1.3），修改前需用户确认。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Sequence

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# 百炼 text-embedding-v3 单次请求 input 条数上限 10；超出按批切分，避免 400
EMBEDDING_BATCH_SIZE = 10
CACHE_MAX_ENTRIES = 512
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class EmbeddingError(RuntimeError):
    """embedding 调用失败的统一异常；message 可直接落 `vector_status=failed` 的原因。"""


class EmbeddingDimensionError(EmbeddingError):
    """返回维度与 `EMBEDDING_DIM` 不一致——继续写入会让 Milvus collection 不匹配。"""


# --------------------------------------------------------------------------- #
# 查询短期缓存（§5.3「相同查询短期缓存」）
# --------------------------------------------------------------------------- #
_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{settings.embedding_model}\x00{text}".encode("utf-8")).hexdigest()


def _cache_get(text: str) -> list[float] | None:
    ttl = max(0, int(settings.embedding_query_cache_ttl))
    if ttl == 0:
        return None
    key = _cache_key(text)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        stored_at, vector = hit
        if now - stored_at > ttl:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return list(vector)


def _cache_put(text: str, vector: Sequence[float]) -> None:
    ttl = max(0, int(settings.embedding_query_cache_ttl))
    if ttl == 0:
        return
    key = _cache_key(text)
    with _cache_lock:
        _cache[key] = (time.monotonic(), list(vector))
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_query_cache() -> None:
    """测试/排障用：清空查询向量缓存。"""
    with _cache_lock:
        _cache.clear()


def _record_alive(alive: bool) -> None:
    """被动探活：把最近一次**真实**调用结果写给 /health（缓存命中不算真实调用）。

    延迟导入避免 `core` ↔ `api` 的模块级循环依赖（health 不反向依赖本模块）。
    """
    try:
        from app.api.health import set_embedding_alive

        set_embedding_alive(alive)
    except Exception:  # noqa: BLE001 - 探活记录绝不影响主流程
        logger.debug("记录 embedding_alive 失败", exc_info=True)


class EmbeddingClient:
    """可注入 base_url / client 的 embedding 客户端（便于假 server 与单测）。

    双模式（PRD D22/G05 本地推理交付项，2026-11-11 用户确认双模式架构）：
    - API 模式（默认）：`EMBEDDING_MODE=api`（或缺省）——OpenAI 兼容 /embeddings
      HTTP 调用（百炼 text-embedding-v3 等）；
    - 本地模式：`EMBEDDING_MODE=local`——进程内 sentence_transformers 加载客户本地
      模型目录（如 BAAI/bge-large-zh-v1.5，1024 维与 Milvus collection 冻结维度
      一致，无需重建）；同样走查询缓存与维度校验，/health 探活同口径。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        dim: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.mode = (settings.embedding_mode or "api").strip().lower()
        self.base_url = base_url or settings.effective_embedding_base_url
        self.api_key = api_key or settings.effective_embedding_api_key
        self.model = model or settings.embedding_model
        self.timeout = timeout if timeout is not None else settings.embedding_timeout
        self.dim = dim if dim is not None else settings.embedding_dim
        self._client = client
        self._local_model: Any | None = None
        self._local_lock = threading.Lock()

    # ---- 本地模式（sentence_transformers，可选依赖） ----

    def _ensure_local_model(self) -> Any:
        if self._local_model is not None:
            return self._local_model
        with self._local_lock:
            if self._local_model is not None:
                return self._local_model
            path = settings.embedding_local_model_path
            if not path:
                raise EmbeddingError(
                    "EMBEDDING_MODE=local 但未配置 EMBEDDING_LOCAL_MODEL_PATH（本地模型目录）"
                )
            try:
                from sentence_transformers import SentenceTransformer  # noqa: PLC0415 可选依赖
            except ImportError as exc:
                raise EmbeddingError(
                    "本地 embedding 模式需要 sentence-transformers："
                    "backend/.venv/Scripts/pip install sentence-transformers"
                ) from exc
            try:
                self._local_model = SentenceTransformer(
                    path, device=settings.embedding_local_device or "cpu"
                )
            except Exception as exc:  # noqa: BLE001 模型加载失败如实报错
                raise EmbeddingError(
                    f"本地 embedding 模型加载失败（{path}）：{type(exc).__name__}: {str(exc)[:200]}"
                ) from exc
            logger.info("本地 embedding 模型已加载：%s", path)
        return self._local_model

    def _local_request(self, batch: list[str]) -> list[list[float]]:
        """本地模式单批推理（无网络调用；探活同样刷新——真实推理即真实依赖可用）。"""
        try:
            model = self._ensure_local_model()
            with self._local_lock:  # SentenceTransformer.encode 非线程安全，进程内串行
                vectors = model.encode(
                    batch, batch_size=len(batch), normalize_embeddings=True,
                    show_progress_bar=False,
                )
            vectors = [list(map(float, v)) for v in vectors]
        except EmbeddingError:
            _record_alive(False)
            raise
        except Exception as exc:  # noqa: BLE001 推理异常统一转 EmbeddingError
            _record_alive(False)
            raise EmbeddingError(f"本地 embedding 推理失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
        if len(vectors) != len(batch):
            _record_alive(False)
            raise EmbeddingError(f"Embedding 返回条数不匹配：请求 {len(batch)}，返回 {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dim:
                _record_alive(False)
                raise EmbeddingDimensionError(
                    f"Embedding 维度不符：本地模型 {settings.embedding_local_model_path} "
                    f"返回 {len(vector)}，EMBEDDING_DIM={self.dim}；"
                    "请校正配置或重建 Milvus collection"
                )
        _record_alive(True)
        return vectors

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,  # 重试策略由调用方（worker/补偿脚本）决定，此处不叠层
            )
        return self._client

    def _request(self, batch: list[str]) -> list[list[float]]:
        """单批真实调用（≤ EMBEDDING_BATCH_SIZE）；成功/失败都刷新被动探活。"""
        if self.mode == "local":
            return self._local_request(batch)
        try:
            resp = self.client.embeddings.create(model=self.model, input=batch)
            vectors = [list(item.embedding) for item in resp.data]
        except (APIStatusError, APITimeoutError, APIConnectionError) as exc:
            _record_alive(False)
            status = getattr(exc, "status_code", None)
            raise EmbeddingError(
                f"Embedding 调用失败（{type(exc).__name__}"
                f"{f'，HTTP {status}' if status else ''}）：{str(exc)[:200]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - SDK/供应商异常统一转 EmbeddingError
            _record_alive(False)
            raise EmbeddingError(f"Embedding 调用失败：{type(exc).__name__}: {str(exc)[:200]}") from exc

        if len(vectors) != len(batch):
            _record_alive(False)
            raise EmbeddingError(f"Embedding 返回条数不匹配：请求 {len(batch)}，返回 {len(vectors)}")

        for vector in vectors:
            if len(vector) != self.dim:
                _record_alive(False)
                raise EmbeddingDimensionError(
                    f"Embedding 维度不符：模型 {self.model} 返回 {len(vector)}，"
                    f"EMBEDDING_DIM={self.dim}；请校正配置或重建 Milvus collection"
                )
        _record_alive(True)
        return vectors

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """后台批量/检索入口：按 `EMBEDDING_TIMEOUT` 分片调用，返回与输入同序的向量。

        空串直接拒绝——空文本的向量没有检索意义，且会让 Milvus 报错难定位。
        """
        cleaned = [text if isinstance(text, str) else str(text) for text in texts]
        if not cleaned:
            return []
        if any(not text.strip() for text in cleaned):
            raise EmbeddingError("embedding 输入包含空文本")

        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), EMBEDDING_BATCH_SIZE):
            vectors.extend(self._request(cleaned[start : start + EMBEDDING_BATCH_SIZE]))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """后台单条（解析流水线 stage=vector 用），不走查询缓存。"""
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError("embedding 输入为空")
        return self.embed_texts([text])[0]

    def embed_query(self, text: str) -> list[float]:
        """语义检索查询向量：命中短期缓存则不产生真实调用（不刷新 embedding_alive）。"""
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError("查询文本为空")
        cached = _cache_get(text)
        if cached is not None:
            logger.debug("embedding 查询缓存命中（len=%d）", len(text))
            return cached
        vector = self.embed_one(text)
        _cache_put(text, vector)
        return vector

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


_default_client: EmbeddingClient | None = None
_client_lock = threading.Lock()


def get_embedding_client() -> EmbeddingClient:
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = EmbeddingClient()
        return _default_client


def reset_embedding_client() -> None:
    """测试用：丢弃默认单例。"""
    global _default_client
    with _client_lock:
        _default_client = None


# --------------------------------------------------------------------------- #
# 模块级便捷函数（业务侧统一从这里调，不自行 new EmbeddingClient）
# --------------------------------------------------------------------------- #
def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    return get_embedding_client().embed_texts(texts)


def embed_one(text: str) -> list[float]:
    return get_embedding_client().embed_one(text)


def embed_query(text: str) -> list[float]:
    return get_embedding_client().embed_query(text)
