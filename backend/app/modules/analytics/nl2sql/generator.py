"""问题 → SQL 生成器（T-D09 ①Vanna/大模型桥）。

协议 `SQLGenerator`：generate_sql(question, schema_context) → str | None。
- 返回 None = **无法安全映射，拒答不猜测**（验收②的生成器侧）；
- 抛 `GeneratorUnavailable` = 生成通道不可用（LLM 未配置/网络失败）——调用方转 503。

实现：
- `OpenAICompatGenerator`：OpenAI 兼容 chat completions（当前 spark-x2.6，换 Agnes
  仅需改 .env 三键——DEC-20261107-AGNES-VERIFY 双轨预留）；LLM_API_KEY 为占位符时
  构造即失败（明确不可用，不静默降级）。
- vanna 2.0.2 为新版重写包（Agent 架构），与经典 ask/train API 不兼容；本接口层
  与 vanna 解耦——后续接 vanna 原生能力时实现同一协议即可，校验器/执行器零改动。
"""

from __future__ import annotations

from typing import Protocol

from app.config import settings


class GeneratorUnavailable(RuntimeError):
    """生成通道不可用（LLM 未配置 / 调用失败）——调用方转 503，不猜测。"""


class SQLGenerator(Protocol):
    def generate_sql(self, question: str, schema_context: str) -> str | None: ...


def _is_placeholder(key: str | None) -> bool:
    return not key or key.startswith("sk-your-") or key.strip() == ""


class OpenAICompatGenerator:
    """OpenAI 兼容 chat completions（spark/Agnes 皆此协议）。"""

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
        if _is_placeholder(api_key):
            raise GeneratorUnavailable("模型凭据未配置（LLM_API_KEY 为占位符）")
        from openai import OpenAI  # 延迟导入：配置缺失时不触发

        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=1)

    def generate_sql(self, question: str, schema_context: str) -> str | None:
        """调用 chat completions 生成 SQL；响应无法解析为 SQL 时返回 None（拒答）。"""
        system = (
            "你是只读分析库的 SQL 生成器。规则：\n"
            "1. 只输出一条 PostgreSQL SELECT 语句，不要任何解释、注释或 markdown。\n"
            "2. 只允许使用下面列出的表和列，不得访问其他对象。\n"
            "3. 始终加 LIMIT（不超过 1000 行）。\n"
            "4. 如果问题无法用这些表回答，只输出 CANNOT_ANSWER。\n\n"
            f"可用表和列：\n{schema_context}"
        )
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                temperature=0,
            )
        except Exception:  # noqa: BLE001 - 网络/凭据/配额失败统一转不可用
            raise GeneratorUnavailable("模型调用失败") from None
        text = (resp.choices[0].message.content or "").strip()
        if not text or text.upper().strip() == "CANNOT_ANSWER":
            return None
        # 剥 markdown 围栏（模型常见输出形态）
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("sql"):
                text = text[3:]
            text = text.strip()
        return text or None


class RefusingGenerator:
    """始终拒答（LLM 未启用时的默认实现——「拒绝不猜测」的兜底）。"""

    def generate_sql(self, question: str, schema_context: str) -> str | None:
        return None


def get_generator() -> SQLGenerator:
    """按配置选择生成器；spark key 可用 → OpenAI 兼容生成器，否则拒答兜底。"""
    if _is_placeholder(settings.llm_api_key):
        return RefusingGenerator()
    return OpenAICompatGenerator()
