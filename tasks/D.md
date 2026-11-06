# D 工作包：模型任务、指标问数、报告、固定流程与工作台

维护角色：D；姓名由组内绑定。日期：2026-11-06。状态：公共调用/调度源码已导入，指标、报告和正式固定流程待实现，本机运行未验证。

先读根 AGENTS.md、README、更新记录、四个角色最新交接、目标路径规则及[统一需求 V2.2](../docs/需求文档.md)。接入事实见[D 契约基线](../docs/modules/D/源码接入基线.md)、[接入决策](../docs/源码接入与需求决策.md)和[来源参考区](../docs/imports/hhy_gycs_master/README.md)；全组安排见[四人分工](../docs/四人分工与试行方案.md)。README 运行入口尚未实测。

## 继承的现成文件

后端继承 core/llm_client.py、embedding_client.py、milvus_store.py、worker_state.py、core/prompts 的公共 loader/schemas 框架、tasks/runner.py、api/health.py。前端继承 lib/usePolling.ts 与 usePolling.test.tsx，保留 analytics/reports/workflows/dashboard/tasks 等既有新模块目录归属。

prompts 集中目录按业务拆分：parse.txt/profile.txt 由 B 维护，match.txt 由 A 维护；D 只维护公共加载/版本/结构框架及本包业务提示，不单独修改别人的业务输出语义。parse_worker 属 B，screen_worker/match_worker 属 A；公共 http/auth/types、components、Next.js 布局及 main.py 入口属 A。

唯一机器写入来源为[ownership.json](../.collaboration/ownership.json)，导入路径已登记；主要文件相对 backend/app 与 frontend/src。本文不复制完整清单，不因维护模型/任务框架取得其他模块业务权限。

## 剩余任务

1. 先复用 API lifespan 内的单进程 TaskRunner，保持 --workers 1 约束，核对注册、认领、并发、超时、有限重试、停机回写和恢复。现有 parse/screen/match 扫描不等于通用工作流；不新增平行调度框架，不宣称独立 worker 已落地。
2. 保留来源 Spark 与独立 Embedding 事实，验证 Agnes agnes-3.0-flash 的协议、文本/结构化输出、失败响应和超时；Embedding 端点/凭据/维度独立核对，不默认 Agnes 具备 Embedding/OCR。保留 Milvus 语义检索及明确降级状态。
3. 登记任务状态兼容映射：解析含 pending/running/success/failed/duplicate_pending/cancelled，批次含 pending/running/done/failed；不批量替换来源枚举，不把解析成功但向量失败藏成全成功。记录输入、模型/Prompt 版本和结果快照，不以 mock 或静态结果冒充真实模型。
4. 一期白名单指标问数：目录包含 metric_id、来源、公式、时间字段、去重键、允许参数/分组、权限及输出 schema。自然语言只解析受控指标与参数，后端校验后查询真实数据，目录外明确不支持；不执行模型原始 SQL。跨模块自由查询仅列二期候选，尚未批准为二期承诺。
5. 保存分析快照，提供真实表/图/解读，同快照生成 DOCX/PDF 并授权下载；补中文字体、长表、空数据、截断与解读失败处理。两种格式导出不重新问数。
6. 实现两条固定预置流程“解析→档案→匹配”“测评完成→能力分析→培训草稿”，记录分步输入输出和失败恢复，重试不重复产物；人工评价、录用与培训分配保留人为确认。可编辑画布放二期。双端工作台使用真实业务和任务汇总，不填演示常量。

## 跨包依赖与交付接口

A/B/C 各自落实只读数据服务、节点、业务提示、授权与指标口径；白名单内涉及模块组合的指标也需所属人确认，不由 D 猜字段或按姓名关联。person_id 先映射现有 talent_profile_id，版本及证据来自 B；D 不建平行主档。

A 接入依赖、公共配置、启动/路由和页面布局；D 提交自有任务组件，由 A 组装，不直接改 A 的共享组件。B 负责知识/档案分段与索引生命周期，D 维护向量框架。契约写 docs/modules/D/，跨包请求写 docs/requests/D/，业务服务由原维护人修改。

## 验收与检查点

真实模型调用成功与失败分别可追溯；任务中断恢复与阶段重试不重复业务结果，健康状态如实反映依赖。白名单指标的允许筛选/分组与人工统计一致，歧义、无数据、超范围、未授权明细明确处理。DOCX/PDF 数值与同一快照一致，中文、长表和图表可读，文件下载重新授权；两条固定流程有真实节点历史，工作台可回查原记录。

3–4 天为首轮检查窗口：先探针验证 Agnes、独立 Embedding/Milvus、单进程 runner 和中文报告，再评估指标/流程剩余工作，不保证全范围四天完成。外部运行探针未执行；历史 588 项测试不能作为本次业务通过，协作检查通过也不等于业务验收。

后续代码变更按本人 clone/分支和机器范围实施，新增 docs/updates/D/ 交接，保持未测/待联调状态准确。本轮完成源码导入与协作基线调整；业务代码原样保留，外部运行探针未执行；不授予业务角色。
