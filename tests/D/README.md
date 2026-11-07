# tests/D · D 域测试脚手架（T-D00f）

> 维护：D　|　运行方式：仓库根目录执行 `backend/.venv/Scripts/python.exe -m pytest tests/D -v`
> （或任意已装 requirements 的 Python；`tests/D/conftest.py` 会自动把 `backend/` 加入 sys.path）

## 组成

| 文件 | 用途 |
|---|---|
| `conftest.py` | sys.path 前置 backend/；SQLite 编译规则（TINYINT/MEDIUMTEXT/BIGINT 映射、ON UPDATE CURRENT_TIMESTAMP 剥离），使 51 表（A_0007 基线）可免 MySQL 建表 |
| `fake_llm.py` | `FakeLLMServer`：OpenAI 兼容假服务器，三态注入 success/timeout/malformed |
| `events_factory.py` | 通知事件工厂：14 个 event_type 的 payload+幂等键构造（对应 [通知契约](../../docs/modules/D/通知契约.md)） |
| `sse_client.py` | `parse_sse(text)` / `parse_sse_data(events)`：text/event-stream 最小解析 |
| `test_harness_smoke.py` | 冒烟：三态切换、14 类事件幂等键唯一、SSE 解析、SQLite ORM 往返 |

## 用法示例

```python
# 假 LLM（成功/超时/畸形三态）
from tests.D.fake_llm import FakeLLMServer   # 或 from fake_llm import ...
server = FakeLLMServer("success")
client = OpenAI(base_url=server.base_url, api_key="test", timeout=0.5, max_retries=0)
server.set_mode("timeout")   # 切换注入态

# 事件工厂
from events_factory import build_event, build_all_events
event = build_event("recruitment.stage_changed", ref_id=42, version=2)

# SQLite 逻辑库（全 51 表免 MySQL 建表）
from sqlalchemy import create_engine
from app.database import Base
engine = create_engine("sqlite:///test.db")
Base.metadata.create_all(engine)
```

## 边界

- SQLite fixture 用于**逻辑测试**（服务层/状态机/权限判断）；MySQL 方言 DDL、
  迁移链升级与真实 Milvus/LLM 属 `@pytest.mark.integration` 范围，跑真实 MySQL。
- 冒烟测试外的业务测试放 `tests/D/test_*.py`；backend 域的既有测试在 `backend/tests/`。
