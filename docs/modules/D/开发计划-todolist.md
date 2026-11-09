# 开发计划 · Todolist · 板块 D（D 线路）

> 版本：v0.3　|　日期：2026-11-07　|　身份：D　|　分支：`Lon`　|　基线：统一需求 **V2.6（已冻结，2026-11-07 用户确认）**——原始 PRD 必达 + D 线路四轮确认全部在冻结基线内
> 上游：`docs/需求文档.md` **V2.6 已冻结**（§1/§5 F07/F08/F09/§11/§12.4/§16/§17）、`tasks/D.md`、[源码接入基线](源码接入基线.md)、[A 指标口径交付](../A/指标口径交付.md)（T-A53）、[A 对 D 契约响应](../../updates/A/20261107-1130-响应D工作流授权通知契约.md)、`.collaboration/ownership.json`。
> 用法：**按 P0→P5 顺序**，批次内按依赖并行；每项满足「验收标准」并留下实际命令/结果证据才勾选，勾选不是完成声明。编号 `T-Dxx` 供沟通引用；**规模 S/M/L** 为粗估（S≈1–2 人日、M≈3–5 人日、L≥1–2 周）仅用于排期参考，实跑后按证据重估。
> v0.3 变更（用户确认的 6 处优化）：①依赖清单请求提前至 P0 首发（原 P1 T-D07 → **T-D00e**，P1 全部与真实模型调用都卡它）；②P1 末新增 **T-D01b 最小垂直切片**提前暴露集成风险；③全部任务标注规模与并行轨道；④依赖表补 A 侧接入点（router 注册/中间件挂载）；⑤基建任务补回退边界；⑥P0 新增测试基建 **T-D00f**。任务总数 32（v0.2 误标 33，P2 实为 5 项）→ v0.3 为 **34**。
> 门禁：需求已全部清晰（D 线路四轮确认落稿 + V2.6 冻结）；执行按 AGENTS §4（测试通过→fetch 同步→推 Lon→快进 master）。

---

## 批次总览

| 批次 | 内容 | 任务 | 前置依赖 | 状态 |
|---|---|---|---|---|
| P0 | 前置核对、契约登记、依赖请求、测试基建（无业务代码） | 6 | 无 | ⬜ 最高优先级 |
| P1 | 基建：Redis/PgSQL/通知中心/LangGraph/Dify/SSE + 垂直切片 | 7 | P0 完成；依赖六组件已由 A 合入（T-D00e 部分闭环，sse-starlette 缺项不阻 T-D01~05） | ⬜ 可开工 |
| P2 | F07 自由问数、多维分析与报告 | 5 | P1（T-D01b 切片、T-D02 副本）、B/C 指标口径 | ⬜ |
| P3 | F08 工作流、AI 中台、Skills、数字员工 | 8 | P1（T-D04 引擎、T-D05 Dify）、C 测评事件 | ⬜ |
| P4 | F09 工作台与通知前端 | 3 | P1（T-D03）、各域授权结果服务 | ⬜ |
| P5 | 验收、评测与发布 | 5 | P2–P4 | ⬜ |

**并行轨道**：P0 全部可并行；P1 内 **T-D01/T-D02/T-D03**（存储与通知线）与 **T-D04/T-D05**（双引擎线）两条轨道并行推进，T-D01b 收口；P2/P3 在各自前置就绪后可与 P1 尾部重叠开工。

---

## 跨模块依赖（需要哪些模块优先完成）

| 依赖 | 提供方 | 当前状态 | 阻塞的 D 任务 |
|---|---|---|---|
| AccountScope 阶段 1（admin/hr/personal 三角色 + person_id） | A | ✅ 已交付（`core/auth/scope.py`，迁移 A_0002） | T-D10 联调（阶段 1 范围） |
| AccountScope 组织范围扩展（六类角色 + `user_scope_grants` 表） | A | ✅ 已交付（A_0007，51 表；`department_ids`/`granted_scope` 聚合，[A 交付记录](../../updates/A/20261107-1700-组织范围授权扩展.md)；授予/撤销写接口属后续） | T-D10 组织范围验收、T-D26 组织越权负例（可开工） |
| 指标口径——岗位/筛选/匹配三域（字段+去重+时间+维度） | A | ✅ 已交付（T-A53，[指标口径交付](../A/指标口径交付.md)） | T-D08/T-D09 |
| 指标口径——解析/档案/问答域 | B | ⏳ 待交付 | T-D08/T-D09（B 域指标） |
| 指标口径——测评/培训/盘点域 | C | ⏳ 待交付（盘点域二期追加） | T-D08/T-D09（C 域指标） |
| M3 人才需求实体（headcount 字段） | A | ⏳ 冻结后建表（缺口指标数据源） | 缺口/人工推进类指标 |
| 迁移链挂载点（A_0001→…→A_0007，51 表） | A | ✅ 已交付 | 全部 `D_*.py` |
| **notifications/workflows router 注册进 `main.py`** | A | ✅ 已承诺（契约响应第 4 项：直接 import router 模式） | T-D03/T-D04 联调 |
| **Redis 限流/缓存中间件挂载进 `main.py`** | A | ⏳ 随 T-D01 交付协调挂载 | T-D01 联调 |
| 知识/档案入库事件 + 图谱抽取输入 | B | ⏳ 知识模块待建 | T-D18 第三条事件、T-D27 图谱 |
| 测评提交事件 | C | ⏳ 测评模块实现中 | T-D18 第二条事件 |
| Agnes `agnes-3.0-flash` API 验证 | D 自查 | ⏳ 当前实配 spark-x2.6 可用，Agnes 协议未验证 | P2/P3 全部真实模型调用 |
| requirements.txt 新增依赖（langgraph/vanna/redis/psycopg/dify-sdk/neo4j/python-docx/图表库） | A 修改，D 提请求 | ✅ 六组件已合入并实装验证（langgraph 1.2.20/vanna 2.0.2/redis 8.1.0/psycopg 3.3.8/dify-client 0.1.10/neo4j 6.4.1，宽松约束，573842c + [A 响应](../../updates/A/20261107-1710-依赖冻结.md)）；**sse-starlette/reportlab 未入**（T-D06/T-D11 开工前补请） | P1 主体解锁；P2/P3 真实模型调用仍卡 Agnes（DEC-20261107-AGNES-VERIFY） |

---

## P0 · 前置核对、契约登记、依赖请求与测试基建（无业务代码）

- [x] **T-D00e 依赖清单请求闭环**｜规模：S — **P0 首发项**：✅ 2026-11-07 请求已落 [docs/requests/D/20261107-D-新增依赖清单请求.md](../../requests/D/20261107-D-新增依赖清单请求.md)；**A 已于 573842c 合入 6/8 项并实装验证**（[UPD-A-1710](../../updates/A/20261107-1710-依赖冻结.md)），D 侧 `uv pip install`+六包 import 复验通过，**P1 主体解锁**；sse-starlette/reportlab 未合入（T-D06/T-D11 开工前补请），请求保持部分关闭状态。原文：向 A 发 `docs/requests/D/` 请求列全部新增依赖（langgraph/vanna/redis/psycopg/dify-sdk/neo4j/python-docx/图表库），由 A 修改 requirements.txt/锁文件。影响结构：`docs/requests/D/**`、`backend/requirements.txt`（A 改）。验收标准：依赖入清单且 `pip install` 全仓可装；P1 开工前到位。完成定义：A 合入并回复。**为什么放 P0 首位**：P1 全部基建与 P2/P3 真实模型调用都卡这份清单。
- [x] **T-D00f 测试基建（tests/D harness）**｜规模：M — ✅ 2026-11-07 完成：`tests/D/`（fake_llm 三态/events_factory 14 枚举/sse_client/conftest SQLite 编译规则）+ 冒烟 7/7 通过（`backend/.venv -m pytest tests/D -v`）。用法见 [tests/D/README.md](../../tests/D/README.md)。原文：`tests/D/` 脚手架：DB fixture（迁移链建表+种子数据）、假 LLM server（可注入成功/超时/畸形输出三态）、SSE 测试客户端、notifications 事件工厂。影响结构：`tests/D/**`（新）。验收标准：①harness 可被 P1 各任务直接复用；②假 LLM 三态切换验证；③迁移链在测试库跑通。完成定义：样例测试跑通 + harness 用法写入模块文档。
- [x] **T-D00a 环境与契约核对**｜规模：S — ✅ 2026-11-07 完成：alembic heads=`A_0006_merge_m3_c0003`（单头，D_0001 可挂）；AccountScope 三角色/auth 单测全绿（.env 缺失系本 clone 未建，已从 .env.example 生成后 34/34 通过）；**Agnes 探针 401**（端点可达、响应 AgnesAI JSON 错误体；现配 spark key 在 Agnes 无效——无 Agnes 凭据，协议完整验证阻塞，需用户提供 Agnes key 或确认先用 spark）。**Agnes 阻塞已登记决策账本（DEC-20261107-AGNES-VERIFY）**。原文：实测 `get_account_scope` 三角色返回、迁移链 `alembic current/head` 挂载点、Agnes 协议探针（对照 spark 现状双轨记录）。影响结构：只读核对，无改动。验收：三项实测结果（含 Agnes 真实调用或明确失败原因）写入 `docs/updates/D/`。完成定义：三份证据齐 + 阻塞（如有）登记决策账本。
- [x] **T-D00b 白名单指标目录 v1 起草**｜规模：M — ✅ 2026-11-07 完成：[docs/modules/D/指标目录.md](指标目录.md)（A 域 3 卡片按 T-A53 登记；**M3 五表已由 A 于 fbcfb7f 落地**，缺口/人工推进类指标数据源解锁；B/C 域标待口径）；**B/C 催办请求已发**（[对 B：解析域口径确认](../../requests/D/20261107-D-指标口径确认催办-B.md)——B 契约 §6 已有草稿待确认字段终值；[对 C：测评/培训域口径交付](../../requests/D/20261107-D-指标口径交付催办-C.md)——C 尚无口径交付）。原文：消费 A 的 T-A53 三域口径起草 metric 卡片（metric_id/来源表/公式/时间字段/去重键/允许参数分组/权限/返回 schema），B/C 域标「待口径」。影响结构：`docs/modules/D/指标目录.md`（新）。验收：A 域 3 张卡片每张含 A 交付文档引用；B/C 域缺口清单+催办请求发出。完成定义：A 已核对口经 A 复核确认，B/C 催办有据。
- [x] **T-D00c 通知事件契约冻结**｜规模：S — ✅ 2026-11-07 完成：[docs/modules/D/通知契约.md](通知契约.md)（12 类/14 枚举 + schema + 幂等键逐类示例）；生产方函数签名待 A 对接确认。原文：12 类 event_type 枚举 + notifications 表 schema + 幂等键规则（`{event_type}:{ref_id}:{业务版本值}`）落契约文档；与 A 确认 router 注册与生产方调用函数签名。影响结构：`docs/modules/D/通知契约.md`（新）。验收：A 书面确认（或 counter）；幂等键规则覆盖 12 类逐条示例。完成定义：A 回复登记。
- [x] **T-D00d Dify 社区版选型评审**｜规模：S — ✅ 2026-11-07 完成：[docs/modules/D/Dify选型结论.md](Dify选型结论.md)（社区版可用结论/许可要点/对接边界表/回退三要素；具体版本号部署时锁定复核）。原文：版本锁定、商用/多租户许可条款核对、与自研 agent_* 的对接边界（API 对接层 vs 数据落库）结论。影响结构：`docs/modules/D/Dify选型结论.md`（新）。验收：许可条款核对结论 + 版本号 + 回退方案三要素齐。完成定义：MASTER/用户知悉，无未决反对。

---

## P1 · 基建层（D 域公共设施）

- [x] **T-D01 Redis 统一缓存/会话/限流层**｜规模：M — ✅ 2026-11-07 隔离测试完成：`backend/app/core/cache/`（CacheClient 三类键/JSON/降级冷却 + RateLimitMiddleware 429/403/降级放行）+ config.py 四键（默认向后兼容）；`pytest tests/D -v` → **20 passed, 1 skipped**（真实 Redis 集成用例无服务自动跳过；冒烟 7 项不回归）。降级反例用死端口真实连接验证：Redis 停服 → 核心接口仍 200、黑名单不误杀、计数放行。契约 [缓存限流契约](缓存限流契约.md)；**公共接入待 A**（[挂载请求](../../requests/D/20261107-D-限流中间件挂载请求.md)：main.py 挂载 + .env.example + 429 码表裁决）。原文：①`backend/app/core/cache/` 缓存客户端（键前缀/序列化/TTL 策略：会话/数据缓存/限流三类键分离）；②FastAPI 限流中间件（接口级限流+黑名单+熔断降级）；③命中率/容量埋点。影响结构：`backend/app/core/cache/**`（新）、`backend/app/config.py`（REDIS_URL）、`backend/app/main.py`（A 挂载中间件）、`backend/requirements.txt`、`.env.example`。验收标准：①三类键读写/TTL/序列化单测；②限流 429、黑名单 403、Redis 停服降级直查库且核心接口仍 200 的反例；③命中率埋点可观测。完成定义：pytest 通过 + 降级反例证据 + 更新记录。**回退边界**：缓存数据丢失可重建；会话失效需重新登录；停服降级路径已定义。
- [x] **T-D02 PgSQL 只读副本 + 定时 ETL**｜规模：M — ✅ 2026-11-07 完成：`deploy/postgres/docker-compose.yml`（postgres:16-alpine@5433，容器 healthy）+ `config.py: analytics_db_url`（空=降级主库直查）+ `scripts/D/etl_pgsql.py`（9 表列级 allowlist——JD 全文/简历原文/理由快照整体排除，`resume_files`/`parsed_resumes`/`talent_profiles` 不进副本；类型从 information_schema 自动映射；全量 TRUNCATE+分块重灌 / 增量水位 ON CONFLICT 幂等 / `--verify` 行数+延迟核对）+ [ETL说明](ETL说明.md)。集成测试 `tests/D/test_etl_pgsql.py` **4/4 真实 MySQL→真实 PG**：敏感排除、全量行数一致、增量拾取+幂等、水位追平（零积压≤15 分钟目标）；`--verify` CLI 实测 8 表源目标 OK。**过程中抓出两处规格漂移**（match_results 无 job_profile_id、match_batches 无 updated_at）——防护机制生效，规格已按真实 schema 修正。原文：①deploy 增加 PgSQL 服务；②`config.py` 增 ANALYTICS_DB_URL；③`scripts/D/etl_pgsql.py` 定时批同步（全量初始化+增量，表清单按指标目录圈定，排除敏感大字段）；④同步延迟与行数核对命令。影响结构：`deploy/**`、`backend/app/config.py`、`scripts/D/etl_pgsql.py`（新）、`.env.example`、`docs/modules/D/ETL说明.md`。验收标准：①ETL 全量+增量跑通且行数核对一致；②延迟实测≤15 分钟目标；③敏感字段未同步确认。完成定义：核对输出 + ETL 说明 + 更新记录。**回退边界**：副本数据不一致→回退主库 MySQL 直查并降级口径；ETL 可随时停不停业务。
- [x] **T-D03 通知中心**｜规模：M — ✅ 2026-11-07 隔离测试完成：`backend/app/modules/notifications/`（models 14 枚举 + service emit 幂等投递/AccountScope 读取隔离/retry + producers 12 类生产方函数 + router）+ 迁移 `D_0001_notifications`（挂 A_0007，52 表基线，alembic heads 单头）；`tests/D/test_notifications.py` 13 例 + 全套 tests/D **32 passed, 1 skipped**——验收①同键重复投递唯一约束拦截（含绕过 service 直插的 DB 级反例）②个人只收本人/越权 403/管理侧全量+按收件人过滤 ③14 枚举发信全覆盖 ④failed+reason→重试同一行不新增。**契约 v1.1 两处修订**（多收件人幂等键加 `:r{recipient}`；重试改「幂等键重发」语义，TaskRunner 任务模型与通知状态机不匹配）见 [通知契约](通知契约.md)。router 待 A 注册（[挂载请求](../../requests/D/20261107-D-限流中间件挂载请求.md) 第 5 项）。原文：①`backend/app/modules/notifications/`：models（notifications 表全字段）+ service（emit 幂等投递、收件人按 AccountScope 继承源对象权限、失败重试走 TaskRunner、read/sent 状态）+ api（router 交 A 注册）；②`D_0001_notifications.py` 挂迁移链；③12 类事件枚举（含 `talent.gap_alert`）+ 各事件生产方函数。影响结构：`backend/app/modules/notifications/**`（新）、`backend/migrations/versions/D_0001_notifications.py`（新）、`backend/app/main.py`（A 注册 router）。验收标准：①同幂等键重复投递被唯一约束拦截；②个人只收本人、越权读取 403 反例；③12 类事件发信单测；④失败→failed+reason→重试不产生新记录。完成定义：pytest 通过 + A 注册后联调 200 + 更新记录。
- [x] **T-D04 LangGraph 执行引擎接入**｜规模：L — ✅ 2026-11-07 隔离测试完成：`backend/app/core/workflows/engine/`（SQLAlchemyCheckpointSaver 自实现持久化检查点——**未引入额外依赖**，官方 sqlite/postgres saver 是 A 管的新包，一套代码覆盖 SQLite 测试/MySQL 生产/T-D02 PgSQL；WorkflowEngine 引擎壳 + `workflow_engine_runs` 登记表 53 表基线 + D_0002 迁移单头）。`tests/D/test_workflow_engine.py` 6/6：**①分支/条件/等待/恢复四路径 ②杀进程（新实例）后检查点续跑、等待态三轮查询不重置 ③recover 白名单 running，waiting 明确排除（人工审批不被任何恢复/心跳重置）**；失败→failed 可重试（回退边界）。langgraph 1.x 中断判定依据 invoke 返回 `__interrupt__` 键（实测）。契约 [工作流引擎契约](工作流引擎契约.md)。原文：①引入 langgraph + PostgreSQL checkpointer（检查点持久化支撑断点续跑）；②引擎壳：有状态图运行器（并行于 TaskRunner，后者保留为解析/OCR 轻任务执行器）；③人工等待/审批节点独立等待状态——**不得被任何恢复/心跳机制重置**，超时按流程定义转催办/失败/接管。影响结构：`backend/app/core/workflows/engine/**`（新）、`backend/migrations/versions/D_0002_workflow_checkpoint.py`（新）、`backend/requirements.txt`。验收标准：①示例图跑通分支/条件/等待/恢复四路径；②杀进程重启后从检查点续跑、等待态不被重置单测；③与 TaskRunner 并发互不干扰。完成定义：单测+恢复实测 + 更新记录。**回退边界**：引擎异常→运行实例置 failed 可重试；TaskRunner 轻任务独立不受影响。
- [ ] **T-D05 Dify 私有化部署与对接层**｜规模：M — 前置：T-D00d。**进度（2026-11-08）：①③完成**——Dify 1.19.0 社区版私有化部署（15 容器 Up/healthy，compose 镜像 tag 锁定，pinned commit `8e689e86`，凭据与版本记录在部署目录）；验收①「起服+建应用+API 连通」实测通过：console API 建应用 `AIT-Connectivity-Check`（201）+ app key + `GET /v1/parameters` 200。**剩余**：②自研 agent_* 改造为 Dify API 对接层（归 T-D16 dify_bridge 协同）与对接说明文档；升级演练记录。过程中的坑：GitHub 直连断流三次（改走 ghfast 镜像 tar 包 33.7MB）；`localhost` IPv6 hairpin 半坏（自动化一律用 `127.0.0.1`）；宿主机遗留看门狗脚本持管理员凭据周期登录（凭据已找回：admin@local.dev，记录在部署目录）。原文：①Dify 社区版私有化部署（compose/版本锁定）；②自研 agent_management/agent_publish/agent_workflow/model_provider 改造为 Dify API 对接层（编排配置下发、应用发布、数据落库回写）；③不另造第三套编排。影响结构：`deploy/dify/**`（新）、`backend/app/modules/agent_*/**` 改造、`docs/modules/D/Dify对接说明.md`（新）。验收标准：①Dify 起服+建应用+API 调用连通；②自研层流程定义在 Dify 可见可运行；③版本锁定生效（升级演练记录）。完成定义：连通证据 + 对接说明 + 更新记录。**回退边界**：Dify 不可用→表单/模板配置兜底（V2.6 确认的合法补充路径），不阻塞流程定义与执行。
- [ ] **T-D06 SSE 进度推送**｜规模：S — 2026-11-09 代码已实现（`core/tasks/events.py`，复用持久化任务行 + `Last-Event-ID` 快照去重 + 终态重连；main.py 已挂载），仅静态验证，运行时权限/重连/终态测试待最终验收；断线期间中间状态不可逐条重放，边界见 [任务进度 SSE 契约](任务进度SSE契约.md)。前置：T-D03。做什么：`/api/tasks/{id}/events` SSE 端点推送长任务真实阶段；断线重连补发、终态持久化（任务终态后订阅返回终态）。影响结构：`backend/app/api/tasks_events.py`（新）、`backend/app/core/tasks/`。验收标准：①SSE 收到阶段事件且不虚构百分比；②断线重连不丢终态；③无权限订阅 403。完成定义：实测证据 + 更新记录。
- [x] **T-D01b 最小垂直切片（走通一条再铺量）**｜规模：M — ✅ 2026-11-08 完成：活跃岗位数（A 口径 §1：is_active=1 且 title 最新 version）端到端贯通——`backend/app/modules/analytics/`（service 受控统计 + 副本/主库自适应 + Redis 缓存 TTL300s + `analytics_query_snapshots` 快照 54 表基线 D_0003）+ `/api/analytics/metrics/jobs/active` JSON+SSE 双端点（StreamingResponse 实现，未引 sse-starlette）+ 前端指标卡 `/analytics`（antd，vitest 3/3，tsc 通过）。验收实测：①真实链路演示 value=1/source=replica/快照落库/**缓存命中**（测试 Redis 6399，命中率 1.0）②personal 403 反例（JSON+SSE 均不进流）③**集成问题清单 4 项**（redis.asyncio 跨事件循环崩溃——已修复+回归测试；models 直连入口循环导入纪律；MySQL 双引号陷阱；jest-dom 缺失惯例）。[问数切片契约](问数切片契约.md)。原文：选取 A 已交付口径的最简指标「活跃岗位数」，全链路贯通：PgSQL 副本→ORM 统计→查询快照落库→前端指标卡→SSE 进度；Redis 缓存接入。影响结构：analytics 最小实现、前端一张指标卡、SSE 端点首用。验收标准：①端到端演示一次成功（含缓存命中）；②越权访问反例；③发现的集成问题全部清单化。完成定义：演示证据 + 集成问题清单 + 更新记录。**为什么**：铺量前验证 P1 基建组合可行性，把集成风险消灭在 P1。

---

## P2 · F07 自由自然语言问数、多维分析与报告

- [ ] **T-D08 白名单指标查询服务（第一层）**｜规模：L — **进度（2026-11-08）：第一层完成，3/6 指标可查**。①✅ metric registry 落库（`metric_registry` 表 D_0004，7 行 = 六卡 + 切片指标；`awaiting_caliber` 卡查询 422 拒绝不猜测）；②✅ 受控统计函数 ×3（jobs.active / job.demand_progress_gap【A v0.2 §7 权威公式：缺口=headcount−HR确认已录用，人工推进不混入】/ match.threshold_reference【硬淘汰 score=0 排除分母】；先过滤后聚合：403/422 准入先于计算 + 参数 WHERE 先于聚合，均有断言）；③✅ 查询快照落库（params/result_json 含表格/来源/命中；图/解读归 T-D10）；新增目录端点 + 通用查询端点。**验收实测**：②未登记 422/待口径 422 ✓、③零分母暂无数据 ✓、④准入先于计算 ✓、①一致性 3/6（A 域 3 卡 SQLite 单测 + 真实 MySQL 集成 == 人工 SQL；**B/C 3 卡待口径交付后补实现与核算**，registry 已挂 awaiting）。剩余：B/C 口径交付后 6/6 收口 + by_department 等多维分组（T-D10 协同）。影响结构：`backend/app/modules/analytics/**`、`backend/migrations/versions/D_0004_metric_registry.py`（新，已对真实 MySQL 执行）、61 表基线。原文：①metric registry（目录 v1 落库：首批六类）；②每指标受控 ORM 统计函数（先过滤后聚合，按 AccountScope；软删/硬淘汰行排除按 A 口径）；③查询快照落库（问题/指标/参数/数据时间/表格/图/解读）。验收标准：①六类指标各 1 条与授权范围内人工 SQL 核算一致；②未登记指标拒绝；③零分母「暂无数据」；④先过滤后聚合断言。完成定义：一致性核对记录 + pytest + 更新记录。
- [ ] **T-D09 Vanna.ai + SQL 校验器 + 受控执行**｜规模：L — **进度（2026-11-08）：②③完成并实测，①生成器就绪待真实 Key**。`backend/app/modules/analytics/nl2sql/`（whitelist/validator/executor/generator/service 五层）+ `nl2sql_audit` 表（D_0005 挂 D_0004，**62 表基线**）+ `POST /api/analytics/nl2sql/query`。**验收实测（31 例全绿）**：①越权负例矩阵全绿（INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE/GRANT/多语句/CTE 内嵌写/FOR UPDATE/越权表 pg_authid/子查询——全 4xx+审计留痕；引号内关键词不误伤）②无法安全映射拒答（CANNOT_ANSWER→422 refused_unmappable；LLM key 占位→RefusingGenerator 不假数据）③超时实测 pg_sleep(5) vs 3s→statement_timeout 取消 503；行上限 generate_series(5000)→截断 1000+truncated ④禁写兜底实测：PG 只读事务拒绝 INSERT（校验器漏网硬强制）+副本列级 allowlist=字段白名单执行面。①生成器：OpenAI 兼容协议（spark 现配/Agnes 换 .env 三键），**真实调用待用户填 Key**（占位→RefusingGenerator）；vanna 2.0.2 为新版 Agent 架构与经典 API 不兼容，生成器接口已与 vanna 解耦（后续实现同协议零改动接入）。原文：①Vanna.ai 本地部署挂已选大模型（Agnes 或 spark 双轨实测）；②SQL 校验器：SELECT-only AST 解析→表/字段白名单→行级权限改写→强制 LIMIT→只读账号连 PgSQL 副本执行→审计落库；③默认超时 30s/返回 1000 行（可配置）。影响结构：`backend/app/modules/analytics/nl2sql/**`（新）。验收标准：①越权表/字段/行查询拦截负例矩阵；②无法安全映射拒答不猜测；③超时/上限触发实测；④禁写（INSERT/UPDATE/DDL）负例。完成定义：负例矩阵全绿 + 更新记录。
- [ ] **T-D10 多维分析、自动出图与决策建议**｜规模：M — **进度（2026-11-08）：完成并实测**。①✅ 多维分组：`DIMENSIONS`/`BREAKDOWN_FUNCS`（jobs.active→department、gap→project_group、threshold→job）+ GET /metrics/{id}?group_by=（非白名单维度 422；awaiting 先于维度检查）；同组合跨缓存状态逐字段可复现 + 快照逐组合留痕。②✅ 自动出图：`charts.py` 规格器——图表数据只从同一响应 table.rows 派生（逐点核对+Σseries==value），类型确定性选择（none/number/bar，序列截断 20）。③✅ 决策建议：`insight.py` 分段 {kind: fact|inference, ref, basis}——fact 只复述查询数字、inference 模板化建议带依据；**因果措辞扫描守卫零命中**（has_causal_language + 测试）；规则式不依赖 LLM（同数据完全可复现），LLM 增强待 Key。前端：`@ant-design/charts` Bar + fact/inference Tag 分段渲染（vitest 4/4，tsc 通过）。快照 result_json 扩展存 chart/interpretation（T-D11 同快照导出基础）。影响结构：`backend/app/modules/analytics/**`、前端图表组件。验收标准：①同一问题多维组合结果可复现；②解读文本机器可区分事实段/推断段；③图表数据与表格一致。完成定义：样例集实测 + 更新记录。
- [ ] **T-D11 同快照 DOCX/PDF 导出**｜规模：M — **进度（2026-11-08）：完成并实测（7 例全绿）**。`backend/app/modules/reports/`（新：service/router/models）+ `snapshot_export_audit` 表（D_0007 挂 D_0006，**64 表基线**）+ `GET /api/reports/snapshots/{id}/export?format=docx|pdf`。**验收实测**：①两格式生成且二进制可解析（python-docx 回读：标题/指标值/表格逐格/事实推断标记；PDF 头合法+STSong CID 中文字体；长表 platypus 分页）②同快照断言：统计函数哨兵零调用，构建输入只有快照 result_json ③越权导出 403（personal 非创建者）、404/422 反例 ④空数据/截断/无解读如实显示 ⑤导出审计逐次落库。**图表以数据表形式呈现**（chart.data 忠实转写，「图表可读」以数据可读交付；矢量图形渲染归 T-D20 看板）。影响结构：`backend/app/modules/reports/**`（新）、`backend/requirements.txt`（sse-starlette/reportlab 已由 D 直接合入）。原文：从同一分析快照生成 DOCX/PDF（中文字体、长表分页、空数据/截断/解读失败如实显示）；导出前重新校验权限、记录导出审计。完成定义：三份样例产物 + 反例证据 + 更新记录。
- [ ] **T-D12 问数反馈/纠错入口**｜规模：S — **进度（2026-11-08）：后端完成并实测（9 例全绿）**。`backend/app/modules/analytics/feedback/`（service 创建仅快照创建者+幂等/列表管理侧全量附快照关联）+ `analytics_feedback` 表（D_0006 挂 D_0005，**63 表基线**）+ 2 端点（POST/GET /api/analytics/feedback）。**验收实测**：①反馈落库且关联快照（含快照 metric_id/params/result 回显）②反馈前后同查询值/表逐字段一致（不自动改统计）③越权反馈他人快照 403+未落库、未知快照 404、personal 403 ④幂等（重复标记同 id 不新增）。**依赖直接合入（用户授权）**：sse-starlette 2.4.1 + reportlab 4.5.1 入 requirements 并装 venv（T-D00e 余项清零）。剩余：前端「标记不准确」按钮与反馈列表页（影响结构内）；实测待浏览器。原文：结果页「标记不准确」入口（附说明），反馈关联查询快照写审计；反馈列表管理端可见。影响结构：`backend/app/modules/analytics/feedback/**`、`frontend/src/features/analytics/**`。验收标准：①反馈落库且关联快照；②反馈不改当次结果、不自动改统计；③越权反馈他人结果 403。完成定义：单测+实测 + 更新记录。

---

## P3 · F08 受控 Agent 工作流、AI 中台、Skills、数字员工

- [ ] **T-D14 流程数据模型**｜规模：L — **进度（2026-11-08）：完成并实测（13 例全绿 + 迁移演练）**。`backend/app/modules/workflows/`（models/service）+ 6 表迁移 D_0008 挂 D_0007（**70 表基线**）：definitions/versions（draft→published→deprecated，发布后不可变）/nodes/edges/runs（version_id 启动钉死）/run_steps（run×node×attempt 唯一）。**验收实测**：①版本锁定——运行实例不受新版本/废弃影响、发布版本拒绝修改（409）、草稿不可启动 ✓；②节点白名单——五类任务+人工审批六类型参数化通过、白名单外 422（建版/落步骤两道关）✓；③输入/输出引用落库——trigger/form_field/node_output/literal 四来源 schema 校验+前向引用合法、审批分支 approve/reject 规则 ✓；步骤幂等覆盖/重试新 attempt（T-D18 依赖）；get_version_detail 全量可审阅（T-D15 审核页/T-D16 对账共用）；真实 MySQL 迁移演练 73=70 ORM+2 检查点+alembic_version 逐一对账（顺手补齐 T-D04 残留缺表 workflow_checkpoint_writes）。契约 [工作流数据模型契约](工作流数据模型契约.md)。原文：workflow_definitions/versions/nodes/edges/runs/steps 落库模型与迁移（D_000x）；定义/版本/节点/连线可审阅；版本锁定（运行中实例不受新版本影响）。影响结构：`backend/app/modules/workflows/**`、`backend/migrations/versions/D_000x_workflow_*.py`。验收标准：①版本锁定断言；②节点白名单校验（五类任务+人工审批，白名单外拒绝）；③输入/输出引用落库。完成定义：pytest + 迁移演练 + 更新记录。
- [ ] **T-D15 表单/模板配置与审批**｜规模：M — **进度（2026-11-09）：后端+前端完成并实测（后端 6 例 + 前端 7 例全绿）**。`backend/app/modules/workflows/` 生命周期：版本状态机加 `pending_review`（draft→提交→pending_review→审核通过 published/驳回退 draft 留 review_note）；D_0009 加列 submitted_by/submitted_at/review_note（70 表基线不变）；`/api/workflows` 9 端点挂载 main.py。**验收实测**：①草稿→审核→发布/停用/回滚全生命周期 ✓（含回滚到已停用版本重新发布、回滚后新运行缺省取回滚目标、运行中实例不受影响）；②无审核权限者提交后不可自行发布 ✓——四眼原则（提交人 approve/reject 均 403 且版本保持待审）+ personal 全操作 403；权限口径：管理侧 can_view_all_persons + 四眼分离（阶段 1 无 admin 账号，scope.py 对 admin 落 deny 兜底）；service 层 create/update/submit 全链门禁（user 必传，修掉仅 router 拦截的裸奔）。剩余：前端配置/审核页（`frontend/src/app/(main)/workflows/`，按仓库惯例非 features/ 路径）+ 双端实测。原文：受控表单/模板创建→修改→检查→提交流程定义；管理员审核发布/停用/回滚（授权人员操作，非后台文件）。影响结构：`backend/app/modules/workflows/**`、`frontend/src/features/workflows/**`。验收标准：①草稿→审核→发布/停用/回滚全生命周期单测；②无审核权限者提交后不可自行发布（403 反例）。完成定义：双端实测 + 更新记录。
- [ ] **T-D16 Dify 编排画布对接**｜规模：M — 前置：T-D05、T-D14。做什么：Dify 编排画布与落库流程模型双向映射（导入/导出/对账）；运行追踪回写。影响结构：`backend/app/modules/workflows/dify_bridge/**`。验收标准：①落库流程在 Dify 画布可见可改可回传；②版本对账一致；③画布外改动不静默进落库（需显式导入）。完成定义：往返演练证据 + 更新记录。
- [ ] **T-D17 五类任务节点 + 人工审批节点**｜规模：L — **进度（2026-11-09）：首轮交付并实测（13 例全绿）**。`backend/app/modules/workflows/nodes/`（runtime 状态/引用解析/步骤包装、human_approval interrupt 审批、business 五类执行器、graph_builder 确定性构建）+ `run_service.py`（手动触发→引擎→镜像；恢复口三道校验：管理侧/四眼/deadline 409）+ router 4 端点（POST /runs、GET /runs、GET /runs/{id}、POST /runs/{id}/approval）。**验收实测**：①data_analysis 真实端到端（registry 准入→副本 SQL→快照）、job_match 真实（create_batch）；parse/training 服务边界映射验证、真实 LLM 端到端待 Key（DEC-20261107-AGNES-VERIFY 同族）；②等待不被重启/恢复重置（新引擎实例+recover_registry 断言，复用 T-D04 口径）✓；③节点越权 403（registry 角色白名单外发起人→节点 failed 留痕 [403]→运行 failed）+ 恢复口非管理侧/自批 403 ✓。剩余：真实 LLM 端到端、前端运行/审批页、通知接线（T-D18）。原文：档案解析/岗位匹配/测评分析/培训建议/数据分析五类节点执行器（调用各域授权服务，不自查表）；人工审批节点（AccountScope 校验、等待→审批/驳回、超时转催办/失败/人工接管）。影响结构：`backend/app/modules/workflows/nodes/**`。验收标准：①五类节点各接真实域服务跑通；②审批等待不被心跳/恢复重置（复用 T-D04 断言）；③节点越权调用 403 反例。完成定义：逐节点实测 + 更新记录。
- [ ] **T-D18 触发、运行与恢复**｜规模：L — 2026-11-09 首阶段仅实现内部业务事件的确定性幂等启动入口；三条业务事件接线、定时启用审计、阶段重试和取消/恢复尚未实现，静态核查而非运行验收。前置：T-D17、T-D03。做什么：手动触发；三条首批业务事件触发（简历解析成功→匹配；测评提交→能力分析→培训草稿；知识入库→索引更新）；定时触发默认关闭+管理员启用+审计；失败阶段重试不重复生成已成功产物；取消/恢复。影响结构：`backend/app/modules/workflows/triggers/**`。验收标准：①三条事件端到端各 1 次（含 B/C 事件联调）；②重复事件不重复触发（幂等）；③失败重试不重复产物断言；④定时默认关闭验证。完成定义：端到端证据 + 更新记录。
- [ ] **T-D19 Skills 组件库**｜规模：M — 前置：T-D14。做什么：组件 schema（提示词模板+参数 JSON Schema+工具绑定+输出解析器）；创建→管理员审核→版本发布→编排节点引用；复用进运行审计；一期内部库（市场化按 PRD §8 后置）。影响结构：`backend/app/modules/workflows/skills/**`、`D_000x_skills.py`、前端组件库页。验收标准：①未审核组件不可发布（403）；②版本回退不影响已引用实例断言；③组件执行进运行审计。完成定义：全流程实测 + 更新记录。
- [ ] **T-D20 AI 中台管理面**｜规模：L — 前置：T-D04/T-D05。做什么：智能体注册/发布/退役/运行看板；模型供应商管理（多供应商接入、启停、密钥仅本地读取）；Prompt 版本库；任务调度管理页（定时默认关闭+授权启用）；调用日志/用量统计/错误率看板。影响结构：`backend/app/modules/dashboard/**`、`frontend/src/features/dashboard|tasks/**`。验收标准：①密钥不出现在任何接口响应/日志（断言）；②供应商启停即时生效；③用量/错误看板数字可回查调用日志。完成定义：看板实测 + 更新记录。
- [ ] **T-D21 数字员工**｜规模：M — 前置：T-D20、企微开放平台凭据。做什么：成员企业微信账号绑定（member_im_accounts 已建模）；三类只读场景（数据日报整理/简历队列巡检/待办提醒）；结果仅入库+站内通知指定管理员，不做站外外发；外发与状态变更须审批+幂等+限速+可接管。影响结构：`backend/app/modules/workflows/digital/**`、企微对接配置。验收标准：①三类场景各端到端 1 次；②站外外发被拦截的反例；③审批流挂起时不动业务状态。完成定义：实测 + 更新记录。

---

## P4 · F09 工作台与通知前端

- [ ] **T-D22 管理员工作台**｜规模：M — 前置：P1、各域授权结果服务。做什么：岗位/档案/待复核/待评分/培训计划/人才缺口预警六类真实数量卡片（一律经各域授权结果服务读取，不直连他模块业务表）+ 业务入口。影响结构：`backend/app/modules/dashboard/**`、`frontend/src/features/dashboard/**`。验收标准：①六类数字与业务记录回查一致；②未公布成绩/内部反馈/人才池不出现（断言）；③新增业务数据后数量随之变化。完成定义：回查记录 + 更新记录。
- [ ] **T-D23 个人工作台**｜规模：S — 前置：同上。做什么：本人待测评/培训/近期结果（AccountScope personal 范围）。影响结构：同 T-D22 个人侧。验收标准：另账号访问 403；本人数据完整显示。完成定义：双账号实测 + 更新记录。
- [ ] **T-D24 通知中心前端**｜规模：S — 前置：T-D03。做什么：通知列表/已读/失败查看；按事件类型跳转源对象。影响结构：`frontend/src/features/tasks/**` 或独立 notifications 页。验收标准：12 类事件渲染、已读状态、越权不可见。完成定义：实测 + 更新记录。

---

## P5 · 验收、评测与发布

- [ ] **T-D25 指标一致性验收**｜规模：M — 前置：T-D08/T-D09、A/B/C 口径齐。做什么：白名单六类指标逐条与授权范围内人工核算比对（组织/筛选/分组维度）。验收标准：逐指标「一致」结论+核算 SQL 留档；不一致项回改。完成定义：核对报告 + 更新记录。
- [ ] **T-D26 越权负例全套**｜规模：M — 前置：T-D09/T-D10。做什么：表/字段/行越权、目录外、歧义、伪造参数、个人明细下钻、组织范围未授权——全部拦截且不泄露对象存在性。验收标准：负例矩阵全绿；组织范围负例等 A 扩展交付后补。完成定义：负例报告 + 更新记录。
- [ ] **T-D27 Neo4j 图谱落地与抽检**｜规模：M — 前置：B 知识入库链路、归属登记（**未登记，先补 ownership**）。做什么：图谱数据模型落地、档案确认/知识入库后自动抽取、RAG 溯源与技能推荐接入。验收标准：人工抽检准确率≥85% 且引用可定位；双写一致性反例。完成定义：抽检报告 + 更新记录。
- [ ] **T-D28 性能实测**｜规模：L — 前置：口径冻结（ORG-05）。做什么：向量检索≤500ms、报表≤3–5s、缓存命中≥98% 按冻结口径实测。验收标准：实测报告（P95/P99、样本、环境）。完成定义：报告 + 未达标项进阻塞清单。
- [ ] **T-D29 D 域发布**｜规模：S — 前置：测试全绿。做什么：按 AGENTS §4 流程推 Lon→master 并在 `docs/updates/D/` 发布登记。完成定义：四引用一致 + 登记完成。

---

## 完成定义（全局 DoD）

1. 代码 + pytest/vitest 通过（新功能带正反例，反例至少覆盖越权/幂等/降级）；
2. `check_scope --staged` / `--base master` 通过（整合模式提示留痕）；
3. `docs/updates/D/` 新增交接（含实际命令/结果/未测项）；
4. 涉及接口/表结构变化同步 `docs/modules/D/` 契约；
5. 涉及依赖变化已由 A 合入 requirements（P0 的 T-D00e 闭环）；
6. 验收标准逐条实测，未执行写未执行。

## 明确不做（本清单外）

A/B/C 域业务模块、权限矩阵终稿（A）、M3 人才需求实体（A）、Skills 组件市场化（PRD §8 后置阶段）、原生 App、站外外发。
