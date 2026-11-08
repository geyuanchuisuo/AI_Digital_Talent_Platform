"""SSE 测试客户端（T-D00f）——text/event-stream 解析与流式收集。

最小实现：解析 `event:`/`data:` 块；供 T-D06 SSE 端点与后续任务使用。
"""
from __future__ import annotations

from typing import Any


def parse_sse(text: str) -> list[dict[str, str]]:
    """把 text/event-stream 文本解析为 [{event, data, ...字段}] 列表。

    遵循 SSE 规范：空行分隔事件块；`field: value`；值前至多一个空格被剥离。
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                events.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        current[field] = current.get(field, "") + value
    if current:
        events.append(current)
    return events


def parse_sse_data(events: list[dict[str, str]]) -> list[str]:
    """提取全部 data 行（多行 data 以 \n 连接由调用方处理）。"""
    return [e.get("data", "") for e in events if "data" in e]
