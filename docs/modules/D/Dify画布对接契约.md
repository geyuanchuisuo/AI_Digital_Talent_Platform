# Dify 画布对接契约（T-D16，v1.0）

> 版本：v1.0　|　日期：2026-11-11　|　维护：D　|　上游：统一需求 V2.6 §5 F08、DEC-20261107-DLINE-CONFIRMED
> 实现：`backend/app/modules/workflows/dify_bridge/`（dsl / service / writeback）+ `workflows/router.py` 3 端点
> 对接目标：Dify **1.19.0**（T-D05 锁定 commit `8e689e868b63`）；DSL envelope 以其锁定源码实核为准（`api/constants/dsl_version.py` 的 `CURRENT_APP_DSL_VERSION="0.7.0"`、`make_app_dsl`、`WorkflowContentDict`）
> 上游模型：[工作流数据模型契约](工作流数据模型契约.md)（T-D14，版本锁定语义沿用）

## 1. 职责与验收对照

| 项 | 口径 | 本桥交付 |
|---|---|---|
| 导出 | 落库版本图 → Dify DSL（只读快照） | `GET /api/workflows/versions/{id}/dify-dsl` |
| 显式导入 | Dify DSL → 目标定义**新草稿版本**；已发布版本绝不触碰（版本锁定） | `POST /api/workflows/definitions/{id}/dify-import` |
| 对账 | DSL 与落库版本只读 diff 报告（不落库、不建版本） | `POST /api/workflows/versions/{id}/dify-reconcile` |
| 运行追踪回写 | 终态运行 → 已配置接收端（默认关，best-effort） | `dify_bridge/writeback.py` + `sync_run_status` 终态挂钩 |

全部端点管理侧门禁（复用 workflows `service.require_management`，与 T-D15 同口径）。

## 2. 映射约定（DifyBridge DSL v1）

- **envelope**：与 Dify 导出同形——`{version: "0.7.0", kind: "app", app{name/mode:"workflow"/icon/...}, workflow{graph{nodes,edges}, features:{}, environment_variables:[], conversation_variables:[], rag_pipeline_variables:[]}}`；features 空对象合法（Dify 侧 `validate_and_set_defaults` 补默认）。
- **节点 1:1**：本仓库六类域节点 → Dify 自定义类型节点；`node.id = node_key`、`data.type = "ait_<node_type>"`、`data.title = name`、`data.node_key` 冗余；`config_json/inputs_json/outputs_json` 原样放 `data.ait_config/ait_inputs/ait_outputs`；`position_json` 直映 `position`。**不注入合成 start/end**（触发经 run params，1:1 保证导出→导入图恒等）。
- **连线**：`branch`（approve/reject）→ `sourceHandle`（非分支出边 = `"source"`）；`condition_json` → `data.ait_condition`。
- **导入严格性（验收③）**：只接受 `ait_*` 节点且连线端点必须落在本图节点集内；画布上的异类节点（llm/if-else/code 等）与游离连线一律 **422 点名拒绝，不静默丢弃**——操作者须显式处理后再导入。导入经 `create_draft_version` 整图校验（节点白名单/分支规则/输入输出引用）后落新草稿。
- **对账**：两侧各自归一化（node_key 集合 + (source,target,branch) 集合）后比较；报告 `{identical, nodes{added/removed/changed(逐字段)}, edges{added/removed/changed}}`。Dify 画布编辑对未知字段（如 `ait_condition`）的保留程度以联调为准；对账不一致 → 操作者显式重导，即验收③语义。

## 3. 运行追踪回写（writeback）

- 载荷 schema `ait.dify.run-trace.v1`：`{schema, thread_id, definition{id,code}, version, trigger_type, status, error(≤300), finished_at, steps[{node_key, node_type, attempt, status, error(≤300), started_at, finished_at}]}`——不含业务敏感数据（步骤 output 不上送）。
- **门控**：环境变量 `DIFY_RUN_TRACE_URL` 缺省/空 = 不回写（skipped）；配置后 best-effort，任何失败只记日志（`app.dify_bridge`），**不阻塞、不回滚业务事务**。
- **挂点**：`workflows.service.sync_run_status` 每次成功**进入终态**（succeeded/failed/cancelled）后触发；`waiting` 不回写。等待态/运行中取消的直接 CAS 路径不经此函数，其回写口径待 Dify 端点联调时统一定义。
- **传输**：默认 `urllib` POST JSON（10s 超时）；`transport` 参数可注入（测试假传输）。

## 4. 验证与边界

- 测试：`tests/D/test_dify_bridge_t16.py` **14 例**（envelope/映射形状、导出→导入图恒等 + reconcile identical（验收②）、导入只建草稿不触碰发布版本（版本锁定）、异类节点/游离连线/未知 ait_ 类型/坏 envelope/空图 422 矩阵（验收③）、对账 drift 报告且不建版本、HTTP 门禁 403 矩阵、回写载荷形状/默认关/假传输/失败不抛、终态镜像触发与非终态不触发）；workflows 回归 6 文件 **68 例**全绿。
- 依赖：`pyyaml`（alembic 硬依赖，随既有迁移链必然在场；requirements.txt 正式登记归 A 依赖清单流程）。
- **未验证（联调边界）**：①真实 Dify 1.19.0 画布「可见可改可回传」（验收①的画布半边）——Docker/WSL 本轮未运行，待环境恢复后用锁定版本演练（console API 契约见 UPD-D-20261108-041500：Base64 密码/HttpOnly Cookie/CSRF）；②回写接收端在 Dify 侧的落点与鉴权；③Dify 画布 UI 保存对 `ait_*`/`ait_condition` 自定义字段的保留程度（对账机制兜底）。
- **PRD D10 剩余缺口**：前端可视化画布编辑器（`workflows/page.tsx` 当前 JSON 整图输入框替换为节点/连线画布）——属前端单元，另行交付。
