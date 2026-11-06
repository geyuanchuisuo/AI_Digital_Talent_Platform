"""T-04 验收点：真实 key 下 chat_json 一次成功（手动执行，不进 pytest 全集）。

用法（在 main/backend 目录）：
    ./.venv/Scripts/python.exe scripts/smoke_llm.py

会真实外呼 LLM 并产生少量费用；只验证链路，不写库。
不进 pytest 是为了避免每次全量测试都外呼（日志 §1.2 的 L3 集中到 B6）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.llm_client import get_llm_client  # noqa: E402


class SmokeResult(BaseModel):
    name: str
    degree: str


def main() -> int:
    client = get_llm_client()
    print(f"base_url={client.base_url}")
    print(f"model={client.model}  timeout={client.timeout}s  max_tokens=4096")
    started = time.monotonic()
    try:
        result = client.chat_json(
            "候选人：李明，学历：本科。请只提取姓名与学历。",
            SmokeResult,
            system_prompt=(
                "你是结构化提取工具。只输出 JSON：{\"name\": 字符串, \"degree\": 字符串}。"
                "degree 只能是 博士/硕士/本科/大专/高中中专/其他 之一。不要输出其他文字。"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 验收脚本需打印完整失败原因
        print(f"❌ chat_json 失败（{time.monotonic() - started:.1f}s）：{type(exc).__name__}: {exc}")
        return 1
    elapsed = time.monotonic() - started
    print(f"✅ chat_json 成功（{elapsed:.1f}s）：{result!r}")
    print(f"response_format 可用：{client._json_format_supported}")
    if result.name != "李明" or result.degree != "本科":
        print("⚠️ 结构化内容不符合预期，请检查提示词或模型输出")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
