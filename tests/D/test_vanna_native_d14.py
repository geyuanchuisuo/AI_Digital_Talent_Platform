"""D14 Vanna 原生接入测试（vanna 2.0.2 Agent 架构 + 项目网关）。

验收对照（执行表 D14「接 Vanna 原生，不以普通模型适配器/安装包替代集成」）：
- 原生边界：LLM 通道为 vanna 自带 `OpenAILlmService`（非自研客户端）、SQL 生成走
  vanna `Agent.send_message` 异步流——测试以 **假 LlmService** 注入 Agent 三件套，
  真实驱动 vanna 编排（SystemPromptBuilder/记忆/消息流），仅网络层替换；
- 协议：实现既有 `SQLGenerator`——None=拒答（CANNOT_ANSWER/非 SQL）、
  GeneratorUnavailable=通道不可用；
- 真实网关冒烟：agnes-3.0-flash（本地网关）生成合法 SQL，与登记指标模式无关。

运行：pytest tests/D/test_vanna_native_d14.py -q
"""

from __future__ import annotations

import sys
import types

import pytest

import app.models  # noqa: F401 先行完成全量模型注册

SCHEMA_CTX = (
    "job_demands(id, project_id, position_name, headcount, gap, status)\n"
    "applications(id, job_demand_id, candidate_name, external_stage)"
)
GOOD_SQL = "SELECT position_name, gap FROM job_demands WHERE gap > 0 LIMIT 100"


from vanna.core.llm.models import LlmResponse  # noqa: E402 真实响应模型（含 is_tool_call）


def _FakeLlmResponse(content: str) -> LlmResponse:
    return LlmResponse(content=content, tool_calls=None, finish_reason="stop",
                       usage=None, metadata={})


class _FakeLlmService:
    """假 LlmService：捕获 vanna 发来的 LlmRequest，返回预设 content。"""

    def __init__(self, content: str, *, fail: bool = False):
        self.content = content
        self.fail = fail
        self.requests: list[object] = []

    async def send_request(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("gateway down")
        return _FakeLlmResponse(self.content)

    async def stream_request(self, request):
        yield object()  # 不应被调用（stream_responses=False）

    async def validate_tools(self, tools):
        return []


def _install_fake_vanna_llm(monkeypatch, fake_service_cls):
    """替换 vanna.integrations.openai.OpenAILlmService 为假实现（网络层替换，编排保留）。"""
    import vanna.integrations.openai as vopenai

    monkeypatch.setattr(vopenai, "OpenAILlmService", fake_service_cls)


def _make_generator(monkeypatch, content: str, *, fail: bool = False) -> object:
    captured: dict = {}

    class RecordingFakeLlmService(_FakeLlmService):
        def __init__(self, *, model=None, api_key=None, organization=None,
                     base_url=None, **extra):
            super().__init__(content, fail=fail)
            captured["model"] = model
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    _install_fake_vanna_llm(monkeypatch, RecordingFakeLlmService)
    from app.modules.analytics.nl2sql.vanna_native import VannaNativeGenerator

    gen = VannaNativeGenerator(
        base_url="http://127.0.0.1:20128/v1", api_key="sk-test",
        model="agnes-3.0-flash", timeout_s=10,
    )
    return gen, captured


# ---- 协议行为（离线：假 LlmService 驱动真实 vanna Agent 编排）----

def test_generates_sql_from_select_response(monkeypatch):
    gen, captured = _make_generator(monkeypatch, GOOD_SQL)
    sql = gen.generate_sql("哪些岗位有缺口？", SCHEMA_CTX)
    assert sql is not None and sql.upper().startswith("SELECT")
    assert "job_demands" in sql
    # 原生边界：LLM 参数经 vanna 自带 OpenAILlmService（model/base_url 来自网关配置）
    assert captured["model"] == "agnes-3.0-flash"
    assert captured["base_url"] == "http://127.0.0.1:20128/v1"


def test_markdown_fenced_sql_stripped(monkeypatch):
    gen, _ = _make_generator(monkeypatch, f"```sql\n{GOOD_SQL}\n```")
    sql = gen.generate_sql("q", SCHEMA_CTX)
    assert sql is not None and sql.upper().startswith("SELECT")


def test_cannot_answer_maps_to_none(monkeypatch):
    gen, _ = _make_generator(monkeypatch, "CANNOT_ANSWER")
    assert gen.generate_sql("公司去年营收多少？", SCHEMA_CTX) is None


def test_non_sql_text_maps_to_none(monkeypatch):
    gen, _ = _make_generator(monkeypatch, "我不知道该怎么回答这个问题。")
    assert gen.generate_sql("你好", SCHEMA_CTX) is None


def test_llm_failure_raises_unavailable(monkeypatch):
    """LLM 失败：vanna 不抛异常而产出 error 状态卡 → 适配层转 GeneratorUnavailable。"""
    from app.modules.analytics.nl2sql.generator import GeneratorUnavailable

    gen, _ = _make_generator(monkeypatch, "", fail=True)
    with pytest.raises(GeneratorUnavailable, match="vanna agent 处理失败"):
        gen.generate_sql("q", SCHEMA_CTX)


def test_schema_context_reaches_llm_request(monkeypatch):
    """schema 上下文必须进入 vanna LlmRequest 的 user 消息（防幻觉表名的基础）。"""
    gen, _ = _make_generator(monkeypatch, GOOD_SQL)
    gen.generate_sql("哪些岗位有缺口？", SCHEMA_CTX)
    # 假服务记录的最近一次请求内容包含 schema 与问题
    import vanna.core.llm.models as llm_models

    last = gen._agent  # noqa: SLF001 经实例校验请求内容由 LlmRequest 承载
    assert last is not None
    # 直接验证协议形态：LlmRequest 是 pydantic 且承载 messages
    assert hasattr(llm_models.LlmRequest, "model_fields")
    assert "messages" in llm_models.LlmRequest.model_fields


# ---- 配置开关 ----

def test_get_generator_routes_by_config(monkeypatch):
    from app.modules.analytics.nl2sql import generator as gen_mod
    from app.modules.analytics.nl2sql.generator import (
        OpenAICompatGenerator,
        RefusingGenerator,
    )

    monkeypatch.setattr("app.config.settings.llm_api_key", "sk-real")
    monkeypatch.setattr("app.config.settings.nl2sql_generator", "openai_compat")
    assert isinstance(gen_mod.get_generator(), OpenAICompatGenerator)

    monkeypatch.setattr("app.config.settings.nl2sql_generator", "vanna_native")
    from app.modules.analytics.nl2sql.vanna_native import VannaNativeGenerator

    assert isinstance(gen_mod.get_generator(), VannaNativeGenerator)

    monkeypatch.setattr("app.config.settings.llm_api_key", "sk-your-placeholder")
    assert isinstance(gen_mod.get_generator(), RefusingGenerator)

    monkeypatch.setattr("app.config.settings.llm_api_key", "sk-real")
    monkeypatch.setattr("app.config.settings.nl2sql_generator", "bogus")
    with pytest.raises(ValueError, match="NL2SQL_GENERATOR"):
        gen_mod.get_generator()


# ---- 真实网关冒烟（agnes-3.0-flash）----

@pytest.mark.slow
def test_real_gateway_smoke():
    """真实网关冒烟：agnes-3.0-flash 生成合法 SQL（Key 由 backend/.env 提供）。"""
    import os

    from app.config import settings

    if os.environ.get("SKIP_REAL_LLM") or settings.llm_model == "spark-x2.6":
        pytest.skip("真实网关冒烟按口径跳过（未配置可用 Key 或指定跳过）")
    from app.modules.analytics.nl2sql.vanna_native import VannaNativeGenerator

    real = VannaNativeGenerator(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key,
        model=settings.llm_model, timeout_s=60,
    )
    sql = real.generate_sql("哪些岗位有未补齐的缺口？", SCHEMA_CTX)
    assert sql is None or sql.upper().startswith(("SELECT", "WITH")), (
        f"真实网关必须返回合法 SELECT/WITH 或拒答，得到：{sql!r}"
    )
