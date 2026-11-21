"""tests/B 共享前置：sys.path 前置 backend/（REQ-C-20261114-224100-B 第 2 项）。

与 tests/C/c_harness.py、tests/D/conftest.py 对齐，使仓库根 `pytest tests/B`
可直接收集（否则模块级 `import app.*` 报 ModuleNotFoundError）。
同时把 backend/ 写入 PYTHONPATH，保证 B 的冷启动子进程用例（如冷导入
knowledge/qa_worker）从仓库根直跑时也能解析 `app.*`。
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
if str(BACKEND_DIR) not in _parts:
    os.environ["PYTHONPATH"] = os.pathsep.join([str(BACKEND_DIR), *_parts])
