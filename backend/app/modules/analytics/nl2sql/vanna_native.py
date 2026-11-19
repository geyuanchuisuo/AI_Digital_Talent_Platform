"""Vanna 原生接入（PRD D14）——vanna 2.0.2 Agent 架构 + 项目 OpenAI 兼容网关。

「原生」边界（执行表 D14：不以普通模型适配器/安装包替代集成）：
- LLM 通道走 **vanna 自带的** `OpenAILlmService`（`vanna.integrations.openai`）——
  网关为其 `base_url/api_key` 参数，不经自研 OpenAI 客户端；
- SQL 生成走 **vanna 自己的 Agent**（`vanna.Agent.send_message` 异步流），自研侧只做
  「同步协议适配 + 输出校验」，不替代其编排；
- Agent 三件套最小化：空 ToolRegistry（纯生成不做工具调用）、固定用户解析器、
  进程内 DemoAgentMemory（vanna 自带 integrations.local）。

协议 `SQLGenerator`（generator.py）：generate_sql(question, schema_context) → str | None；
- None = 无法安全映射/拒答（CANNOT_ANSWER、非 SQL 文本）；
- GeneratorUnavailable = vanna/网关不可用——调用方转 503。

已验收边界：真实网关（agnes-3.0-flash）单发生成；Agent 多轮工具调用、RAG 训练
（DDL/文档 training）与 PgSQL 副本联动未接入（一期免工具纯生成口径）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.modules.analytics.nl2sql.generator import GeneratorUnavailable

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是只读分析库的 SQL 生成器。规则：\n"
    "1. 只输出一条 PostgreSQL SELECT 语句，不要任何解释、注释或 markdown。\n"
    "2. 只允许使用给定 schema 上下文中的表和列。\n"
    "3. 始终加 LIMIT（不超过 1000 行）。\n"
    "4. 无法回答时只输出 CANNOT_ANSWER。"
)

_MAX_COLLECT_CHARS = 4000  # 生成 SQL 足够；防异常长输出拖垮解析


def _build_agent(model: str, base_url: str, api_key: str, timeout_s: float) -> Any:
    """构造 vanna Agent 三件套（全部为 vanna 自带实现——原生边界）。"""
    from vanna import Agent, AgentConfig
    from vanna.core.registry import ToolRegistry
    from vanna.core.user.models import User
    from vanna.core.user.request_context import RequestContext
    from vanna.core.user.resolver import UserResolver
    from vanna.integrations.local.agent_memory.in_memory import DemoAgentMemory
    from vanna.integrations.openai import OpenAILlmService

    llm = OpenAILlmService(model=model, api_key=api_key, base_url=base_url,
                           timeout=timeout_s)

    class FixedUserResolver(UserResolver):
        """单租户内部服务：任何请求上下文都解析为同一分析账号。"""

        async def resolve_user(self, request_context: RequestContext) -> User:
            return User(id="ait_nl2sql", username="ait_nl2sql",
                        email=None, metadata={}, group_memberships=[])

    memory = DemoAgentMemory(max_items=64)
    config = AgentConfig(
        max_tool_iterations=1,
        stream_responses=False,
        auto_save_conversations=True,
        include_thinking_indicators=False,
        temperature=0,
        max_tokens=512,
    )
    return Agent(
        llm_service=llm,
        tool_registry=ToolRegistry(),
        user_resolver=FixedUserResolver(),
        agent_memory=memory,
        config=config,
    )


def _request_context() -> Any:
    from vanna.core.user.request_context import RequestContext

    return RequestContext(cookies={}, headers={}, remote_addr="127.0.0.1",
                          query_params={}, metadata={})


async def _collect_agent_answer(agent: Any, prompt: str) -> str:
    """驱动 Agent 异步流，收集最终 assistant 文本。

    - 文本载体为嵌套字段：`rich_component.content` 或 `simple_component.text`
      （顶层 UiComponent.content/markdown 均为 None——vanna 2.0.2 实测）；
    - LLM 失败时 vanna 不抛异常，而是产出 `status_card(status=error)` 组件
      （错误详情被 vanna 通用文案吞掉）——识别后转 `GeneratorUnavailable`；
    - 无任何文本组件（通道异常）同样转 `GeneratorUnavailable`，不静默返回空。
    """
    chunks: list[str] = []
    ctx = _request_context()
    async for comp in agent.send_message(ctx, prompt):
        rich = getattr(comp, "rich_component", None)
        if rich is not None and getattr(rich, "type", "") == "status_card" \
                and getattr(rich, "status", "") == "error":
            raise GeneratorUnavailable(
                f"vanna agent 处理失败：{getattr(rich, 'title', 'Error')}"
                f"（{getattr(rich, 'description', '')[:150]}）"
            )
        if rich is not None:
            content = getattr(rich, "content", None)
            if isinstance(content, str) and content.strip():
                chunks.append(content)
        simple = getattr(comp, "simple_component", None)
        if simple is not None:
            text = getattr(simple, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    text = "\n".join(chunks).strip()
    if not text:
        raise GeneratorUnavailable("vanna agent 未返回任何文本（通道异常）")
    return text[:_MAX_COLLECT_CHARS]


class VannaNativeGenerator:
    """vanna 原生 Agent 生成器（实现既有 SQLGenerator 协议，零改动接入校验器/执行器）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        base_url = base_url or settings.llm_base_url
        api_key = api_key or settings.llm_api_key
        self._model = model or settings.llm_model
        self._timeout_s = timeout_s
        if not base_url or not api_key or api_key.startswith("sk-your-"):
            raise RuntimeError("vanna 原生生成器需要可用网关（base_url/api_key）")
        self._agent = _build_agent(self._model, base_url, api_key, timeout_s)

    def generate_sql(self, question: str, schema_context: str) -> str | None:
        """同步协议入口：经 asyncio.run 驱动 vanna Agent 异步流（to_thread 线程无环）。

        通道不可用/超时 → GeneratorUnavailable（调用方 503，与其他生成器同语义）；
        拒答/非 SQL → None。
        """
        prompt = (
            f"{_SYSTEM_PROMPT}\n\n可用表和列：\n{schema_context}\n\n用户问题：{question}"
        )
        try:
            text = asyncio.run(self._collect_with_timeout(prompt))
        except GeneratorUnavailable:
            raise
        except asyncio.TimeoutError as exc:
            raise GeneratorUnavailable(
                f"vanna 生成超时（>{self._timeout_s}s）"
            ) from exc
        except Exception as exc:  # noqa: BLE001 网关/agent 异常统一转不可用
            raise GeneratorUnavailable(
                f"vanna 原生生成失败：{type(exc).__name__}: {str(exc)[:200]}"
            ) from exc
        return _extract_sql(text)

    async def _collect_with_timeout(self, prompt: str) -> str:
        return await asyncio.wait_for(
            _collect_agent_answer(self._agent, prompt), timeout=self._timeout_s
        )


def _extract_sql(text: str) -> str | None:
    """与 OpenAICompatGenerator 同一套输出校验（拒答一致，验收②）。"""
    text = (text or "").strip()
    if not text or text.upper().strip() == "CANNOT_ANSWER":
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("sql"):
            text = text[3:]
        text = text.strip()
    if not text:
        return None
    import re  # noqa: PLC0415 局部避免顶部杂项

    if not re.match(r"^\s*(SELECT|WITH)\s", text, flags=re.IGNORECASE):
        return None
    return text
