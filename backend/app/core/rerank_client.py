"""重排适配层（PRD D22）· API 模式（百炼 gte-rerank）/ 本地模式（bge-reranker）双轨。

实现口径：
- 需求 §1 / DEC-20261107-DLINE-CONFIRMED：重排一期引入；API 模式=百炼 gte-rerank
  系列（OpenAI 兼容 /rerank 或原生端点），私有化/本地模式=本地 bge-reranker
  （sentence_transformers CrossEncoder，模型文件客户本地提供，不进 Git）
- `RERANK_MODE` 三值：`off`（默认，不重排——召回序原样返回，行为与未引入前一致）/
  `api`（HTTP 端点）/ `local`（进程内 CrossEncoder）；配置缺失/初始化失败**明确报错
  或如实降级，不静默假装重排过**（§5 模型真实调用、失败明确）
- 输出契约与 LLM/Embedding 同族：`rerank(query, docs) -> [(index, score)]` 按相关性
  降序；`rerank_texts` 便捷重排原文本；分数归一 0-1（两种模式内部各自归一）
- 本地模式与 API 模式的分数**不可跨模式比较**（不同模型不同分制），调用方只能用于
  同一次召回内的排序——docstring 明示，防跨模式混用

依赖纪律：`sentence-transformers` 为**可选依赖**（本地模式才需要）——requirements.txt
不强制安装（API 模式客户零额外依赖）；本地模式首次使用时给出明确安装指引。导入失败
的报错信息包含安装命令，不裸抛 ImportError。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol, Sequence

from app.config import settings

logger = logging.getLogger(__name__)

RERANK_MODES: tuple[str, ...] = ("off", "api", "local")


class RerankError(RuntimeError):
    """重排调用/配置失败的统一异常；message 可直接落日志与告警。"""


class _ModeReranker(Protocol):
    """单模式执行器协议：query 与 docs 同入，返回 [(原始下标, 分数)] 按分降序。"""

    def rerank(self, query: str, docs: Sequence[str]) -> list[tuple[int, float]]: ...


# --------------------------------------------------------------------------- #
# API 模式（百炼 gte-rerank / 任意 OpenAI 兼容 rerank 端点）
# --------------------------------------------------------------------------- #

class ApiReranker:
    """HTTP rerank 端点适配（DashScope 兼容模式 `POST {base}/rerank`）。

    请求体：{"model": ..., "query": ..., "documents": [...], "top_n": n}；
    响应：{"results": [{"index": i, "relevance_score": s}, ...]}（DashScope 兼容
    模式与 Cohere 风格一致）。429/5xx 不重试（重排在同步检索链上，超时预算由
    RERANK_TIMEOUT 控制，超时如实失败交上游决定是否回退召回序）。
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float) -> None:
        from openai import OpenAI  # noqa: PLC0415 复用 SDK 连接栈与超时语义

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)

    def rerank(self, query: str, docs: Sequence[str]) -> list[tuple[int, float]]:
        import httpx  # noqa: PLC0415 openai 传递依赖，必然在场

        url = f"{self._base_url}/rerank"
        body = {
            "model": self._model,
            "query": query,
            "documents": list(docs),
            "top_n": len(docs),
            "return_documents": False,
        }
        try:
            resp = httpx.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 网络异常统一转 RerankError
            raise RerankError(f"重排 API 调用失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
        if resp.status_code != 200:
            raise RerankError(f"重排 API HTTP {resp.status_code}：{resp.text[:200]}")
        data = resp.json()
        results = data.get("results")
        if not isinstance(results, list):
            raise RerankError(f"重排 API 响应缺少 results 数组：{str(data)[:200]}")
        out: list[tuple[int, float]] = []
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(idx, int) or not isinstance(score, (int, float)):
                raise RerankError(f"重排 API results 元素形状非法：{item!r}")
            out.append((idx, float(score)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out


# --------------------------------------------------------------------------- #
# 本地模式（sentence_transformers CrossEncoder + 客户本地模型目录）
# --------------------------------------------------------------------------- #

class LocalReranker:
    """进程内 CrossEncoder 重排（bge-reranker-base 等 seq2seq 分类头模型）。

    模型路径来自 `RERANK_LOCAL_MODEL_PATH`（客户本地模型目录，不进 Git）；
    CPU 可跑（bge-reranker-base ~1.1GB），设备由 `RERANK_LOCAL_DEVICE` 覆盖
    （缺省 auto）。首调懒加载（几秒级），进程内复用；线程安全由推理锁保证。
    """

    _lock = threading.Lock()

    def __init__(self, *, model_path: str, device: str | None = None) -> None:
        self._model_path = model_path
        self._device = device
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import CrossEncoder  # noqa: PLC0415 可选依赖
            except ImportError as exc:
                raise RerankError(
                    "本地重排模式需要 sentence-transformers："
                    "backend/.venv/Scripts/pip install sentence-transformers"
                ) from exc
            if not self._model_path:
                raise RerankError(
                    "RERANK_MODE=local 但未配置 RERANK_LOCAL_MODEL_PATH（本地 bge-reranker 模型目录）"
                )
            try:
                self._model = CrossEncoder(self._model_path, device=self._device or "auto")
            except Exception as exc:  # noqa: BLE001 模型加载失败如实报错
                raise RerankError(
                    f"本地重排模型加载失败（{self._model_path}）：{type(exc).__name__}: {str(exc)[:200]}"
                ) from exc
            logger.info("本地重排模型已加载：%s", self._model_path)
        return self._model

    def rerank(self, query: str, docs: Sequence[str]) -> list[tuple[int, float]]:
        model = self._ensure_model()
        pairs = [(query, doc) for doc in docs]
        with self._lock:  # CrossEncoder.predict 非线程安全，进程内串行
            scores = model.predict(pairs)
        out = [(i, float(s)) for i, s in enumerate(scores)]
        out.sort(key=lambda p: p[1], reverse=True)
        return out


# --------------------------------------------------------------------------- #
# 门面（与 llm_client / embedding_client 同族：单例 + 可注入 + 便捷函数）
# --------------------------------------------------------------------------- #

_reranker: _ModeReranker | None = None
_reranker_lock = threading.Lock()


def _build_reranker() -> _ModeReranker:
    mode = (settings.rerank_mode or "off").strip().lower()
    if mode == "off":
        raise RerankError("重排未启用（RERANK_MODE=off）")
    if mode == "api":
        base = settings.rerank_base_url or settings.effective_embedding_base_url
        key = settings.rerank_api_key or settings.effective_embedding_api_key
        model = settings.rerank_model or "gte-rerank"
        if not base:
            raise RerankError("RERANK_MODE=api 但未配置 RERANK_BASE_URL（缺省回退 Embedding 配置亦为空）")
        return ApiReranker(base_url=base, api_key=key or "", model=model,
                           timeout=settings.rerank_timeout)
    if mode == "local":
        return LocalReranker(
            model_path=settings.rerank_local_model_path,
            device=settings.rerank_local_device or None,
        )
    raise RerankError(f"RERANK_MODE 非法：{settings.rerank_mode!r}（允许 {', '.join(RERANK_MODES)}）")


def get_reranker() -> _ModeReranker | None:
    """当前模式执行器；`off` 返回 None（调用方跳过重排，不报错——默认行为）。"""
    global _reranker
    with _reranker_lock:
        if (settings.rerank_mode or "off").strip().lower() == "off":
            return None
        if _reranker is None:
            _reranker = _build_reranker()
        return _reranker


def reset_reranker() -> None:
    """测试/改配置后重建（模式切换须重启进程或调本函数）。"""
    global _reranker
    with _reranker_lock:
        _reranker = None


def rerank(query: str, docs: Sequence[str]) -> list[tuple[int, float]] | None:
    """门面：未启用返回 None（召回序原样用）；启用返回 [(下标, 分数)] 按分降序。

    分数仅供同一次召回内排序；跨模式（api vs local）分制不可比。
    """
    reranker = get_reranker()
    if reranker is None or not docs:
        return None
    if not query or not query.strip():
        raise RerankError("重排 query 为空")
    cleaned = [d if isinstance(d, str) else str(d) for d in docs]
    if any(not d.strip() for d in cleaned):
        raise RerankError("重排输入包含空文本")
    return reranker.rerank(query, cleaned)


def rerank_texts(query: str, texts: Sequence[str]) -> list[str] | None:
    """便捷：直接返回重排后的文本列表；未启用返回 None（原序）。"""
    result = rerank(query, texts)
    if result is None:
        return None
    return [texts[i] for i, _ in result]
