"""TaskRunner 状态共享（T-12）。

`GET /api/health` 的 `worker` 字段与 `tasks/runner.py` 之间只通过本模块交换数据：
runner 每轮扫描后写一次，health 只读。放在 `core/` 而不是 `api/`，是为了让
`tasks → api` 的反向依赖不出现（tasks 不应该 import api）。

线程安全：字典写入整体替换 + 简单锁，读方拿浅拷贝；不追求强一致，health 允许
比真实状态晚一个扫描周期（2s）。
"""

from __future__ import annotations

import threading
from typing import Any

from app.config import settings

BACKLOG_KEYS: tuple[str, ...] = ("parse", "screen", "match", "duplicate_pending", "cancelled")

_lock = threading.Lock()
_state: dict[str, Any] = {
    "alive": False,
    "last_heartbeat": None,
    "concurrency": settings.task_concurrency,
    "backlog": {key: 0 for key in BACKLOG_KEYS},
}


def set_worker_state(
    *,
    alive: bool,
    last_heartbeat: str | None = None,
    backlog: dict[str, int] | None = None,
) -> None:
    """runner 上报；`backlog` 传 None 表示保持上次值（避免局部更新把计数清零）。"""
    with _lock:
        _state["alive"] = alive
        _state["last_heartbeat"] = last_heartbeat
        _state["concurrency"] = settings.task_concurrency
        if backlog is not None:
            _state["backlog"] = {key: int(backlog.get(key, 0)) for key in BACKLOG_KEYS}


def get_worker_state() -> dict[str, Any]:
    """health 读取：返回拷贝，避免调用方改到共享字典。"""
    with _lock:
        return {
            "alive": _state["alive"],
            "last_heartbeat": _state["last_heartbeat"],
            "concurrency": _state["concurrency"],
            "backlog": dict(_state["backlog"]),
        }


def reset_worker_state() -> None:
    """仅测试/进程关闭时用：恢复"未启动"初值。"""
    with _lock:
        _state["alive"] = False
        _state["last_heartbeat"] = None
        _state["backlog"] = {key: 0 for key in BACKLOG_KEYS}
