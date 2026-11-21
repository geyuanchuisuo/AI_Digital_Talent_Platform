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


@pytest.fixture(autouse=True, scope="session")
def _isolate_task_runner_session(request):
    """Session 级先行隔离（REQ-C-20261121-171500-A 根因修复）。

    函数级 `_isolate_task_runner` 的 setup 晚于**模块级 fixture**（如
    `test_match_batch_t25.py` 的 `client`）：pytest 先实例化高 scope fixture，
    模块级 `TestClient(app)` 首个请求触发 lifespan 时 patch 尚未生效，真实
    `TaskRunner` 启动后台线程并在整模块内每 2 秒扫描，与直接调用 handler 的
    用例形成双执行竞态（2026-11-21 全量运行实锤：`match_results` 1062 重复键、
    LLM 调用数 +1）。本 fixture 在所有 fixture 之前把全局单例打成空操作；
    session 结束恢复。`real_runner` 用例由函数级 fixture 恢复真实行为。
    """
    from app.tasks import runner as runner_module

    runner = runner_module.runner
    orig_start, orig_stop = runner.start, runner.stop
    request.config._store["runner_orig"] = (orig_start, orig_stop)
    runner.start = lambda: None
    runner.stop = lambda *a, **kw: None
    yield
    runner.start, runner.stop = orig_start, orig_stop


@pytest.fixture(autouse=True)
def _isolate_task_runner(request, monkeypatch):
    """不让全局单例的后台扫描线程参与测试（见模块 docstring 的 T-13 说明）。

    ⚠️ 只 patch **全局单例实例**（`app.tasks.runner.runner`），不 patch `TaskRunner` 类：
    T-12 的 runner 测试会直接 `TaskRunner()` 实例化并调用 `start()/stop()` 验证
    启动恢复 / 停止回置，patch 类方法会把它们打成空操作导致假失败。
    直接实例化不触发 lifespan，不会有后台线程，天然安全。

    Session 级先行隔离见 `_isolate_task_runner_session`（本 fixture 保持函数级
    monkeypatch 语义：每个测试前后状态干净；real_runner 用例在此恢复真实方法）。
    """
    if request.node.get_closest_marker("real_runner"):
        orig = request.config._store.get("runner_orig", None)
        if orig:
            from app.tasks import runner as runner_module

            monkeypatch.setattr(runner_module.runner, "start", orig[0])
            monkeypatch.setattr(runner_module.runner, "stop", orig[1])
        return
    from app.tasks import runner as runner_module

    monkeypatch.setattr(runner_module.runner, "start", lambda: None, raising=False)
    monkeypatch.setattr(runner_module.runner, "stop", lambda *a, **kw: None, raising=False)


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    """测试进程统一关闭 Redis 限流器（T-D01b 回归修复，2026-11-08）。

    本 suite 数百个请求共享同一个客户端身份（TestClient），真开 120/min 固定窗口
    必然在字母序靠后的集成测试里全部 429 误伤（t18/t10 实证）。Redis 相关用例
    （tests/D/test_cache.py 等）自建 CacheClient 显式注入 URL，不受本夹具影响。
    """
    monkeypatch.setattr("app.config.settings.redis_url", "")
    from app.core.cache import reset_cache

    reset_cache(None)
    yield
    reset_cache(None)
