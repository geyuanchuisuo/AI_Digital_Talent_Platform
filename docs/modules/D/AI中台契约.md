# AI 中台契约（T-D20）

版本：v1.0（2026-11-09）。需求依据：PRD §2.7、统一需求 D-35 中台管理矩阵、原 PRD 执行跟踪表 D12（D/A）。
前置：T-D04 引擎（运行看板数据源）；与 T-D15 工作流生命周期、T-D18 任务调度共用框架。

## 1. 表（迁移 D_0011 挂 D_0010，80 表基线）

| 表 | 职责 | 约束 |
|---|---|---|
| `ai_providers` | 模型供应商：name/provider_type/base_url/model/api_key/enabled/is_default/timeout | `uk_ai_providers_name`(name)；同时刻仅一个 is_default（service CAS） |
| `ai_prompt_versions` | Prompt 版本库：name+version 唯一，发布后不可变 | `uk_ai_prompt_versions`；`idx_aip_name_status` |
| `ai_call_logs` | LLM 调用日志（llm_client 逐次埋点）：provider/model/purpose/result/latency/tokens/error | `idx_aic_created`；`idx_aic_purpose` |

## 2. 验收对照

| 验收项 | 实现与证据 |
|---|---|
| ①密钥不出现在任何接口响应/日志 | `to_provider_dict` 序列化**无 api_key 字段**（仅 has_api_key 布尔）；测试断言 `sk-secret-123` 不出现在任何 dict 字符串化结果中 |
| ②供应商启停即时生效 | `resolve_default_provider` 每次实时读库；llm_client `client` property 每次访问重查中台配置；停用默认 → 解析 None（回退 .env 兜底）→ 恢复启用需显式重设默认（不自动复活） |
| ③用量/错误看板数字可回查调用日志 | `usage_summary`（days 1–90，按 purpose×result 聚合：calls/error_rate/avg_latency/tokens）与 `list_call_logs` 明细**同源 ai_call_logs**；测试断言明细行数 == 聚合 total_calls |

## 3. 智能体口径（不另造第三套编排）

一期智能体以**受控工作流定义**为载体：注册/发布/退役/回滚复用 T-D15 生命周期（`/api/workflows`），运行看板 `GET /api/ai-platform/agents/runs` 为 workflow_runs 只读聚合（按定义 × 触发类型 × 状态，days 窗口）。中台不新建独立 Agent 表。

## 4. API（`/api/ai-platform`，全部管理侧）

| 端点 | 说明 |
|---|---|
| GET/POST `/providers` | 列表（不回显密钥）/ 新建（provider_type 白名单 openai_compatible；timeout 1–600） |
| PATCH `/providers/{id}` | 部分更新；api_key 传 null 不动、空串清除 |
| POST `/providers/{id}/enabled` | 启停（停用默认一并取消默认身份） |
| POST `/providers/{id}/default` | 设默认执行面（停用态 422；CAS 全表唯一） |
| GET/POST `/prompts` | 版本列表（name 过滤、版本倒序）/ 新建草稿（name 内 max+1） |
| PATCH `/prompts/{id}` | 替换草稿内容（发布后 409） |
| POST `/prompts/{id}/publish` | 发布（同 name 其他 published 自动退役——唯一生效版） |
| GET `/usage` | 用量/错误率看板（days 1–90） |
| GET `/call-logs` | 明细回查（days/purpose/result/limit≤500 过滤） |
| GET `/agents/runs` | 运行看板（只读聚合） |

## 5. llm_client 联动

- `_chat_raw` 成功/失败两路 `_record_call()` 埋点（审计**旁路**：落库失败仅记日志不抛，不阻塞业务调用——测试 `test_record_call_is_audit_bypass`）。
- `provider_config_factory`：get_llm_client 每次构造时绑定中台读取函数；`client` property 每次访问重查（启停/切换即时生效）；中台库不可用回退 `.env`（`provider_name="env_default"`）。
- `with_purpose(purpose)`：调用方按业务用途打标（parse/match/nl2sql/…），purpose 落日志索引列；现有 8 处调用点迁移到 with_purpose 属后续逐域接线（本片不改他域文件，默认 purpose=unspecified 已可统计）。
- Prompt 库与 `core/prompts` 文件加载器**并存**：`resolve_published_prompt(name)` 供执行面优先读中台、无则回退文件（B/A 业务提示词文件不动）。

## 6. 测试与边界

- `tests/D/test_ai_platform_t20.py` 12 例：密钥不回显/403/白名单校验、启停与默认解析即时生效矩阵、默认唯一 CAS、部分更新与密钥清除、Prompt 生命周期（草稿可改/发布 409/唯一生效版/自动退役）、看板聚合与明细同源、审计旁路、days 边界、运行看板聚合。
- 表基线断言：`app/models/__init__.py` 77→80；`backend/tests/test_models_structure.py` 补三表预期。
- **未含**：前端中台管理页（供应商/Prompt/看板 UI，随前端批次交付，菜单入口待排）；现有 8 处 LLM 调用点 purpose 打标（逐域接线，本片不修改他域文件）；真实供应商连通实测（待 Key/环境）；`test_models_structure` 三处预存在失败（C_0006 归属 C，见 T-D19 记录）。
