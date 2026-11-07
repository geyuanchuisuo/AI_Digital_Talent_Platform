# 开发计划 · Todolist · 板块 D（D 线路）

> 版本：v0.1　|　日期：2026-11-07　|　身份：D　|　分支：`Lon`　|　基线：统一需求 V2.5（原始 PRD 必达 + D 线路四轮确认）
> 上游：`docs/需求文档.md` V2.5（§1/§5 F07/F08/F09/§11/§12.4/§16/§17）、`tasks/D.md`、[源码接入基线](源码接入基线.md)、[A 指标口径交付](../A/指标口径交付.md)（T-A53）、[A 对 D 契约响应](../../updates/A/20261107-1130-响应D工作流授权通知契约.md)、`.collaboration/ownership.json`。
> 用法：**按 P0→P5 顺序**，批次内按依赖并行；每项满足「验收标准」并留下实际命令/结果证据才勾选，勾选不是完成声明。编号 `T-Dxx` 供沟通引用。进度同步 `docs/updates/D/` 交接。
> 门禁：需求已全部清晰（D 线路四轮确认落稿）；本清单经用户 2026-11-07 授权细化，执行仍按 AGENTS §4（测试通过→fetch 同步→推 Lon→快进 master）。

---

## 批次总览

| 批次 | 内容 | 任务 | 前置依赖 | 状态 |
|---|---|---|---|---|
| P0 | 前置核对与契约登记（无代码） | 4 | 无 | ⬜ 最高优先级 |
| P1 | 基建：Redis/PgSQL/通知中心/LangGraph/Dify/SSE/限流 | 7 | P0；T-D01/T-D02/T-D07 可并行 | ⬜ |
| P2 | F07 自由问数、多维分析与报告 | 6 | P1（T-D02 副本、T-D03 通知）、A/B/C 指标口径 | ⬜ |
| P3 | F08 工作流、AI 中台、Skills、数字员工 | 8 | P1（T-D04 引擎、T-D07 Dify）、A/C 事件 | ⬜ |
| P4 | F09 工作台与通知前端 | 3 | P1（T-D03）、各域授权结果服务 | ⬜ |
| P5 | 验收、评测与发布 | 5 | P2–P4 | ⬜ |

---

## 跨模块依赖（需要哪些模块优先完成）

| 依赖 | 提供方 | 当前状态 | 阻塞的 D 任务 |
|---|---|---|---|
| AccountScope 阶段 1（admin/hr/personal 三角色 + person_id） | A | ✅ 已交付（`core/auth/scope.py`，迁移 A_0002） | T-D10 联调（阶段 1 范围） |
| AccountScope 组织范围扩展（六类角色 + `user_scope_grants` 表） | A | ⏳ 等 V2.5 六类角色冻结 | T-D10 组织范围验收、T-D26 组织越权负例 |
| 指标口径——岗位/筛选/匹配三域（字段+去重+时间+维度） | A | ✅ 已交付（T-A53，[指标口径交付](../A/指标口径交付.md)） | T-D08/T-D09 |
| 指标口径——解析/档案/问答域 | B | ⏳ 待交付 | T-D08/T-D09（B 域指标） |
| 指标口径——测评/培训/盘点域 | C | ⏳ 待交付（盘点域二期追加） | T-D08/T-D09（C 域指标） |
| M3 人才需求实体（headcount 字段） | A | ⏳ 等 V2.5 冻结后建表（缺口指标数据源） | 缺口/人工推进类指标 |
| 迁移链挂载点（A_0001→…→A_0004 merge head，35 表） | A | ✅ 已交付 | 全部 `D_*.py` |
| 知识/档案入库事件 + 图谱抽取输入 | B | ⏳ 知识模块待建 | T-D15 第三条事件、T-D27 图谱 |
| 测评提交事件 | C | ⏳ 测评模块实现中 | T-D15 第二条事件 |
| Agnes `agnes-3.0-flash` API 验证 | D 自查 | ⏳ 当前实配 spark-x2.6 可用，Agnes 协议未验证 | P2/P3 全部真实模型调用 |
| requirements.txt 新增依赖（langgraph/vanna/redis/psycopg/dify-sdk/neo4j） | A 修改，D 提请求 | ⏳ 当前清单无这些组件 | P1 全部 |

---

## P0 · 前置核对与契约登记（无代码）

- [ ] **T-D00a 环境与契约核对** — 实测 `get_account_scope` 三角色返回、迁移链 `alembic current/head` 挂载点、Agnes 协议探针（对照 spark 现状双轨记录）。影响结构：只读核对，无改动。验收：三项实测结果（含 Agnes 真实调用或明确失败原因）写入 `docs/updates/D/`。完成定义：三份证据齐 + 阻塞（如有）登记决策账本。
- [ ] **T-D00b 白名单指标目录 v1 起草** — 消费 A 的 T-A53 三域口径起草 metric 卡片（metric_id/来源表/公式/时间字段/去重键/允许参数分组/权限/返回 schema），B/C 域标「待口径」。影响结构：`docs/modules/D/指标目录.md`（新）。验收：A 域 3 张卡片每张含 A 交付文档引用；B/C 域缺口清单+催办请求发出。完成定义：A 已核对口经 A 复核确认，B/C 催办有据。
- [ ] **T-D00c 通知事件契约冻结** — 12 类 event_type 枚举 + notifications 表 schema + 幂等键规则（`{event_type}:{ref_id}:{业务版本值}`）落 [契约文档]；与 A 确认 router 注册与生产方调用函数签名。影响结构：`docs/modules/D/通知契约.md`（新）。验收：A 书面确认（或 counter）；幂等键规则覆盖 12 类逐条示例。完成定义：A 回复登记。
- [ ] **T-D00d Dify 社区版选型评审** — 版本锁定、商用/多租户许可条款核对、与自研 agent_* 的对接边界（API 对接层 vs 数据落库）结论。影响结构：`docs/modules/D/Dify选型结论.md`（新）。验收：许可条款核对结论 + 版本号 + 回退方案三要素齐。完成定义：MASTER/用户知悉，无未决反对。

---

## P1 · 基建层（D 域公共设施）

- [ ] **T-D01 Redis 统一缓存/会话/限流层** — 前置：P0、A 改 requirements。做什么：①`backend/app/core/cache/` 新增缓存客户端（键前缀/序列化/TTL 策略：会话/数据缓存/限流三类键分离）；②FastAPI 限流中间件（接口级限流+黑名单+熔断降级：故障时直查库+限流退化不阻塞核心业务）；③命中率/容量埋点。影响结构：`backend/app/core/cache/**`（新）、`backend/app/config.py`（REDIS_URL 等）、`backend/app/main.py`（中间件挂载）、`backend/requirements.txt`、`.env.example`。验收标准：①单测覆盖三类键读写/TTL/序列化；②限流触发 429、黑名单 403、Redis 停服时降级直查库且核心接口仍 200 的反例测试；③命中率埋点可在日志/看板观测。完成定义：pytest 通过 + 降级反例证据 + 更新记录。
- [ ] **T-D02 PgSQL 只读副本 + 定时 ETL** — 前置：P0。做什么：①deploy 增加 PgSQL 服务（compose）；②`config.py` 增 ANALYTICS_DB_URL；③`scripts/D/etl_pgsql.py` 定时批同步（一期分钟级 ETL：全量初始化+增量同步，表清单按指标目录圈定，排除 `profile_input_snapshot_json` 等敏感大字段）；④同步延迟与行数核对命令。影响结构：`deploy/**`、`backend/app/config.py`、`scripts/D/etl_pgsql.py`（新）、`.env.example`、`docs/modules/D/ETL说明.md`。验收标准：①ETL 全量+增量各跑通一次，行数与源库核对一致；②延迟实测记录（≤15 分钟目标）；③敏感字段确认未同步。完成定义：核对输出 + ETL 说明文档 + 更新记录。
- [ ] **T-D03 通知中心** — 前置：T-D00c、迁移链。做什么：①`backend/app/modules/notifications/`：models（notifications 表：event_type/ref_type/ref_id/actor/recipient/payload/idempotency_key 唯一/status/failure_reason/时间戳）+ service（emit 幂等投递、收件人按 AccountScope 继承源对象权限、失败重试走 TaskRunner、read/sent 状态）+ api（router 交 A 注册）；②`D_0001_notifications.py` 挂迁移链；③12 类事件枚举（含 `talent.gap_alert` 第 12 类）+ 各事件生产方函数。影响结构：`backend/app/modules/notifications/**`（新）、`backend/migrations/versions/D_0001_notifications.py`（新）、`backend/app/main.py`（A 注册 router）。验收标准：①同幂等键重复投递被唯一约束拦截（单测）；②个人只收本人通知、越权读取 403 反例；③12 类事件各 1 条发信单测；④失败→failed+reason→重试不产生新记录。完成定义：pytest 通过 + A 注册 router 后联调 200 + 更新记录。
- [ ] **T-D04 LangGraph 执行引擎接入** — 前置：P0、A 改 requirements。做什么：①引入 langgraph + PostgreSQL checkpointer（检查点持久化支撑断点续跑）；②引擎壳：有状态图运行器（并行于 TaskRunner，TaskRunner 保留为解析/OCR 轻任务执行器）；③人工等待/审批节点的独立等待状态——**不得被任何恢复/心跳机制重置**，等待超时按流程定义转催办/失败/接管。影响结构：`backend/app/core/workflows/engine/**`（新）、`backend/migrations/versions/D_0002_workflow_checkpoint.py`（新）、`backend/requirements.txt`。验收标准：①示例图跑通分支/条件/等待/恢复四路径；②杀进程重启后从检查点续跑、等待态实例不被重置的单测；③与 TaskRunner 并发互不干扰。完成定义：单测+恢复实测证据 + 更新记录。
- [ ] **T-D05 Dify 私有化部署与对接层** — 前置：T-D00d。做什么：①Dify 社区版私有化部署（compose/版本锁定）；②自研 agent_management/agent_publish/agent_workflow/model_provider 改造为 Dify API 对接层（编排配置下发、应用发布、数据落库回写）；③不另造第三套编排。影响结构：`deploy/dify/**`（新）、`backend/app/modules/agent_*/**` 改造、`docs/modules/D/Dify对接说明.md`（新）。验收标准：①Dify 起服+建应用+API 调用连通；②自研层创建的流程定义在 Dify 可见可运行；③版本锁定生效（升级演练记录）。完成定义：连通证据 + 对接说明 + 更新记录。
- [ ] **T-D06 SSE 进度推送** — 前置：T-D03。做什么：`/api/tasks/{id}/events` SSE 端点推送长任务真实阶段；断线重连补发、终态持久化（任务终态后订阅返回终态）。影响结构：`backend/app/api/tasks_events.py`（新）、`backend/app/core/tasks/`。验收标准：①SSE 收到阶段事件且不虚构百分比；②断线重连不丢终态；③无权限订阅 403。完成定义：实测证据 + 更新记录。
- [ ] **T-D07 依赖清单与请求闭环** — 前置：P0。做什么：向 A 发 requests/D 请求列新增依赖（langgraph/vanna/redis/psycopg/dify-sdk/neo4j/python-docx/报表图表库），由 A 修改 requirements.txt/锁文件。影响结构：`docs/requests/D/**`、`backend/requirements.txt`（A 改）。验收标准：依赖入清单且 `pip install` 全仓可装。完成定义：A 合入并回复。

---

## P2 · F07 自由问数、多维分析与报告

- [ ] **T-D08 白名单指标查询服务（第一层）** — 前置：T-D00b、T-D02、T-D03。做什么：①metric registry（目录 v1 落库：首批六类）；②每指标受控 ORM 统计函数（先过滤后聚合，按 AccountScope；排除软删/硬淘汰行按 A 口径）；③查询快照落库（问题/指标/参数/数据时间/表格/图/解读）。影响结构：`backend/app/modules/analytics/**`、`backend/migrations/versions/D_000x_metrics.py`、`frontend/src/features/analytics/**`。验收标准：①六类指标各 1 条与授权范围内人工 SQL 核算一致；②未登记指标拒绝；③零分母显示「暂无数据」；④先过滤后聚合断言（越权数据不进聚合）。完成定义：一致性核对记录 + pytest + 更新记录。
- [ ] **T-D09 Vanna.ai + SQL 校验器 + 受控执行** — 前置：T-D02、T-D08。做什么：①Vanna.ai 本地部署挂已选大模型（Agnes 或 spark 双轨实测）；②SQL 校验器：SELECT-only AST 解析→表/字段白名单→行级权限改写→强制 LIMIT→只读账号连 PgSQL 副本执行→审计落库；③默认超时 30s/返回 1000 行（可配置）。影响结构：`backend/app/modules/analytics/nl2sql/**`（新）。验收标准：①越权表/字段/行查询全部拦截的负例矩阵；②无法安全映射的请求拒答不猜测；③超时/上限触发实测；④禁写（INSERT/UPDATE/DDL）负例。完成定义：负例矩阵全绿 + 更新记录。
- [ ] **T-D10 多维分析、自动出图与决策建议** — 前置：T-D09。做什么：自动多维分析、图表生成（适用图表类型选择）、智能决策建议（事实与推断显式区分，不把相关说成因果）。影响结构：`backend/app/modules/analytics/**`、前端图表组件。验收标准：①同一问题多维组合结果可复现；②解读文本机器可区分事实段/推断段；③图表数据与表格一致。完成定义：样例集实测 + 更新记录。
- [ ] **T-D11 同快照 DOCX/PDF 导出** — 前置：T-D10。做什么：从同一分析快照生成 DOCX/PDF（中文字体、长表分页、空数据/截断/解读失败如实显示）；导出前重新校验权限、记录导出审计。影响结构：`backend/app/modules/reports/**`（新）、`backend/requirements.txt`（python-docx/报表库）。验收标准：①两格式中文/表格/图表可读；②导出不再重新问数（同快照断言）；③越权导出 403 反例；④无数据/截断如实显示。完成定义：三份样例产物 + 反例证据 + 更新记录。
- [ ] **T-D12 问数反馈/纠错入口** — 前置：T-D08。做什么：结果页「标记不准确」入口（附说明），反馈关联查询快照写审计；反馈列表管理端可见。影响结构：`backend/app/modules/analytics/feedback/**`、`frontend/src/features/analytics/**`。验收标准：①反馈落库且关联快照；②反馈不改当次结果、不自动改统计；③越权反馈他人结果 403。完成定义：单测+实测 + 更新记录。

---

## P3 · F08 受控 Agent 工作流、AI 中台、Skills、数字员工

- [ ] **T-D14 流程数据模型** — 前置：T-D04。做什么：workflow_definitions/versions/nodes/edges/runs/steps 落库模型与迁移（D_000x）；定义/版本/节点/连线可审阅；版本锁定（运行中实例不受新版本影响）。影响结构：`backend/app/modules/workflows/**`、`backend/migrations/versions/D_000x_workflow_*.py`。验收标准：①版本锁定断言（新版本不串运行实例）；②节点白名单校验（五类任务+人工审批，白名单外节点拒绝）；③输入/输出引用落库。完成定义：pytest + 迁移演练 + 更新记录。
- [ ] **T-D15 表单/模板配置与审批** — 前置：T-D14。做什么：受控表单/模板创建→修改→检查→提交流程定义；管理员审核发布/停用/回滚（授权人员操作，非后台文件）。影响结构：`backend/app/modules/workflows/**`、`frontend/src/features/workflows/**`。验收标准：①草稿→审核→发布/停用/回滚全生命周期单测；②无审核权限者提交后不可自行发布（403 反例）。完成定义：双端实测 + 更新记录。
- [ ] **T-D16 Dify 编排画布对接** — 前置：T-D05、T-D14。做什么：Dify 编排画布与落库流程模型双向映射（导入/导出/对账）；运行追踪回写。影响结构：`backend/app/modules/workflows/dify_bridge/**`。验收标准：①落库流程在 Dify 画布可见可改可回传；②版本对账一致；③画布外改动不静默进落库（需显式导入）。完成定义：往返演练证据 + 更新记录。
- [ ] **T-D17 五类任务节点 + 人工审批节点** — 前置：T-D14、A/B/C 授权服务。做什么：档案解析/岗位匹配/测评分析/培训建议/数据分析五类节点执行器（调用各域授权服务，不自查表）；人工审批节点（ActorScope 校验、等待→审批/驳回、超时转催办/失败/人工接管）。影响结构：`backend/app/modules/workflows/nodes/**`。验收标准：①五类节点各接真实域服务跑通；②审批等待不被心跳/恢复重置（复用 T-D04 断言）；③节点越权调用 403 反例。完成定义：逐节点实测 + 更新记录。
- [ ] **T-D18 触发、运行与恢复** — 前置：T-D17、T-D03。做什么：手动触发；三条首批业务事件触发（简历解析成功→匹配；测评提交→能力分析→培训草稿；知识入库→索引更新）；定时触发默认关闭+管理员启用+审计；失败阶段重试不重复生成已成功产物；取消/恢复。影响结构：`backend/app/modules/workflows/triggers/**`。验收标准：①三条事件端到端各 1 次（含 B/C 事件联调）；②重复事件不重复触发（幂等）；③失败重试不重复产物断言；④定时默认关闭验证。完成定义：端到端证据 + 更新记录。
- [ ] **T-D19 Skills 组件库** — 前置：T-D14。做什么：组件 schema（提示词模板+参数 JSON Schema+工具绑定+输出解析器）；创建→管理员审核→版本发布→编排节点引用；复用进运行审计；一期内部库（市场化按 PRD §8 后置）。影响结构：`backend/app/modules/workflows/skills/**`、`D_000x_skills.py`、前端组件库页。验收标准：①未审核组件不可发布（403）；②版本回退不影响已引用实例断言；③组件执行进运行审计。完成定义：全流程实测 + 更新记录。
- [ ] **T-D20 AI 中台管理面** — 前置：T-D04/T-D05。做什么：智能体注册/发布/退役/运行看板；模型供应商管理（多供应商接入、启停、密钥仅本地读取）；Prompt 版本库；任务调度管理页（定时默认关闭+授权启用）；调用日志/用量统计/错误率看板。影响结构：`backend/app/modules/dashboard/**`、`frontend/src/features/dashboard|tasks/**`。验收标准：①密钥不出现在任何接口响应/日志（断言）；②供应商启停即时生效；③用量/错误看板数字可回查调用日志。完成定义：看板实测 + 更新记录。
- [ ] **T-D21 数字员工** — 前置：T-D20、企微开放平台凭据。做什么：成员企业微信账号绑定（member_im_accounts 已建模）；三类只读场景（数据日报整理/简历队列巡检/待办提醒）；结果仅入库+站内通知指定管理员，不做站外外发；外发与状态变更须审批+幂等+限速+可接管。影响结构：`backend/app/modules/workflows/digital/**`、企微对接配置。验收标准：①三类场景各端到端 1 次；②站外外发被拦截的反例；③审批流挂起时不动业务状态。完成定义：实测 + 更新记录。

---

## P4 · F09 工作台与通知前端

- [ ] **T-D22 管理员工作台** — 前置：P1、各域授权结果服务。做什么：岗位/档案/待复核/待评分/培训计划/人才缺口预警六类真实数量卡片（一律经各域授权结果服务读取，不直连他模块业务表）+ 业务入口。影响结构：`backend/app/modules/dashboard/**`、`frontend/src/features/dashboard/**`。验收标准：①六类数字与业务记录回查一致；②未公布成绩/内部反馈/人才池不出现（断言）；③新增业务数据后数量随之变化。完成定义：回查记录 + 更新记录。
- [ ] **T-D23 个人工作台** — 前置：同上。做什么：本人待测评/培训/近期结果（AccountScope personal 范围）。影响结构：同 T-D22 个人侧。验收标准：另账号访问 403；本人数据完整显示。完成定义：双账号实测 + 更新记录。
- [ ] **T-D24 通知中心前端** — 前置：T-D03。做什么：通知列表/已读/失败查看；按事件类型跳转源对象。影响结构：`frontend/src/features/tasks/**` 或独立 notifications 页。验收标准：12 类事件渲染、已读状态、越权不可见。完成定义：实测 + 更新记录。

---

## P5 · 验收、评测与发布

- [ ] **T-D25 指标一致性验收** — 前置：T-D08/T-D09、A/B/C 口径齐。做什么：白名单六类指标逐条与授权范围内人工核算比对（组织/筛选/分组维度）。验收标准：逐指标「一致」结论+核算 SQL 留档；不一致项回改。完成定义：核对报告 + 更新记录。
- [ ] **T-D26 越权负例全套** — 前置：T-D09/T-D10。做什么：表/字段/行越权、目录外、歧义、伪造参数、个人明细下钻、组织范围未授权——全部拦截且不泄露对象存在性。验收标准：负例矩阵全绿；组织范围负例等 A 扩展交付后补。完成定义：负例报告 + 更新记录。
- [ ] **T-D27 Neo4j 图谱落地与抽检** — 前置：B 知识入库链路、归属登记（**未登记，先补 ownership**）。做什么：图谱数据模型落地、档案确认/知识入库后自动抽取、RAG 溯源与技能推荐接入。验收标准：人工抽检准确率≥85% 且引用可定位；双写一致性反例。完成定义：抽检报告 + 更新记录。
- [ ] **T-D28 性能实测** — 前置：口径冻结（ORG-05）。做什么：向量检索≤500ms、报表≤3–5s、缓存命中≥98% 按冻结口径实测。验收标准：实测报告（P95/P99、样本、环境）。完成定义：报告 + 未达标项进阻塞清单。
- [ ] **T-D29 D 域发布** — 前置：测试全绿。做什么：按 AGENTS §4 流程推 Lon→master 并在 `docs/updates/D/` 发布登记。完成定义：四引用一致 + 登记完成。

---

## 完成定义（全局 DoD）

1. 代码 + pytest/vitest 通过（新功能带正反例，反例至少覆盖越权/幂等/降级）；
2. `check_scope --staged` / `--base master` 通过（整合模式提示留痕）；
3. `docs/updates/D/` 新增交接（含实际命令/结果/未测项）；
4. 涉及接口/表结构变化同步 `docs/modules/D/` 契约；
5. 涉及依赖变化已由 A 合入 requirements；
6. 验收标准逐条实测，未执行写未执行。

## 明确不做（本清单外）

A/B/C 域业务模块、权限矩阵终稿（A）、M3 人才需求实体（A）、Skills 组件市场化（PRD 后置阶段）、原生 App、站外外发。
