"""LLM 适配层（T-04）· 全项目唯一 LLM 调用入口。

实现口径：
- `docs/架构设计.md` v1.4 §5.2：chat_json/chat_text、temperature=0、显式 max_tokens、
  JSON 修复 + Pydantic 校验 + 重试 1 次、超时 120s、全局信号量 2、429/5xx 指数退避、
  数据段包裹防注入、评分缓存钩子预留不实现（D-7）
- `docs/LLM提示词设计.md` v1.2：response_format=json_object、模板与 PROMPT_VERSION
- `docs/运行配置.md` v1.3：LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT / LLM_CONCURRENCY
- 开发日志 H-5：**spark-x2.6 是推理模型**，响应带 reasoning_content 且思维链吃 token，
  因此 max_tokens 给足（JSON 默认 4096）且**只解析 `choices[].message.content`**。

⚠️ 公共部分（开发日志 §1.3），修改前需用户确认。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable, Protocol, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.core.prompts import build_messages

logger = logging.getLogger(__name__)

# H-5：推理模型思维链会消耗 completion token，JSON 输出给足预算
DEFAULT_JSON_MAX_TOKENS = 4096
DEFAULT_TEXT_MAX_TOKENS = 1024
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE = 0.5  # 秒；第 n 次退避 = BACKOFF_BASE * 2**n
HTTP_MAX_ATTEMPTS = 2  # 单次 HTTP 调用：首次 + 1 次退避重试
RAW_SUMMARY_LEN = 300

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.S)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """LLM 调用/解析失败的统一异常；message 含原始输出摘要，供 worker 落失败原因。"""


class LLMJsonError(LLMError):
    """JSON 修复后仍无法解析。"""


class ScoreCache(Protocol):
    """D-7 评分缓存钩子接口（一期缓办，仅预留类型，不实现、不调用）。"""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...


_score_cache: ScoreCache | None = None


def set_score_cache(cache: ScoreCache | None) -> None:
    """D-7 预留：注册评分缓存实现。一期无实现，调用也不生效。"""
    global _score_cache
    _score_cache = cache


def get_score_cache() -> ScoreCache | None:
    return _score_cache


_semaphore: threading.Semaphore | None = None
_semaphore_lock = threading.Lock()


def _get_semaphore() -> threading.Semaphore:
    """全局 LLM 信号量，与 TaskRunner 线程池解耦（架构 §5.2）。"""
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.Semaphore(max(1, settings.llm_concurrency))
        return _semaphore


def reset_semaphore(concurrency: int | None = None) -> None:
    """测试/运行时改并发数后重建信号量。"""
    global _semaphore
    with _semaphore_lock:
        _semaphore = threading.Semaphore(max(1, concurrency or settings.llm_concurrency))


def _backoff(attempt: int) -> None:
    time.sleep(BACKOFF_BASE * (2**attempt))


def repair_json(raw: str) -> Any:
    """JSON 修复：去 markdown 围栏 → 截取最外层花括号/方括号 → json.loads。

    只做架构 §5.2 允许的两种修复，不做尾逗号/单引号等"猜测式"修补。
    """
    text = (raw or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    openers = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not openers:
        raise LLMJsonError(f"输出中找不到 JSON 对象；原始输出摘要：{text[:RAW_SUMMARY_LEN]!r}")
    start = min(openers)
    closing = "}" if text[start] == "{" else "]"
    end = text.rfind(closing)
    if end <= start:
        raise LLMJsonError(f"JSON 括号未闭合；原始输出摘要：{text[:RAW_SUMMARY_LEN]!r}")
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMJsonError(
            f"JSON 修复后仍无法解析（{exc}）；原始输出摘要：{text[:RAW_SUMMARY_LEN]!r}"
        ) from exc


class LLMClient:
    """可注入 base_url / client 的 LLM 客户端（便于假 server 测试）。

    中台联动（T-D20）：`purpose` 随调用传入并逐次落 ai_call_logs（审计旁路，
    落库失败不阻塞业务）；`provider_config_factory` 返回中台默认供应商配置
    （每次 get_llm_client 实时读取——供应商启停/切换即时生效），None 回退 .env。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        client: Any | None = None,
        purpose: str = "unspecified",
        provider_config_factory: Any | None = None,
    ) -> None:
        cfg = provider_config_factory() if provider_config_factory is not None else None
        if cfg is not None:
            base_url = cfg.get("base_url") or base_url
            api_key = cfg.get("api_key") or api_key
            model = cfg.get("model") or model
            if cfg.get("timeout_seconds"):
                timeout = float(cfg["timeout_seconds"])
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout
        self.purpose = purpose
        self.provider_name = (cfg or {}).get("name") or "env_default"
        self._provider_config_factory = provider_config_factory
        self._client = client
        # 部分供应商（含部分讯飞网关）不支持 response_format；首次 400 时自动降级
        self._json_format_supported = True

    @property
    def client(self) -> Any:
        # 每次访问都重查中台配置：供应商启停/默认切换即时生效（T-D20 验收②）。
        # client 已外部注入（测试）时不再重建。
        if self._client is None:
            cfg = (
                self._provider_config_factory() if self._provider_config_factory is not None else None
            )
            if cfg is not None:
                self.base_url = cfg.get("base_url") or self.base_url
                self.api_key = cfg.get("api_key") or self.api_key
                self.model = cfg.get("model") or self.model
                self.provider_name = cfg.get("name") or self.provider_name
                if cfg.get("timeout_seconds"):
                    self.timeout = float(cfg["timeout_seconds"])
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,  # 退避由本模块统一控制，避免 SDK 再叠一层
            )
        return self._client

    def _chat_raw(self, messages: list[dict[str, str]], max_tokens: int, want_json: bool,
                  purpose: str | None = None) -> str:
        """单次原始调用；内部处理 429/5xx 退避与 response_format 降级。

        `purpose` 单次覆盖实例默认（None=实例值），经 _record_call 落 ai_call_logs。
        """
        http_try = 0
        while True:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            if want_json and self._json_format_supported:
                kwargs["response_format"] = {"type": "json_object"}
            started = time.monotonic()
            try:
                with _get_semaphore():
                    resp = self.client.chat.completions.create(**kwargs)
                message = resp.choices[0].message
                # H-5：只取 content，忽略 reasoning_content（思维链不算业务输出）
                content = getattr(message, "content", None) or ""
                reasoning = getattr(message, "reasoning_content", None) or ""
                if not content.strip():
                    raise LLMError(
                        "LLM 返回 content 为空（H-5：推理模型思维链可能吃光 token，请加大 max_tokens）；"
                        f"reasoning_content 长度={len(reasoning)}"
                    )
                if reasoning:
                    logger.debug("忽略推理模型 reasoning_content（%d 字符）", len(reasoning))
                usage = getattr(resp, "usage", None)
                self._record_call(
                    result="ok", latency_ms=int((time.monotonic() - started) * 1000),
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    purpose_override=purpose,
                )
                return content
            except APIStatusError as exc:
                # 供应商不支持 response_format → 去掉该参数立即重试，不消耗重试预算
                if exc.status_code == 400 and want_json and self._json_format_supported:
                    self._json_format_supported = False
                    logger.warning("供应商不支持 response_format=json_object，已降级为普通 JSON 提示")
                    continue
                if exc.status_code in RETRYABLE_STATUS and http_try < HTTP_MAX_ATTEMPTS - 1:
                    logger.warning("LLM HTTP %s，第 %d 次退避后重试", exc.status_code, http_try + 1)
                    _backoff(http_try)
                    http_try += 1
                    continue
                self._record_call(
                    result="error", latency_ms=int((time.monotonic() - started) * 1000),
                    error=f"LLM HTTP {exc.status_code}：{str(exc)[:RAW_SUMMARY_LEN]}",
                    purpose_override=purpose,
                )
                raise LLMError(f"LLM HTTP {exc.status_code}：{str(exc)[:RAW_SUMMARY_LEN]}") from exc
            except (APITimeoutError, APIConnectionError) as exc:
                if http_try < HTTP_MAX_ATTEMPTS - 1:
                    logger.warning("LLM 网络异常 %s，第 %d 次退避后重试", type(exc).__name__, http_try + 1)
                    _backoff(http_try)
                    http_try += 1
                    continue
                self._record_call(
                    result="error", latency_ms=int((time.monotonic() - started) * 1000),
                    error=f"LLM 网络异常：{type(exc).__name__}: {exc}",
                    purpose_override=purpose,
                )
                raise LLMError(f"LLM 网络异常：{type(exc).__name__}: {exc}") from exc

    def _record_call(self, *, result: str, latency_ms: int,
                     prompt_tokens: int | None = None,
                     completion_tokens: int | None = None,
                     error: str | None = None,
                     purpose_override: str | None = None) -> None:
        """中台调用日志埋点（T-D20）：审计旁路，落库失败不阻塞业务。

        `purpose_override` 单次调用覆盖实例默认（None=实例值）。
        """
        try:
            from app.database import SessionLocal  # noqa: PLC0415
            from app.modules.dashboard import service as ai_platform  # noqa: PLC0415

            with SessionLocal() as db:
                ai_platform.record_call(
                    db, provider_name=self.provider_name, model=self.model,
                    purpose=purpose_override or self.purpose, result=result, latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    error=error,
                )
        except Exception:  # noqa: BLE001
            logger.exception("AI 调用日志落库异常（旁路，不阻塞）")

    def chat_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        purpose: str | None = None,
    ) -> str:
        """纯文本调用（不带 response_format），返回 content 原文。

        `purpose` 单次覆盖实例默认用途（T-D20 调用日志维度）；None 用实例值。
        """
        messages = build_messages(prompt, system_prompt)
        return self._chat_raw(messages, max_tokens or DEFAULT_TEXT_MAX_TOKENS,
                              want_json=False, purpose=purpose)

    def chat_json(
        self,
        prompt: str,
        schema: type[T] | None = None,
        *,
        system_prompt: str | None = None,
        prompt_name: str | None = None,
        retry: int = 1,
        max_tokens: int | None = None,
        on_result: Callable[[dict[str, Any]], None] | None = None,
        purpose: str | None = None,
    ) -> T | dict:
        """结构化调用：JSON 修复 → Pydantic 校验 → 失败自动重试 `retry` 次。

        `schema` 为 Pydantic 模型时返回校验后的模型实例；传 None 时只返回 dict。
        重试耗尽抛 `LLMError`，message 含原始输出摘要供 worker 落失败原因与阶段。
        `purpose` 单次覆盖实例默认用途（T-D20 调用日志维度）；None 用实例值。
        """
        if schema is not None and not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError("schema 必须是 pydantic.BaseModel 子类或 None（JSON schema 请用 prompt_name 传入）")

        messages = build_messages(prompt, system_prompt, prompt_name=prompt_name)
        tokens = max_tokens or DEFAULT_JSON_MAX_TOKENS
        attempts = max(1, retry + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            # D-7 缓办：评分缓存钩子接入点（get/set），一期不查询、不写入、不建表
            raw = ""
            try:
                raw = self._chat_raw(messages, tokens, want_json=True, purpose=purpose)
                data = repair_json(raw)
                result = data if schema is None else schema.model_validate(data)
                if on_result is not None:
                    on_result({"attempt":attempt + 1, "status":"validated", "raw_text":raw[:16000],
                               "raw_truncated":len(raw) > 16000})
                return result
            except (LLMJsonError, ValidationError, LLMError) as exc:
                last_error = exc
                if on_result is not None:
                    on_result({"attempt":attempt + 1, "status":"failed", "raw_text":raw[:16000],
                               "raw_truncated":len(raw) > 16000, "error":str(exc)[:2000]})
                if attempt < attempts - 1:
                    logger.warning("LLM 结构化输出第 %d 次失败，重试：%s", attempt + 1, exc)
                    continue

        raise LLMError(
            f"LLM 结构化输出失败（共 {attempts} 次尝试）：{last_error}"
        ) from last_error


_default_client: LLMClient | None = None
_default_client_lock = threading.Lock()


def _default_provider_config() -> dict[str, Any] | None:
    """中台默认供应商配置（T-D20 验收②启停/切换即时生效）。

    每次实时读库；中台不可用/未配置时返回 None（回退 .env，不阻塞）。
    """
    try:
        from app.database import SessionLocal  # noqa: PLC0415
        from app.modules.dashboard import service as ai_platform  # noqa: PLC0415

        with SessionLocal() as db:
            row = ai_platform.resolve_default_provider(db)
            if row is None:
                return None
            return {
                "name": row.name,
                "base_url": row.base_url,
                "api_key": row.api_key,
                "model": row.model,
                "timeout_seconds": row.timeout_seconds,
            }
    except Exception:  # noqa: BLE001 - 中台库不可用回退 .env
        return None


def get_llm_client() -> LLMClient:
    global _default_client
    with _default_client_lock:
        if _default_client is None:
            _default_client = LLMClient(provider_config_factory=_default_provider_config)
        return _default_client


def reset_llm_client() -> None:
    """测试用：丢弃默认单例（也会重建 response_format 探测状态）。"""
    global _default_client
    with _default_client_lock:
        _default_client = None


def chat_json(prompt: str, schema: type[T] | None = None, **kwargs: Any) -> T | dict:
    """模块级结构化调用；`purpose` 关键字经 kwargs 透传到实例（T-D20 调用日志维度）。"""
    return get_llm_client().chat_json(prompt, schema, **kwargs)


def chat_text(prompt: str, **kwargs: Any) -> str:
    """模块级纯文本调用；`purpose` 关键字经 kwargs 透传到实例。"""
    return get_llm_client().chat_text(prompt, **kwargs)


def with_purpose(purpose: str) -> LLMClient:
    """带用途标签的客户端（T-D20 调用日志 purpose 维度）。

    复用默认供应商配置工厂；同一进程多 purpose 并发各自持有实例。
    """
    return LLMClient(provider_config_factory=_default_provider_config, purpose=purpose)
