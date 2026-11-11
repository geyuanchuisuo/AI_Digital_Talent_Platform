# D 模块可自主推进待办清单

> 版本：v1.2　|　日期：2026-11-11　|　身份：MASTER 审核整理（面向 D 开发者）　|　分支基线：`Lon`（`master`/`gitea/master` 已全量同步，与 `Lon` 同一提交）
> 需求基线：统一需求 **V2.6（冻结）** + 原 PRD 必达；任务入口：[原 PRD 统一执行跟踪表](原PRD统一执行跟踪表.md)
> 配套文档：[开发计划-todolist](开发计划-todolist.md)（历史计划）、[问数切片契约](问数切片契约.md)、[工作流引擎契约](工作流引擎契约.md)、[缓存限流契约](缓存限流契约.md)、[Dify画布对接契约](Dify画布对接契约.md)、[指标目录](指标目录.md)、[ETL说明](ETL说明.md)
> **本文用途**：把 D 模块当前**可以不等外部模块回传、不依赖未恢复环境即可开工**的开发与测试项单独列出，供开发者直接领取。每项均含：目标、现状、具体子任务、涉及文件、验收标准、测试建议、影响与风险、工作量粗估。
> **标记约定**：`✅` 已有代码/测试证据；`⬜` 待做；`⏳` 等外部；`⚠️` 有风险需评审。× 只表示代码实现，不表示接入/联调/业务验收。

---

## 0. 使用方式

1. **先读**：根 `AGENTS.md` → `tasks/D.md` → 对应契约文档 → 本文对应条目。
2. **领取**：从 §1 中选一项，确认前置文件与测试命令；不要同时改同一文件的多项。
3. **实现**：按子任务逐条完成；新增/修改代码必须带正反例测试（越权/幂等/降级至少覆盖一类）。
4. **验证**：运行条目「测试建议」中的命令；真实环境未恢复的写「未验证」，不得编造通过。
5. **登记**：在 `docs/updates/D/` 新增 `YYYYMMDD-HHMMSS-主题.md`，记录实际命令、结果、未测项、风险。
6. **提交**：按 §5 流程，先推 `Lon`，再申请 `master` 集成；不要直接推 `master`。

---

## 1. 可立即自主开工（无外部阻塞）

### T-D-SELF-01　D15 受控 SQL 执行与审计补强　【P0 建议】

- **目标**：把自由问数的 SQL 安全边界做深，确保任何未审/越权/危险 SQL 都不会执行，审计可回溯。
- **现状**：`analytics/nl2sql/{validator,executor,service}.py` + `nl2sql_audit` 表（D_0005）已实现，31 例越权/超时/上限/禁写测试通过；真实生成与行权限全链未验收。
- **可自主推进的子任务**：
  - [x] `✅` 补全 SQL 校验器边界（2026-11-11）：新增 `INTO`/`EXPLAIN`/`ANALYZE`/`PERFORM` 禁写关键词；修复 `_has_top_level_limit` 子查询 LIMIT 误判（改为括号深度扫描）；修复 `_walk_tables` 子查询别名被当表名；补注释/UNION 注入回归测试。`COPY`/`FOR UPDATE`/CTE 内嵌写/动态 `EXECUTE` 原已覆盖；`pg_sleep`/`generate_series` 滥用由执行层 `statement_timeout` + 行上限兜底，非校验器职责。
  - [~] `⚠️` 行级权限改写（2026-11-11 部分）：当前 service 仅允许 `hr`（`can_view_all_persons=True`），personal/未知角色已 403（补不依赖 PG 反例 `test_personal_denied_403_no_pg`）。**行范围 SQL 注入未做**：需先决定哪些角色可用 nl2sql、扩展 `get_account_scope` 支持 `dept_manager` 等角色、并设计 SQL 改写方案（直接注入 `WHERE department_id IN (...)` 对任意 SQL 不可靠，可能需 RLS 或子查询包装）；当前副本表是否有 `department_id` 也需核对。
  - [x] `✅` 超时与取消（2026-11-11）：`statement_timeout` + SQL 级 LIMIT 双重兜底原已实现；执行器异常路径已有 `conn.rollback()` + `with` 块保证连接释放；补 timeout/readonly/error 三例审计状态测试（不依赖真实 PG，monkeypatch 执行器），验证 stage 与 refused_reason 落库。
  - [x] `✅` 审计绑定（2026-11-11）：`nl2sql_audit` 新增 `executed_sql`（实际执行 SQL，保留 `generated_sql` 原始输出不被覆盖）与 `truncated`（截断标志）两列；service 在 validated/executed 阶段分别写入；新增迁移 `D_0013_nl2sql_audit_fields`；补 SQLite 单测验证字段分离与截断标志。审计与快照/反馈关联：nl2sql 不创建 `analytics_query_snapshots`（快照仅用于登记指标），反馈对象是快照而非 nl2sql 审计，故本项不涉及跨表关联。
  - [x] `✅` 拒答一致性（2026-11-11）：生成器新增启发式：非 SQL 文本（空/自然语言/歧义）统一返回 None，不再当 SQL 猜测；仅 `SELECT`/`WITH` 开头才交给校验器。补 5 例测试（非 SQL 文本拒答 + CTE 合法不误伤）。
- **涉及文件**：`backend/app/modules/analytics/nl2sql/**`、`tests/D/test_analytics_td09.py`（或新建 `test_nl2sql_security.py`）。
- **验收标准**：
  - 危险 SQL 负例矩阵全绿，且**不执行**（数据库无副作用、审计有留痕）；
  - 超时/上限触发实测；
  - 审计记录可与查询快照互相回查；
  - 拒答路径不返回部分结果。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_analytics_td09.py -q`；新增负例单独文件并跑全量 `tests/D/`。
- **影响与风险**：这是自由问数的核心安全边界；改动校验器可能误伤合法只读 SQL，必须保留「引号内关键词不误伤」反例。
- **工作量**：M（3–5 人日）。

---

### T-D-SELF-02　D22 重排接入框架与调用契约　【P1 建议】

- **目标**：在 D 自有边界内完成重排适配层的**接入框架**，等 B 的 F06 检索改造时可直接对接，不改变现有检索结果。
- **现状**：`core/rerank_client.py` 三模式（off/api/local）+ 20 例测试 + 本地 bge-reranker 真实冒烟已通过；**接入框架完成，等 B 的 F06 检索改造对接**。
- **可自主推进的子任务**：
- [x] `✅` 定义统一 `Reranker` 接口：`rank_candidates(query, candidates, mode=None, top_n=None)`，返回 `RerankOutcome`（候选 + 排序 + 是否重排 + 降级原因）。
- [x] `✅` 实现空结果、候选不足、模型失败、超时、分数不可比（跨模式）时的**明确降级**（保持原顺序，不静默重排）。
- [x] `✅` 默认关闭保护：`RERANK_MODE=off` 时调用链完全不触发模型；补「关闭时不改变结果」断言。
- [x] `✅` 接入契约文档：[`重排接入契约.md`](重排接入契约.md)——调用方需传 `query`、候选 `id/score/text`、模式、超时、降级策略。
- [x] `✅` 单元测试：假 reranker 注入，验证排序、截断、失败回退、配置缺失报错。
- **涉及文件**：`backend/app/core/rerank_client.py`、`docs/modules/D/` 新增重排契约、`tests/D/test_rerank_d22.py`。
- **验收标准**：
  - 三模式行为与配置一致；
  - 关闭模式零模型调用；
  - 失败/超时降级不改变候选集合；
  - 契约文档可被 B 直接消费。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_rerank_d22.py -q`；新增接口测试。
- **影响与风险**：接入主链后可能改变候选排序与召回数量，需 B 用评测集验证准确率；D 侧只做框架，不宣称检索已重排。
- **工作量**：M（2–4 人日）。

---

### T-D-SELF-03　D17 SSE 权限、重连与终态边界　【P1 建议】

- **目标**：把任务进度 SSE 做成安全、可重连、终态明确的实时通道。
- **现状**：`core/tasks/events.py` 已实现并挂载 `main.py`；本次补齐权限/重连/终态/降级边界（14 例用例通过），前端评估结论为复用 `usePolling`。
- **可自主推进的子任务**：
  - [x] `✅` 权限反例：无权限订阅他人任务 403（JSON，不进流）；任务转移/账号停用后流内明确 `error` 事件并关闭。
  - [x] `✅` 重连语义：`Last-Event-ID` 状态指纹去重；断线期间中间状态不逐条重放（读当前快照）；未收到终态时重连补发。
  - [x] `✅` 终态一致性：任务成功/失败/取消后订阅返回终态；终态事件每连接只发一次。
  - [x] `✅` 降级：快照读取失败（DB 不可用等）明确 `unavailable` 事件并关闭，不伪造进度。
  - [x] `✅` 前端断线重连与错误提示：评估结论 **复用 `usePolling`**（出错不停自愈 + `onError` 提示 + 终态停止），不为任务进度另引 SSE 客户端；SSE 端点保留为服务端能力。
- **涉及文件**：`backend/app/core/tasks/events.py`、`tests/D/test_tasks_events_sse.py`（本次新建）、前端 `lib/usePolling.ts`（评估复用，未改）。
- **验收标准**：
  - 无权限订阅 403；
  - 断线重连不丢终态、不越权；
  - 终态只发一次；
  - 不可重放场景有明确降级说明。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_analytics_slice.py -q -k "sse"`。
- **影响与风险**：涉及实时权限；必须明确「不承诺逐事件历史重放」，避免用户误解。
- **工作量**：S–M（2–3 人日）。

---

### T-D-SELF-04　D24 问数反馈权限反例与浏览器验收准备　【P1 建议】

- **目标**：补全反馈入口的组织/字段权限边界，并准备浏览器验收用例。
- **现状**：`feedback/service.py` + `analytics_feedback` 表（D_0006）+ 2 端点 + 前端入口已实现；本次补全组织/角色反例、快照状态边界、审计字段与前端反例用例（后端 14 例、前端 13 例通过）。
- **可自主推进的子任务**：
  - [x] `✅` 组织隔离反例：跨部门角色（dept_manager）/非创建者反馈一律 403，文案不泄露快照是否存在。
  - [x] `⚠️` 字段级权限：**当前设计不适用**——列表仅管理侧可见，快照 `result_json` 为聚合指标结果，不含个人敏感字段；已加敏感字段零泄露守护用例，待 A 字段范围契约到位后细化。
  - [x] `✅` 快照状态边界：不存在 404；无解读（`result_json` 无 interpretation）仍可反馈；模型无「已删除/过期」字段，不适用。
  - [x] `✅` 幂等与审计：重复标记同 id 不新增；反馈行自带 `created_by`/`created_at`；反馈不改快照与统计结果。
  - [x] `✅` 前端验收用例：按钮权限（无快照禁用）、错误提示、重复提交（提交中忽略二次点击 + 成功后关弹窗）、列表 403 隐藏均已覆盖（vitest 13 例）。
- **涉及文件**：`backend/app/modules/analytics/feedback/**`、`frontend/src/app/(main)/analytics/page.tsx`、`frontend/src/app/(main)/analytics/page.test.tsx`、`tests/D/test_analytics_td12.py`。
- **验收标准**：
  - 跨组织/字段越权全 403 且不泄露；
  - 反馈与快照、审计强关联；
  - 反馈不改统计结果。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_analytics_td12.py -q`；浏览器验收待真实账号。
- **影响与风险**：字段级权限依赖 A 的最终范围契约；D 可先补通用反例，契约到位后补细化。
- **工作量**：S（1–2 人日）。

---

### T-D-SELF-05　D03 ETL 敏感排除、水位与幂等测试　【P1 建议】

- **目标**：把 PostgreSQL 只读副本的 ETL 做成可核对、可重跑、敏感可控。
- **现状**：`scripts/D/etl_pgsql.py` + `deploy/postgres/` 已实现，历史真实库演练通过；本轮未复测，敏感排除/水位/幂等/延迟未系统化。
- **可自主推进的子任务**：
  - [ ] `⬜` 敏感字段清单固化：`resume_files`/`parsed_resumes`/`talent_profiles` 不进副本；JD 全文、简历原文、理由快照整体排除。
  - [ ] `⬜` 增量水位：`ON CONFLICT` 幂等；补主键冲突、部分失败、延迟场景测试。
  - [ ] `⬜` 核对工具：行数、关键字段哈希、延迟阈值（≤15 分钟）自动核对。
  - [ ] `⬜` 降级：PG 不可达时回退主库 MySQL 直查，并记录降级口径。
  - [ ] `⬜` 授权一致性：副本数据与主库行范围一致，不引入越权可见字段。
- **涉及文件**：`scripts/D/etl_pgsql.py`、`deploy/postgres/**`、`docs/modules/D/ETL说明.md`、`tests/D/test_etl_pgsql.py`。
- **验收标准**：
  - 全量/增量跑通且行数一致；
  - 敏感字段零同步；
  - 重复执行无副作用；
  - 延迟核对有报告。
- **测试建议**：真实 MySQL→PG 环境恢复后跑 `tests/D/test_etl_pgsql.py`；当前环境不可达会自动跳过，需如实记录。
- **影响与风险**：ETL 停/慢只影响问数时效，不影响业务主库；但错误的水位可能导致数据重复或缺失。
- **工作量**：M（2–4 人日）。

---

### T-D-SELF-06　D21 本地 Embedding 系统化验收　【P1 建议】

- **目标**：把本地 Embedding 从「冒烟通过」提升到「可部署验收」。
- **现状**：`embedding_client.py` 支持 api/local 双模式，本地 bge-large-zh-v1.5 真实推理 1024 维；批量/查询/失败/凭据隔离未系统化。
- **可自主推进的子任务**：
  - [ ] `⬜` 批量/单条查询测试：空输入、超长输入、特殊字符、并发调用。
  - [ ] `⬜` 维度强校验：非 1024 维明确报错，不写入 Milvus。
  - [ ] `⬜` 模式切换：`EMBEDDING_MODE=api/local` 即时生效，配置缺失/模型缺失有明确错误。
  - [ ] `⬜` 失败降级：本地模型不可用时明确回退或拒绝，不静默零向量。
  - [ ] `⬜` 凭据隔离：API key 不进日志、不进响应、不进审计明文。
- **涉及文件**：`backend/app/core/embedding_client.py`、`tests/D/test_embedding_unit.py`、`tests/D/test_embedding_milvus_td23.py`。
- **验收标准**：
  - 批量/查询向量维度一致；
  - 失败路径明确；
  - 无密钥泄露；
  - 与 Milvus collection 冻结维度一致。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_embedding_unit.py tests/D/test_embedding_milvus_td23.py -q`。
- **影响与风险**：本地模型会增加启动内存与 CPU 开销，需在部署文档写明；GPU 环境另装对应 torch。
- **工作量**：S–M（2–3 人日）。

---

### T-D-SELF-07　D16/D23 自由问数图表规格与报告导出补强　【P2 建议】

- **目标**：让自由问数结果也能生成图表规格，并与 DOCX/PDF 同快照导出对齐。
- **现状**：已完成。`charts.build_nl2sql_chart(columns, rows)` 按结果形态确定性推断绘图列（无行→none；2 列→bar；1 列 1 行→number；其余→none+原因），`nl2sql_query` 成功响应新增 `chart` 字段，与登记指标同一规格契约（`chart_render` 可直接渲染）。新增 `tests/D/test_nl2sql_chart_d23.py` 24 例（含 HTTP 层 3 例）；契约见 [问数切片契约](问数切片契约.md) §12。
- **可自主推进的子任务**：
  - [x] `✅` 定义自由问数 → 图表规格契约（2026-11-11）：支持类型、绘图列推断、行数上限 20、空值/截断规则；契约入 `问数切片契约.md` §12。
  - [x] `✅` 图表规格接入报告渲染（2026-11-11）：与登记指标共用 `chart_render.py`；测试验证 bar/number 可渲染、`none` 不渲染。
  - [x] `✅` 表格/图表一致性测试（2026-11-11）：`chart.data` 逐点等于 `rows`，未截断时 Σseries == Σ 数值列。
  - [x] `✅` 长表分页、中文字体、负值基线、截断标注补强：已由 `test_report_charts_td16.py` 11 例覆盖（PDF repeatRows 分页/CJK 字体/负值基线/30 项截断）。
  - [x] `✅` 下载授权与同快照断言：已由 `test_reports_td11.py` 覆盖（`test_export_does_not_recompute_metrics` 哨兵 + `test_export_permission_matrix` 权限矩阵）。
- **涉及文件**：`backend/app/modules/reports/**`、`backend/app/modules/analytics/charts.py`、`frontend/src/app/(main)/analytics/page.tsx`、`tests/D/test_reports_chart_d16.py`。
- **验收标准**：
  - 图表数据与表格一致；
  - 中文/长表可读；
  - 导出不重新问数；
  - 越权下载 403。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_reports_chart_d16.py tests/D/test_reports_export_d11.py -q`。
- **影响与风险**：D14 未接 Vanna 前不能宣称「完整自由问数图表已验收」；本项只做规格与渲染。**新增口径**：nl2sql 图表为服务端能力，无前端页面消费；报告导出管线（§11）暂不直接消费 nl2sql 结果（其不创建快照）。
- **工作量**：M（3–5 人日）。

---

### T-D-SELF-08　D05/D08 工作流状态机补测　【P1 建议】

- **目标**：把流程定义、版本、重试、取消、恢复的状态一致性做扎实。
- **现状**：`workflows/{models,service,router}.py` + 78 例测试通过（含本次新增 14 例边界补测）；真实 MySQL/浏览器/三类事件未联调。
- **可自主推进的子任务**：
  - [x] `✅` 版本锁定：运行中实例不受新版本/废弃影响；发布版本拒绝修改。
  - [x] `✅` 审核隔离：提交人自审 403；审核人与提交人分离（含驳回后重新提交）。
  - [x] `✅` 回滚语义：回滚到已停用版本重新发布；回滚后新运行取目标版本；pending_review 目标拒绝。
  - [x] `✅` 重试幂等：失败重试不重复副作用；已成功节点不重执行；版本废弃后重试仍用钉死版本。
  - [x] `✅` 取消边界：取消后迟到回执拒绝；并发取消后来者 409。
  - [x] `✅` 重启恢复：孤儿 running 全部恢复 failed 不重复触发；等待态/终态不被重置。
  - [x] `✅` 无执行主体/重复调度/过期调度边界（含无效执行主体如实失败）。
- **涉及文件**：`backend/app/modules/workflows/**`、`tests/D/test_workflow_state_machine_t08.py`（新增）、`tests/D/test_workflow_*`。
- **验收标准**：
  - 状态机全路径覆盖；
  - 无重复副作用；
  - 恢复/取消语义明确；
  - 权限反例全绿。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_workflow_engine.py tests/D/test_workflow_schedule_t18.py tests/D/test_workflow_retry_cancel_t18.py tests/D/test_workflow_nodes_t17.py -q`。
- **影响与风险**：工作流状态机改动可能影响运行中实例；需保证向后兼容，不重置等待态。
- **工作量**：M（3–5 人日）。

---

### T-D-SELF-09　D20 数字员工定时触发接线（纯站内）　【P2 建议】

- **目标**：把数字员工三类只读场景接入定时触发，仍不依赖 IM 平台。
- **现状**：已接线。`schedule.py` 新增数字员工定时（独立开关/间隔/场景清单/通知收件人四键，默认关闭），`scan_once` 拆分为工作流与数字员工两段，执行走 `digital.service.run_task` 受控系统入口（`actor=None` 且 `triggered_by=None`）；`router.py` PUT `/schedule-settings` 支持 `digital` 嵌套配置。348 例 D 域基线通过（含本次新增 17 例定时专项）；真实 MySQL/Redis/浏览器/IM 未联调。
- **可自主推进的子任务**：
  - [x] `✅` 定时任务与数字员工 collector 的受控接线：开关、执行主体、审计。
  - [x] `✅` 默认关闭验证：未启用时不触发外发/通知（零副作用）。
  - [x] `✅` 幂等与限速：同一周期重复触发不重复落库/通知（窗口幂等键 + 到期判定双层）。
  - [x] `✅` 站内通知闭环：结果入库 + 通知指定管理员，不调用 IM API（无站外外发）。
- **涉及文件**：`backend/app/modules/workflows/schedule.py`、`backend/app/modules/workflows/router.py`、`tests/D/test_digital_schedule_t09.py`（新增）、`tests/D/test_workflow_schedule_t18.py`、`tests/D/test_digital_t21.py`。
- **验收标准**：
  - 定时启用后按周期触发；
  - 关闭时零副作用；
  - 重复触发幂等；
  - 无站外外发。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_digital_schedule_t09.py tests/D/test_digital_t21.py tests/D/test_workflow_schedule_t18.py -q`。
- **影响与风险**：定时触发会增加后台负载；默认关闭并可审计（四键 + `updated_by`）；数字员工与工作流定时互不干扰（各自开关）。
- **工作量**：S–M（2–3 人日）。

---

### T-D-SELF-10　D25 真实 LLM 解读验收准备　【P2 建议】

- **目标**：把 D25 从「隔离测试通过」推进到「真实模型 + 真实数据验收」。
- **现状**：`insight.py` 的 `build_interpretation_with_llm()` 已接线；**真实网关冒烟通过（2026-11-11 23:10，`agnes-3.0-flash`，`generator=rule+llm` + 因果守卫零命中，见 [记录](../../updates/D/20261111-231000-D25basis契约修正与真实网关冒烟.md)）**；默认开关关闭；真实 MySQL 数据集与浏览器未验。
- **可自主推进的子任务**：
  - [x] `✅` 真实网关冒烟：`rule+llm` 输出、purpose=`insight_llm_enhance` 审计、失败回退（basis 契约修正：事实序号/key 双向解析，15 例测试）。
  - [ ] `⬜` 开关与配置：`ANALYTICS_LLM_INSIGHT_ENABLED` 显式开启/关闭行为。
  - [ ] `⬜` 数据一致性：解读只引用真实事实段，不虚构数字；因果守卫违规整段丢弃。
  - [ ] `⬜` 浏览器验收：解读段在指标页正确渲染 fact/inference 标签。
  - [ ] `⬜` 性能边界：LLM 超时/限流时的降级延迟可接受。
- **涉及文件**：`backend/app/modules/analytics/insight.py`、`frontend/src/app/(main)/analytics/page.tsx`、`tests/D/test_analytics_d25.py`。
- **验收标准**：
  - 真实模型成功/失败分别可追溯；
  - 失败回退规则式；
  - 前端正确区分事实/推断。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_analytics_d25.py tests/D/test_analytics_td10.py -q`；真实网关需 Key。
- **影响与风险**：LLM 输出不确定，必须始终保留规则段；不得把 LLM 推断写成事实。
- **工作量**：S（1–2 人日，不含真实 Key 等待）。

---

### T-D-SELF-11　D18 通知事件逐类闭环核对　【P2 建议】

- **目标**：把通知从「基础已实现」推进到「逐事件权限/幂等/重试闭环」。
- **现状**：已完成逐类核对。契约 §7「生产方接线清单」：C 培训 6 类 + D 数字员工 1 类已接业务调用；A 招聘/面试 4 类与 D 工作流审批/失败 2 类、缺口预警 1 类**仅有生产方函数、未接业务调用**（如实登记，未静默补齐）。新增 `tests/D/test_notifications_d18.py` 23 例逐类核对（14 枚举幂等重入/读取隔离矩阵/3 类失败重试/键形态/清单外拒绝/封闭集/接线现状）；D 域基线 371 passed, 16 skipped。
- **可自主推进的子任务**：
  - [x] `✅` 逐事件接收权限核对：个人只收本人、越权 403（14 类逐类矩阵）。
  - [x] `✅` 幂等键逐类验证（单/多收件人规则）。
  - [x] `✅` 失败重试：`deliver` 失败 → `failed`+reason → `retry_failed`（生产方逐类）。
  - [x] `✅` 生产方接线清单：标注哪些事件已有生产方、哪些待 D07。
- **涉及文件**：`backend/app/modules/notifications/**`、`tests/D/test_notifications_d18.py`（新增）、`tests/D/test_notifications.py`、`docs/modules/D/通知契约.md`。
- **验收标准**：14 类事件逐类有发信与越权反例；生产方缺口明确。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_notifications_d18.py tests/D/test_notifications.py -q`。
- **影响与风险**：通知权限涉及个人隐私；必须按 AccountScope 读取隔离。**新增风险登记**：7 类事件（A 4 + D 3）未接业务调用，不会产生通知——不得以「生产方函数存在」宣称已可用。
- **工作量**：S（1–2 人日）。

---

### T-D-SELF-12　D26 网页图表一致性补强　【P2 建议】

- **目标**：保证登记指标的表格与图表一致，截断/空结果可读。
- **现状**：已完成补强。`charts.py` 新增**数值守卫**（绘制窗口内出现 None/bool/字符串等非数值 → `none`+可读原因，不渲染误导柱高；窗口外坏值不影响已绘制的点）；前端 bar 图在 `truncated_series` 时显示「仅显示前 N 项（共 M 项，其余未绘制）」（此前只置标记、页面不显示，属静默截断）。新增 `tests/D/test_analytics_charts_d26.py` 25 例 + `page.test.tsx` 4 例；契约 §9.2 同步口径。未浏览器验证（jsdom）。
- **可自主推进的子任务**：
  - [x] `✅` 表格/图表一致性测试：逐点核对（5 个已登记绘图列组合逐点相等）。
  - [x] `✅` 截断/空结果/单序列场景（19/20/21 边界、空结果可读、单序列仍出柱图）。
  - [x] `✅` 非登记维度拒绝与提示（none+原因；服务层白名单 422 已在 td10）。
  - [x] `✅` 前端图表组件回归（截断提示可见、none 不渲染图、单序列渲染）。
- **涉及文件**：`backend/app/modules/analytics/charts.py`、`frontend/src/app/(main)/analytics/page.tsx`、`tests/D/test_analytics_charts_d26.py`（新增）、`tests/D/test_analytics_td10.py`、`docs/modules/D/问数切片契约.md`。
- **验收标准**：表格与图表数值一致；空结果不渲染误导图形。
- **测试建议**：`backend/.venv/Scripts/python.exe -m pytest tests/D/test_analytics_charts_d26.py tests/D/test_analytics_td10.py -q`；前端 `npx vitest run "src/app/(main)/analytics/page.test.tsx"`。
- **影响与风险**：图表一致性是管理层信任基础；必须逐点核对。**新增行为**：非数值不再绘图（原为渲染误导柱高），如后续出现文本型数值列需改为先转数值再绘。
- **工作量**：S（1–2 人日）。

---

## 2. 需恢复环境后验收（代码已有，仅缺真实环境）

> 这些条目**不是没有代码**，而是当前 Docker/Redis/PostgreSQL/Dify 未运行，无法做真实验收。验收用例可先写，执行需环境恢复。

| 条目 | 现状 | 需恢复的环境 | 解除后的动作 |
|---|---|---|---|
| D01 Redis 缓存 | ✅ `core/cache/**` + 20 例 | 真实 Redis | 隔离键空间、故障边界、命中率 |
| D02 API 限流 | ✅ 中间件 + 13 例 | 真实 Redis + 客户模式 | 429/403、故障降级、隔离键空间 |
| D03 ETL | ✅ 脚本 + 历史演练 | MySQL + PostgreSQL | 敏感排除、水位、幂等、延迟 |
| D08 工作流运行 | ✅ 51 例 | 真实 MySQL + 浏览器 | 运行链、定时、恢复 |
| D09/D10 Dify | ✅ DSL 桥 + 14 例 | Dify 1.19.0 + 前端画布 | 画布往返、回写、权限 |
| D11 Skills | ✅ 10 例 | 真实 MySQL + 浏览器 | 版本引用、停用行为 |
| D12 AI 中台 | ✅ 12 例 + 前端 | 真实模型 + 浏览器 | 供应商连通、用量看板 |
| D19 工作台 | ✅ 20 例 + 前端 | 真实 MySQL + 双角色浏览器 | 授权数据、数量回查 |
| D21 Embedding | ✅ 本地冒烟 | 真实 API/模型 | 批量、维度、失败、凭据隔离 |
| D24 反馈 | ✅ 9 例 + 前端 | 真实浏览器 | 组织权限、跨部门反例 |

---

## 3. 外部依赖阻塞（暂不开工）

| 条目 | 等待什么 | 提供方 | 解除条件 |
|---|---|---|---|
| D07 三类事件 | B 解析、知识入库生产方接线 | B | B 回传事件契约并联调 |
| D13 指标目录 | B 卡 2、C 卡 4/5/6 口径 | B / C | B/C 在契约中补字段、公式、时间、去重键 |
| D14 Vanna 原生 | Vanna 环境 + 模型 + PgSQL 契约 | D 自有但需环境 | 真实 Vanna 部署 + Key |
| D19 缺口告警联动 | D07 三类事件 | D/B/C | D07 完成 |
| D22 检索主链 | B 的 F06 检索改造 | B | B 接入 Reranker 接口 |
| D25 真实 LLM | 真实网关 Key | 用户/D | Key 到位后冒烟 |

---

## 4. 建议执行顺序

1. **T-D-SELF-01 D15 受控 SQL 安全边界**（P0，安全底线，可立即开工）。
2. **T-D-SELF-02 D22 重排接入框架**（P1，D 自有框架，等 B 接入）。
3. **T-D-SELF-03 D17 SSE 边界**（P1，实时权限）。
4. **T-D-SELF-08 D05/D08 工作流状态机补测**（P1，可靠性）。
5. **T-D-SELF-04 D24 反馈权限反例**（P1，可并行）。
6. **T-D-SELF-05 D03 ETL 测试**（P1，环境恢复后执行）。
7. **T-D-SELF-06 D21 Embedding 验收**（P1，环境恢复后执行）。
8. ~~**T-D-SELF-07 D16/D23 图表规格**（P2）~~ 已完成（2026-11-11）。
9. ~~**T-D-SELF-09 D20 定时接线**（P2）~~ 已完成（2026-11-11，待提交）。
10. **T-D-SELF-10 D25 真实 LLM 验收**（P2，等 Key）。
11. ~~**T-D-SELF-11 D18 通知闭环**（P2）~~ 已完成（2026-11-11，待提交）。
12. ~~**T-D-SELF-12 D26 图表一致性**（P2）~~ 已完成（2026-11-11，待提交）。

> 并行建议：T-D-SELF-01 / 02 / 03 / 04 可并行；T-D-SELF-05 需环境；T-D-SELF-06 / 10 可离线补用例、真实环境验收待恢复。已完成：T-D-SELF-01/02/03/04/06/07/08/09/10/11/12；剩余可开工：无（T-D-SELF-05 待真实 MySQL+PG；其余阻塞项见 §3）。

---

## 5. 验证与交付规范

- **测试通过是推送前提**：每项完成至少运行对应「测试建议」命令；真实环境未恢复写「未验证」。
- **范围检查**：提交前 `python scripts/check_scope.py --staged`；整合模式会提示跨归属路径。
- **交接登记**：每项在 `docs/updates/D/` 新增记录，含实际命令、结果、未测项、风险、下一步。
- **契约同步**：涉及接口/表结构变化，同步 `docs/modules/D/` 对应契约。
- **同步流程**：
  1. `git -c credential.interactive=false fetch gitea --prune`
  2. 核对 `gitea/master` 与本地 `Lon` 分叉
  3. 将 `gitea/master` 合入本地 `Lon`，冲突逐段保留双方内容
  4. 重跑受影响测试与范围检查
  5. `git push gitea Lon`
  6. 申请 `master` 集成（需用户明确授权）
- **冲突处理**：必须说明冲突位置、双方内容、保留/合并了什么、影响、建议；不得整段 `ours/theirs` 覆盖。

---

## 6. 当前基线快照（2026-11-11 22:45 刷新）

- 本地分支：`Lon`；`master` 与 `gitea/master` 已全量同步（与 `Lon` 同一提交）
- 本批新增（已提交并集成到 `master`，条目级轻量测试通过；集成前定向回归 146 passed / 8 skipped）：
  - T-D-SELF-06 D21 Embedding 单元测试补强（`test_embedding_unit.py` 18 例）
  - T-D-SELF-10 D25 解读隔离用例补强（`test_analytics_d25.py` +9 例）
  - T-D-SELF-07 D16/D23 自由问数图表规格（`test_nl2sql_chart_d23.py` 24 例 + 契约 §12）
- D 域基线（**最近一次经批准的全量运行**，2026-11-11 22:05 @ `1f035a0`）：`pytest tests/D -q` → 396 passed, 16 skipped
- 本批**未跑全量**（用户要求全量测试需批准）；新增用例仅按条目做轻量运行，计数不含入上述 396
- 未执行：真实 MySQL、Redis、PostgreSQL、Dify、浏览器、真实 LLM 全链验收

> 本文只列可执行待办，不替代原 PRD 跟踪表；跟踪表仍是唯一任务入口。完成某项后由 D 本人回传证据，MASTER 复核并更新跟踪表。
