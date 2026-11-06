"""测试级 conftest（T-05 新增，T-13 补 TaskRunner 隔离）。

L1 单元测试按开发日志 §1.2 要求**不触任何外部服务**：应用 lifespan 现在会做
Milvus collection 自检（T-05），因此非 integration 用例统一把它替换为空操作，
避免单测启动 TestClient 时连 Milvus。标记 `@pytest.mark.integration` 的用例保留真实行为。

T-13 起 lifespan 还会注册解析 handler 并启动 TaskRunner；若真跑起来，2 秒后它就会认领
库里真实的 `pending` 任务（连真实 LLM / Embedding / Milvus），把上传、鉴权等集成测试
变成"看天吃饭"。所以默认把 `TaskRunner.start/stop` 打成空操作，需要真跑生命周期的用例
标 `@pytest.mark.real_runner`。业务逻辑一律通过直接调用 handler / `run_once()` 验证。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_external_startup_for_unit_tests(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.setattr("app.main._ensure_milvus_collection", lambda: None)


@pytest.fixture(autouse=True)
def _isolate_task_runner(request, monkeypatch):
    """不让全局单例的后台扫描线程参与测试（见模块 docstring 的 T-13 说明）。

    ⚠️ 只 patch **全局单例实例**（`app.tasks.runner.runner`），不 patch `TaskRunner` 类：
    T-12 的 runner 测试会直接 `TaskRunner()` 实例化并调用 `start()/stop()` 验证
    启动恢复 / 停止回置，patch 类方法会把它们打成空操作导致假失败。
    直接实例化不触发 lifespan，不会有后台线程，天然安全。
    """
    if request.node.get_closest_marker("real_runner"):
        return
    from app.tasks import runner as runner_module

    monkeypatch.setattr(runner_module.runner, "start", lambda: None, raising=False)
    monkeypatch.setattr(runner_module.runner, "stop", lambda *a, **kw: None, raising=False)
