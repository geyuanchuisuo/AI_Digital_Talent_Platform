# 原 PRD 全员统一执行跟踪表

维护人：MASTER；核对日期：2026-11-11；本轮集成基线：`97b952c`；上次 MASTER 发布：`d266813`。原 PRD 与冻结 V2.6 仍为必达基线。历史补登保留在文末；本轮以当前源码、各域交接及个人远端差异重新核对，不将历史四 ref 一致或测试记录当实时状态。详见 [本轮进度审核](20261111-项目进度审核.md)。

## 标记与执行规则

- **× = 对应粒度的代码已实现，有本表列出的源码或交接证据；不是失败标记，不等于接入完成或验收通过。空白 = 未实现、仅部分实现、存在关键偏差或缺少足够证据。** 库存在/包已安装/计划已勾选均不能单独打 ×；同一行包含多个能力时，不能因其中一项有代码就整行打 ×。拆出的基础能力与其完整目标不能重复计为完成率。
- 已打 × 的条目仍按“证据与验证边界”和“下一步/通过条件”继续联调验收；未标 × 的条目不能从原始必达范围删掉。每次更新注明基线和新证据，未提交代码不计入主干已实现。
- P0：先纠偏或打通依赖；P1：完成主流程及已实现项的联调；P2：企业/质量/完整交付。优先级仅安排先后，不代表低优先级可省略。实际日期由负责人按依赖回传，不能据优先级编造工期。
- A/B/C/D 开工按自己的 ID 行推进；回传本人 `docs/updates/<角色>/` 的条目 ID、提交、输入/预期/实际、真实依赖或替身、未测项、下个检查点。Master 审核后更新本表。其他人不改写他人验证结论，不批量把“代码实现”改成“验收完成”。
- 当前表由源码静态核对和既有交接形成；本轮未重跑产品测试/真实模型/数据库/浏览器。所有原性能/准确率目标仍需独立验收，不能拿单元测试数量代替。

## 业务与公共能力

| ID | 牵头/配合 | 优先级 | 必达条目 | 已实现 | 源码或交接证据 | 接入/验证边界 | 下一步/通过条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A01 | A | P1 | 岗位维护、版本号递增与多岗位申请基础 | × | backend/app/service/job_profile_service.py:80；backend/app/modules/recruitment/service.py:155 | 基础代码已接入；不包含 A08 的完整申请输入快照，也不代表招聘全链验收 | 验证双岗位互不影响、关闭/重开、对外字段与权限；完整申请快照按 A08 补齐 |
| A02 | A | P0 | AI 综合匹配评分与人工推进 |  | **A02-1**（`5eb81c7`）：删除 `evaluate()` 硬淘汰提前返回分支，新增 `classify_rule_gaps()` / `build_rule_review_text()`，`hard_fail` 恒 False、不符项/待核实进 `misses` 并随 Prompt C 输入交模型；`match.txt` 改为「所有候选人都进 prompt，不符项/待核实仅作参考，不得据此扣为 0-39 分」；定向三文件 **exit 0 / 33 passed** | A02-1 子单元已交付（A 记录 `UPD-A-20261110-1100-A02-1取消硬门槛自动淘汰`）；**A02 整行未完成**——旧算法替代未消除，综合分 `overall_score` 仍待 D 的 `PROMPT_C_SCHEMA` 契约（A→D 正式请求 `20261110-D-A02-2-PromptC综合分契约.md` 仍打开） | A02-2：消除旧算法替代 + 综合分口径；缺失资料待核实、内部建议不自动推进/淘汰；低分不自动淘汰、硬条件不符仍显示 |
| A03 | A/B | P0 | 批次开始时冻结档案版本、匹配输入与历史快照 | × | **A03**（`c926fbc`，A 记录 `UPD-A-20261110-1742-A03批次档案冻结`）：两批次表加可空 `frozen_inputs_json`（迁移 `A_0012_profile_freeze` 挂 `A_0011` 成单 head，Inspector 双路径幂等）；`screen_service` 新增冻结函数组（`capture_batch_profile_inputs` / `build_profile_input` / `frozen_profile_input` / `frozen_inputs_summary`），`match_service` 复用；两 worker **只读冻结输入、不再读当前档案**（当前档案仅用于软删跳过），缺冻结列的历史批次显式告警回退；冻结结构 `A03-v1`：成员评分输入值拷贝 + B 正式版本号 / `version_source` / `snapshot_at` / `content_sha256` / `persisted_content`；两个 `BatchDetail` DTO 补 `frozen_inputs` 摘要；定向 **exit 0 / 20 passed**、受影响既有 10 文件 **exit 0 / 292 passed**、deselect 后全量 **exit 0 / 909 passed / 8 skipped / 1 deselected / 0 failed**、前端 vitest **29 文件 131 用例** / `tsc` exit 0 | 已交付并发布 master；批次级值拷贝而非只存版本号（自包含、不受历史版本清理影响）；绑定 B 正式版本但不改写 A 侧评分口径；只冻结内容不冻结存在性（软删跳过语义保留）；幂等 replay 不重新冻结、档案变更新建一轮仍成立；详情只回摘要不外泄全量；`A_0012` 只在隔离库生效，主验收库 `ai_talent` 未应用 | 排队期间修改档案不影响本批、重试和历史回读不读取新版本——实现已就位，真实 MySQL 联调与浏览器验收仍待做；与 B 正式档案版本（B03/B10）的端到端联调待 B 侧回传 |
| A04 | A | P1 | 人工复核、分数与推进结论分离 | × | backend/app/service/{match_service,screen_service}.py | 代码存在；整体新评分口径待联调 | 结合 A02 验证刷新、重试、阈值与人工结论不覆盖 |
| A05 | D/A | P1 | 岗位缺口预警的判断、触发及通知 |  | docs/modules/D/通知契约.md:22；backend/app/modules/notifications/producers.py:206 | 已有缺口统计/通知封装，未找到预警生产函数业务调用；D 为预警生产方，A 提供需求/录用事实；储备推荐见 A09 | D 牵头阈值、触发/解除、去重及通知投递；A 交付授权需求与人工录用口径 |
| A06 | A | P0 | 员工/候选人身份与完整组织/角色/行/字段权限 |  | core/auth/scope.py 多职责/部门/字段工具与门禁已补；api/talents.py:228；service/talent_service.py:438 | 完整权限整行仍未收口：人才搜索未传调用者范围，快照未全面承接部门范围；不能以工具和局部门禁代替全域授权 | 保留已交付子能力；补B11及逐入口/任务/导出/跨部门反例与真实角色联调 |
| A07 | A | P1 | 员工邀请激活、候选人验证注册/找回/会话撤权 |  | 统一需求 §3；预置账号不等价完整身份交付 | 未获完整实现证据 | 建立完整流程及负例；密码/验证码/停用/撤权分别验收 |
| A08 | A/B/C | P1 | 独立申请的岗位/JD、简历内容快照及历史引用 | × | **A08**（`729fc60`）：`Application.input_snapshot_json` + `input_snapshot_at`；新迁移 `A_0011_app_snapshot`（挂 `A_0010` 成单 head）；`capture_application_input_snapshot` 纯读值拷贝（job_profile / jd_publication / talent_profile / resume_file / parsed_resume 五段 + `missing_sources`，原文 sha256+长度固定）；列表按 `account_is_management` 分流、个人侧强制 `talent_profile_id = AccountScope.person_id`、出参 6 字段白名单 `_application_public_out`、未确认终态结果置 None；新增 `GET /applications/{id}/input-snapshot` 挂 `require_roles("system_admin","hr")`；定向 **exit 0 / 22 passed** | 已交付（A 记录 `UPD-A-20261110-1644-A08申请输入快照与对外字段分级`）；原文不整段复制（hash+长度）、缺源容错不阻断创建、面试/邀约引用不重复冻结（C 外键已承载）；主验收库 `ai_talent` 的 `A_0010`/`A_0011` 尚未补齐 | 真实模型、迁移、双角色浏览器与历史快照联调仍待验证。 |
| A09 | A/B/D | P1 | 岗位缺口对应的储备人才推荐 |  | 统一需求 F03、原 PRD §2.3；基础检索/匹配不等于自动形成授权推荐闭环 | 未获完整实现证据；与 D 负责的预警生产 A05 分开跟踪 | A 牵头招聘推荐规则，B 供授权档案，D 接预警流程；推荐有依据且不自动推进/淘汰 |
| A10 | A | P1 | 项目、项目群与项目人才需求基础接口 | × | backend/app/modules/recruitment/models.py:69；router.py:36；service.py:44 | 模型及创建/列表/需求维护已有代码；完整权限、审批及统计另验 | 按 F01 验证项目归属、需求人数与缺口口径、重复提交及权限；审批见 A11 |
| A11 | A | P1 | 岗位/人才需求审批及审批记录 | × | **A11**（`58e1240`，A 记录 `UPD-A-20261110-1840-A11需求审批链`）：审批状态与业务生命周期 `status` **分离**——`project_demands` 加 5 列（`approval_status` / `approval_version` / `approved_version` / `approved_by` / `approved_at`）+ 只追加审计表 `demand_approval_records` + 联合索引（迁移 `A_0013_demand_approval` 挂 `A_0012` 成单 head）；新增 4 端点 `submit` / `decision` / `withdraw` / `approval-records`，三套 `require_roles` 职责门禁（HR 发起、`dept_manager`/`system_admin` 审批、记录只读）；`draft→pending→approved/rejected/return→draft` 合法流转、重复提交/重复批准 409、拒绝与退回必填原因、四眼原则按用户 id；`approve` 冻结 `approved_version`，改内容触发 `approval_version+1` + `revision` 记录使旧审批失效；`matchable` 计算字段只在「已批且版本未失效」为真；定向 **exit 0 / 20 passed**、受影响既有 5 文件 **exit 0 / 205 passed**、**空库全链 upgrade exit 0**（临时库验后已 DROP，88 表）、**原样全量 exit 0 / 932 passed / 8 skipped / 0 failed**、deselect 全量 exit 0 / 931 passed、前端 vitest **29 文件 131 用例** / `tsc` exit 0 | 已交付并发布 master；审批状态不可经 `PATCH` 伪造；`matchable` 只交付判定与服务层纯函数，**未接入匹配入口**（匹配侧按 `job_profile` 圈人，联动属 A05/A09 后续边界）；`A_0013` 只在隔离库/临时空库生效，主验收库 `ai_talent` 未应用 | 审批链真实 MySQL 联调与浏览器验收待做；审批失效后重新提交的版本演进、越权/重复批准反例在定向测试已覆盖（20 例），真实角色账号联调待 A07 身份链齐套 |
| A12 | A/D | P1 | 真实 AI 可编辑 JD 草稿与人工确认发布闭环 |  | 统一需求 F01；现有 JdPublication 记录不能证明真实模型草稿与确认链完成 | 未获完整实现证据 | 真实模型成功/失败、人工编辑保存、内容版本及确认发布全过程验收；投放记录见 A13 |
| A13 | A | P1 | JD 投放内容/渠道/版本记录与撤回基础 | × | backend/app/modules/recruitment/service.py:229、254 | 创建 published 记录、内容快照及撤回已有实现；不代表外部渠道实际投递或 A12 已完成 | 验证撤回、历史内容和授权；外部渠道按客户确认的边界接入，不默认连招聘平台 |
| A14 | A | P1 | 员工花名册及任职记录 |  | 统一需求 §6；账号绑定与简历工作经历不能替代员工任职业务记录 | 未找到完整业务实现 | 明确员工/部门/岗位/任职起止与调岗历史契约，并供盘点、权限与培训消费 |
| A15 | A/B | P1 | 执行评分时的经历/项目证据及结果输入留存 | × | screen_service.py:218、447；screen_worker.py:140；match_worker.py:110 | 新批次已消费A03冻结输入；历史缺冻结批次仍显式告警兼容回读当前资料；本轮未运行业务 | 验证结果可回读实际输入/模型版本，新批次排队与重试不漂移；历史兼容边界单列 |
| B01 | B | P1 | 文本 PDF、DOCX 提取与批量解析 | × | backend/app/service/extract_service.py；tasks/parse_worker.py | 代码及既有链路存在；原准确率未验收 | 补混合批次/损坏/重复/重试证据 |
| B02 | B/A | P0 | 图片、扫描/混合 PDF OCR | × | de77e51；service/extract_service.py:90、210；modules/files/ocr.py:66；tasks/parse_worker.py:162 | 图片/扫描/混合PDF逐页OCR主链已接，成功/失败页可保存；实际模型部署、困难样本和99%准确率未在本轮验收 | 真实四格式/OCR超时/损坏混批、逐页核对与重试验收 |
| B03 | B | P1 | 稳定人员映射与持久化档案版本 | × | modules/talents/history.py:52、98；service/profile_service.py:154、164 | 持久版本与来源页/字段证据生产已接入；引用定位与覆盖限制见B10 | 验证人工修订、重解析、撤权、版本与原件一致；跨包消费另验 |
| B04 | B | P1 | 智能查重、分层合并、可追溯撤销 |  | 现有 dedup 与版本服务仅覆盖部分目标 | 部分 | 按冻结规则实现自动/人工分层、冲突处理、撤销与原件保护 |
| B05 | B/D | P1 | 人才基础向量语义检索 | × | backend/app/service/talent_service.py:438；backend/app/core/milvus_store.py:249 | Embedding→Milvus→排除软删已实现；组织/行授权范围尚未接入，见 B11；重排见 D22 | 用评测集核对召回；基础检索不得作为授权检索或完整 RAG 验收 |
| B06 | B | P1 | 百级标签体系与真实画像依据 |  | 现有画像/标签不证明百级体系完整 | 未获完整实现证据 | 交标签字典/来源/覆盖证据，禁止为个人凑标签 |
| B07 | B/D | P0 | 制度知识 RAG：入库、权限、引用、拒答、删除 |  | backend/app/modules/knowledge 当前主要规则占位 | 缺口 | 完成完整后端与页面，提供引用、权限和删除反例 |
| B08 | B/D | P0 | 单人/批量档案 RAG 问答 | × | f2e8d1a；modules/talents/qa_service.py:10、56；qa_index.py:14；qa.py:44；api/talents.py:64、71 | 单/批档案问答、人员/版本过滤、引用校验和逐人失败隔离已接；历史合成小样本不证明RAG95%或完整组织撤权 | 真实模型/索引、同名/不足拒答、引用版本、多人覆盖及组织权限端到端验收 |
| B09 | B/D | P2 | Neo4j 人才/技能/岗位/制度/证书图谱（V2.6细化） |  | 统一需求 F06；依赖已登记，未见完整抽取/查询闭环 | 待实现 | 档案确认/知识入库后自动抽取；关系抽检准确率 ≥85% 且引用可定位，验证双写/删除/权限，不替代 Milvus/RAG |
| B10 | B | P0 | 逐页原文证据与字段引用的生产链 | × | parse_worker.py:166→files/processing.py:34；profile_service.py:154→talents/history.py:98；talents/evidence.py:32 | 逐页来源与字段证据生产已接；DOCX为文本定位，不保证物理页码；候选原文片段不等于全字段覆盖或自动确认 | 四格式核对页/片段/字段/版本，人工修订和证据确认分离；复杂文档质量单独评测 |
| B11 | B/A/D | P0 | 人才检索的组织/行授权过滤与撤权一致性 |  | backend/app/api/talents.py:177；backend/app/service/talent_service.py:438、467 | 当前只有管理门禁，检索服务无调用者范围入参、Milvus 无授权过滤；不能仅写“待实测” | A 供范围契约，B/D 在召回前过滤并返回前复核，验证跨部门/撤权/软删，不将受限事实送入 AI 上下文 |
| C01 | C | P1 | 默认自适应选题、千人千卷与策略留痕 | × | modules/assessments/adaptive.py；service.py:546、920、961 | 自适应选题与全文题库冻结已补，详C10；历史ID-only方案及停用题过滤有兼容边界 | 动态路径、重复/恢复、策略版本与历史方案分别验收 |
| C02 | C/D | P1 | AI 出题/简答评分、成绩版本与能力雷达 | × | assessments/{generation,grading,scoring}.py | 代码存在；真实模型与 JWT 未完整验收 | 真实模型成功/超时/畸形返回与人工复核逐项核验 |
| C03 | C/A | P1 | 多轮人工面试与结构化内部反馈 | × | backend/app/modules/interviews/{service,router}.py | 代码存在；真实双端权限待验证 | 验证面试人/候选人字段隔离、改期/取消和历史 |
| C04 | C/D | P1 | AI 面试间（V2.6细化） | × | backend/app/modules/interviews/ai_router.py；ai_judge.py | 代码存在；不能借替身测试声明真实评分完成 | 真实模型、失败重试和本人范围浏览器验收 |
| C05 | C/D | P1 | 真实课程、个性化培训草稿及分配 | × | backend/app/modules/training/{drafting,service}.py | 代码存在；模型与跨域输入待联调 | 课程不存在返回缺口；建议不自动分配/改正式评级 |
| C06 | C/A | P1 | 完整报名审批与候补员工24小时确认 |  | 7177402；training/service.py:1633、1760、1792、1705 | 已补awaiting_confirmation及本人24小时确认/超时递补；仍为管理侧单步审批，免审批配置/直属经理/费用复核链未完整 | 补完整职责审批与不可绕过本人确认边界；并发容量与真实通知联调 |
| C07 | C | P1 | 匿名课程评价、7天窗口、跨期去重及双5门槛 | × | training/{service,feedback_themes}.py | 代码存在；真实主题归纳待验收 | 验证拒绝原文泄露及小群体/单讲师拆分推断 |
| C08 | C/A | P0 | 防挂机与可信学习证据、正式完成条件 |  | ab880be；training/schemas.py:129；service.py:1989、2044、1905、1834 | 已禁客户端分数/验证人，补可信来源、防重放和分数门槛；学习时长仍按签发后经过时间，固定60分未按课程条件判定 | 补有效学习/防挂机与按课程条件完成；无交互等待不能等价有效学习 |
| C09 | C/A | P0 | 正式人才盘点、经理初评/HR确认与批准的九宫格规则 |  | fb35ca8+aee2481；inventory/service.py:102、121、306、335、25 | 已补提交完整性/确认状态/HR批准阈值配置与版本；部门经理仍被统一管理门禁拒绝，填经理分不等于经理本人初评 | 接经理真实初评与负责范围权限；业务批准正式阈值，验证各状态与历史 |
| C10 | C | P0 | 发布时冻结题库内容、答案及评分版本 | × | fb35ca8；assessments/service.py:500、546、920、961 | 已冻结题干/选项/答案/评分指导并按冻结内容选题；历史ID-only回退现库，发布后停用题仍被过滤 | 验证修改后旧内容不漂移；历史迁移与停用语义需明确，不宣称停用完全无影响 |
| C11 | C | P1 | 错题快照、开放校验与复习事项 | × | backend/app/modules/assessments/service.py:1184、1465 | 已有原答题快照引用与复习完成标记；真实身份/浏览器待验收 | 验证公开版本、未答题答案不可提前暴露；可调整/取消的独立复习计划仍待单独审批，不自行列为一期必达或已实现 |
| C12 | C/A/D | P1 | 分阶段岗前培养计划（目标、阶段、课程与导师） | × | a3e21ef；training/service.py:571、603、641、669、693、722；frontend training/page.tsx:1238 | 阶段目标、导师、真实课程校验、分配与修订版本已实现；真实迁移/浏览器/课程衔接未验 | 真实员工权限、阶段/导师/课程版本及修改历史端到端验收 |
| C13 | C/A | P1 | 跨测评/面试/正式培训/HR能力事件的成长轨迹 |  | fb35ca8+83c9123；training/service.py:946、2071、993；interviews/service.py:185 | 正式完成事件、四域聚合与页面已补；同维度多次测评按assignment升序首次命中，可能取较早结果，来源定位仍不足 | 修正最新事实选择、补结果ID/未评估语义；真实测评/面试/培训事件与前端验收 |
| C14 | C/A | P1 | 实践成果、经理评价与HR审核后的正式能力评级 |  | 80e2bc1；training/service.py:791、821、855、885、907 | 实践/经理评价/HR审核/版本评级与事件已补；经理/HR负责范围未校验，本人评级历史返回需与F10内部评级不可见规则对齐 | 补部门与HR授权范围、内部评级字段隔离和越权反例；不以流程存在宣称正式评级全闭环 |
| C15 | C | P1 | 基础报名、管理员审批占位/释放及延期记录 | × | training/service.py 基础报名/审批/释放/延期及本人候补确认 | 本人24小时确认已补；完整经理/培训职责链仍在C06，真实并发与定时待验 | 验容量、超时递补、撤回、延期历史和通知，不替代C06 |
| C16 | C | P1 | 盘点录入/计算、人才池、异议与IDP基础 | × | inventory/service.py；scoring.py；frontend inventory 页面 | 基础盘点/人才池/异议/IDP及确认状态/批准配置已补；经理权限与负责范围仍缺 | 联调匿名/导出/历史；真实经理初评按C09补齐 |
| D01 | D/A | P1 | Redis 缓存/会话/限流基础设施 | × | backend/app/core/cache/ | 客户端/中间件实现存在；非整站保护已启用 | 隔离键空间与故障边界，补真实 Redis 与命中率证据 |
| D02 | A/D | P0 | API 限流/黑名单/熔断/降级实际生效 |  | backend/app/main.py:116；RATE_LIMIT_ENABLED 客户模式开关已实现 | 默认演示仍豁免 /api；启用后业务接口进入限流/黑名单；完整熔断与真实Redis环境隔离未验收 | 客户模式实测429/403、Redis故障降级与隔离键空间，不再把开关误报为缺失 |
| D03 | D | P1 | PostgreSQL 只读副本与 ETL | × | scripts/D/etl_pgsql.py；deploy/postgres/；T-D02记录 | 代码+已有真实库演练记录；本轮未复测 | 敏感排除/水位/幂等/延迟和授权一致性验收 |
| D04 | D | P1 | LangGraph 状态图及持久检查点 | × | backend/app/core/workflows/engine/；workflows/nodes/graph_builder.py | 代码存在；新状态转换部分仅静态验证 | 恢复/审批等待/取消迟到回执与并发边界验收 |
| D05 | D | P1 | 流程定义、版本、审核发布/停用/回滚 | × | backend/app/modules/workflows/{models,service,router}.py | 代码存在；完整运行闭环待验证 | 权限、版本锁定、审核人与提交人隔离及回滚验收 |
| D06 | D | P1 | 五类业务节点适配器及人工审批实现 | × | backend/app/modules/workflows/nodes/business.py:160；human_approval.py | 五类执行器/审批 interrupt 已有代码，不等于五类真实业务全链通过；C/D 身份衔接待联调 | 实际服务输入/输出、错误和授权验收；员工本人触发后培训草稿节点需采用受控服务身份，不能绕过管理员服务门禁 |
| D07 | D/B/C | P0 | 解析/测评/知识入库三类业务事件到流程的完整接线 |  | backend/app/modules/assessments/service.py；workflows/run_service.py；C 的 20261109-112028 发布登记 | C 测评生产方与 D 桥接已合入 141927c；60 项隔离测试为历史证据，真实引擎/已发布事件定义未联调，B 解析和知识生产方仍待接线 | D 复核本人/分配人身份与发布定义，B/C/D 联验三类事件、重复/招聘排除/事务失败；不能把 C 当作仍仅本地推进 |
| D08 | D | P1 | 定时触发、取消、失败重试与重启恢复 | × | **T-D18**（D 记录 `UPD-D-20261109-120119-T-D18失败重试与运行取消` / `UPD-D-20261109-124723-T-D18定时触发`）：`schedule.py` 扫描器（sys_settings 开关**默认关闭**、管理侧 GET/PUT 含 interval/参与定义/执行主体、`updated_by` 落审计、daemon 线程随 lifespan 30s 一轮、到期按最近 schedule 运行判定、无执行主体只登记不执行如实可见）；`run_service.retry_failed_run`（failed→running CAS 占用 + 引擎检查点续跑，已成功节点不重执行）与 `cancel_running_run`（双侧 CAS + 节点执行前协作取消检查、取消后拒绝迟到回执与重试）；重启恢复复用 T-D04 检查点（main.py runner 启动无条件 running→pending 恢复 + `test_waiting_survives_engine_restart_and_recovery`）；完整运行= `test_manual_run_data_analysis_succeeds_and_records_steps` 真实端到端（registry 准入→副本 SQL→快照落库）；测试 `tests/D/test_workflow_schedule_t18.py` 11 例 + `test_workflow_retry_cancel_t18.py` 12 例 + `test_workflow_nodes_t17.py` 16 例 + engine/lifecycle 12 例 | 本机复跑（2026-11-11）：上述 5 文件 **exit 0 / 51 passed / 0 failed**（13.55s）；跑前按 P3 修复 4 个工作流测试文件的 A06-1 门禁会话重定向（纯测试侧：env 夹具 `monkeypatch app.database.SessionLocal` + 建空 `sys_roles`/`sys_user_roles` 表回退 `users.role`，见本轮 MASTER 记录，未改任何生产代码） | 真实 MySQL 运行验收与浏览器验收按用户口径延后归集中验收；三条业务事件端到端联调归 D07（B 生产方待接线）；schedule 与 T-D21 数字员工的定时触发接线一期未接（默认关闭口径） |
| D09 | D | P0 | Dify 私有化及业务对接 |  | Dify 1.19.0 历史部署记录；workflows/dify_bridge/（8c71827） | 已有DSL导入/导出/对账和回写适配；真实业务应用、画布往返及升级/回滚仍待联调 | 复核运行环境，完成Dify应用/版本/业务回写与权限闭环 |
| D10 | D | P0 | 可视化节点/连线画布与 Dify 对接 |  | **Dify 对接半边已交付（T-D16，2026-11-11）**：`backend/app/modules/workflows/dify_bridge/`（dsl/service/writeback）+ `/api/workflows` 3 端点（导出/显式导入/对账）+ `sync_run_status` 终态回写钩子（env 门控默认关）；映射以 Dify 1.19.0 锁定源码实核（DSL 0.7.0 envelope，`ait_<域节点类型>` 1:1 映射）；`tests/D/test_dify_bridge_t16.py` **14 例全绿**（导出→导入图恒等/reconcile identical、异类节点 422 不静默丢弃、导入只建草稿不碰发布版本、回写门控）+ workflows 回归 68 例。契约 [Dify画布对接契约](../../modules/D/Dify画布对接契约.md) v1.0 | 隔离测试已达；**真实 Dify 画布可见可改可回传（验收①画布半边）与回写接收端接线待联调**（Docker/WSL 未运行，待环境恢复）；Dify 画布 UI 对 `ait_*` 自定义字段保留程度以联调为准（对账机制兜底，验收③语义） | **前端可视化画布编辑器缺口**：`workflows/page.tsx:467` 仍为 JSON 整图输入框，需替换为节点/连线画布（前端单元另行交付）；真实 Dify 往返演练证据后补 |
| D11 | D | P1 | Skills 组件库与版本发布 | × | **T-D19**（D 记录 `UPD-D-20261109-133022-T-D19-Skills组件库`）：`backend/app/modules/workflows/skills_{router,service}.py`（组件受控生命周期：创建→审核→发布→引用，含 schema/参数/工具绑定）；`main.py:217-220` 注册 `/api/workflows/skills`；`tests/D/test_workflow_skills_t19.py` 10 例，定向 **10 passed**、`tests/D/` 全量 **177 passed / 12 skipped** | 隔离测试已达（SQLite 夹具）；当时 `test_models_structure.py` 3 处失败为 **C_0006 预存在问题**（归属 C，与 Skills 无关，D 已如实登记）；真实 MySQL 与浏览器未验 | 编排引用与审计的真实联调验收；与 D05 流程定义联验版本引用与停用后行为 |
| D12 | D/A | P1 | AI 中台：模型/Prompt/Agent/调度/运行管理 | × | **T-D20**（D 记录 `UPD-D-20261109-150506-T-D20-AI中台`）：`backend/app/modules/dashboard/{models,service,router}.py`（AiProvider / AiPromptVersion / AiCallLog 三 ORM + 白名单枚举；供应商 CRUD/启停/默认切换 CAS 唯一、Prompt 发布后不可变 + 同 name 唯一生效版、record_call 审计旁路、usage_summary 看板）；`/api/ai-platform` 10 端点（`main.py:222-225`）；迁移 `D_0011_ai_platform` 挂 `D_0010`；`llm_client.py` 增 `provider_config_factory`（启停/切换即时生效）+ 成功/失败两路 `_record_call` 埋点；`tests/D/test_ai_platform_t20.py` 12 例 | 隔离测试已达：定向 + LLM 客户端既有回归（`test_llm_client_unit` / `test_llm_client_fake_server`）**46 passed**；`llm_client` 为公共唯一 LLM 入口，D 已回归其 34 例无破坏；真实模型与浏览器未验 | 逐块管理入口的真实联调与授权验收（用量/错误追溯的浏览器验收待做）；调度/运行管理的编排联动待与 D05/D08 联验 |
| D13 | D/A/B/C | P1 | 指标目录及首批六类统计 |  | backend/app/modules/analytics/service.py:255；docs/modules/D/指标目录.md:4 | 六卡中2类有查询实现（需求缺口、匹配参考达线），另有活跃岗位切片，不是3/6；卡1人工推进子项覆盖仍需补 | 各域登记口径，补解析/测评/正式课程完成/学习自报并分离状态；缺口卡拒答而非编造 |
| D14 | D | P0 | Vanna.ai 原生自由问数 |  | analytics/nl2sql/generator.py:7 当前 OpenAICompatGenerator | 与严格Vanna基线存在偏差 | 接 Vanna 原生，不以普通模型适配器/安装包替代集成 |
| D15 | D | P1 | 候选SQL校验、受控执行与查询审计 | × | analytics/nl2sql/{validator,executor,service}.py | 代码存在；真实生成/权限全链待验收 | 验证只读、表/列/行权限、上限、超时与拒答，不执行未审SQL |
| D16 | D | P1 | 多维图表及图形进入同快照 DOCX/PDF | × | backend/app/modules/reports/chart_render.py:81；reports/service.py 的 add_picture/RLImage；UPD-D-20261111-021300 | 图形已从同快照渲染PNG并进入两格式，限登记指标bar/number；自由问数未产图表规格，版式/字体人工验收未做 | 验证中文/长表、图表数值、授权下载；自由问数图表仍需与D14补接，不自动视为接通 |
| D17 | D | P1 | SSE 任务进度端点 | × | backend/app/core/tasks/events.py；main.py已挂载 | 代码实现；权限/重连/终态运行验证待做 | 验证Last-Event-ID边界及降级，不宣称逐事件历史重放 |
| D18 | D/A/B/C | P1 | 通知服务基础 | × | backend/app/modules/notifications/ | 模型/路由/生产方函数存在；各事件投递非全部闭环 | 逐事件核对接收权限、幂等、失败重试及生产方接线 |
| D19 | D/A | P1 | 双端真实工作台与缺口告警 | × | **T-D22/23**（D 记录 `UPD-D-20261109-173107-T-D22管理员工作台` / `UPD-D-20261109-193831-T-D23个人工作台`）：**管理端** `dashboard/overview.py`（admin_overview 六类计数，`_demand_gaps` 复用 analytics `_GAP_SQL` 同口径）+ `GET /admin-overview` + `frontend/src/app/(main)/dashboard/{api,page,page.test}.tsx` 六卡页面；**个人端** `dashboard/me.py`（me_overview 三要素：待测评 / 培训 assigned-only / 已公布结果，`_require_personal` 门禁）+ `GET /me-overview` + `frontend/src/features/dashboard/{me-api,me-dashboard}.tsx` 卡片组件；`tests/D/test_dashboard_{t22,t23}.py` 共 20 例 | 隔离测试已达：t22 **12 passed** + t23 **8 passed**、D 域全量 **209 passed / 12 skipped**；前端 vitest 管理端 4 例 + 个人端 2 例（全量 113 passed 基线）；`AppShell.tsx` 菜单项与 `(person)/home` 挂载为 **A 归属公共布局**（跨归属一行，D 已透明登记，A 集成时可复核）；jsdom 组件测试，真实浏览器/登录态未验 | 真实数量经授权结果服务读取的浏览器验收；可回到业务记录核对；缺口告警与 A05 预警触发的联动待 D07 接线后联验 |
| D20 | D/A | P2 | 数字员工/企业微信与三类只读场景（V2.6细化） | × | **T-D21**（`52b6d24`）：`backend/app/modules/workflows/digital/` 新增 models（MemberImAccount / DigitalTask / DigitalOutboundRequest 三 ORM + 枚举白名单）、service（三类 collector 全只读：analytics 快照近 24h / parse+screen 队列计数 / workflow waiting + 培训逾期）、router（`/api/workflows/digital` 10 端点）；新迁移 `D_0012_digital_employee` 挂 `D_0011` 成单 head、83 表基线；`main.py` 注册 router（A 公共入口，跨模块）；新增 `tests/D/test_digital_t21.py` **15 例**；新建 [数字员工契约](../../modules/D/数字员工契约.md) v1.0 | 已交付（D 记录 `UPD-D-20261110-024500-T-D21数字员工纯站内版`，`pytest tests/D/test_digital_t21.py` **15 passed**、D 域全量 224 passed / 12 skipped）；**用户 2026-11-10 clarify 选定「方案一纯站内」**——一期不依赖任何 IM 平台凭据，三类只读场景端到端终点是入库 + 站内通知，全程不调 IM API；`member_im_accounts` 一期 manual 登记，channel 预留 wecom/dingtalk/feishu；外发红线已固化（request_outbound 只落 pending、decide_outbound 通过只置 approved 且四眼 403/重复 409、mark_outbound_sent 仅 approved 可置 sent） | 联调与业务验收未达；IM 平台凭据到位后补 oauth 绑定与 adapter（表结构不变）；一期定时未接线 |
| D21 | D | P1 | 独立 Embedding 适配与1024维校验 | × | backend/app/core/embedding_client.py:109、158；统一需求 §1 | 百炼 text-embedding-v3 适配已有实现；本轮未调用真实服务，不据此称离线推理已交付 | 验证批量/查询向量、维度、失败与凭据隔离；本地推理按 G05 独立验收 |
| D22 | D/B | P1 | 百炼 gte-rerank 与私有化 bge-reranker 重排 | × | **D22 适配层**（D 记录 `UPD-D-20261111-094900-重排双模式与本地模型接入`）：`backend/app/core/rerank_client.py`（新）——三模式门控（`RERANK_MODE`：off 默认不重排 / api=百炼 gte-rerank HTTP 端点（复用 Embedding 供应商体系）/ local=进程内 sentence_transformers CrossEncoder 加载客户本地模型目录）；配置缺失/初始化失败明确报错不静默；`tests/D/test_rerank_d22.py` **13 例全绿**（门控矩阵/httpx 假传输 results 形状与降序/缺包缺路径报错含安装指引/维度校验/探活同口径）+ **真实本地模型冒烟**：本机 `E:/AI_sentence_transformers/models/BAAI--bge-reranker-base`（XLMRoberta 分类头，CPU）加载推理分数 0-1 降序；配套 embedding 双模式（`EMBEDDING_MODE=api/local`，本地 `BAAI--bge-large-zh-v1.5` 真实推理 **1024 维 == Milvus collection 冻结维度**，无需重建集合）+ `.env.example` 双模式模板 | 隔离测试与真实本地模型冒烟已达；**检索链路接入（召回后调用）待 B 的 F06 检索改造**——适配层就绪、业务接线未做，不宣称检索已重排；api 模式真实百炼调用未验（无 Key）；主干完成状态以本轮集成后的实际内容为准 |
| D23 | D | P1 | 同快照 DOCX/PDF 文本与数据表导出基础 | × | backend/app/modules/reports/service.py:44、122、176；chart_render.py | 文档导出已有实现，图表目前转成数据表；图形导出仍在 D16，不能把本行当完整报告验收 | 验证不重新问数、中文/长表、下载授权与同快照；待 D16 补图形后共同验收 |
| D24 | D | P1 | 问数结果反馈/纠错入口及快照关联 | × | backend/app/modules/analytics/feedback/service.py:25、59；frontend/src/app/(main)/analytics/page.tsx:201 | 后端与前端入口已接入；历史局部测试不代表本轮浏览器验收 | 验证仅创建者、幂等、快照与审计关联、反馈不改结果，以及组织/字段权限 |
| D25 | D | P1 | 基于真实分析结果的智能解读与决策建议 |  | backend/app/modules/analytics/insight.py:28；统一需求 F07 | 当前为事实/推断分段及规则模板建议；完整智能建议与自由问数覆盖未获实现证据 | 以真实结果生成可核对依据，区分事实/推断、不冒称因果；模型不可用明确说明 |
| D26 | D | P1 | 已登记指标的网页图表基础 | × | backend/app/modules/analytics/charts.py:17、32；frontend/src/app/(main)/analytics/page.tsx | 已支持数值卡/柱图和登记维度；不等于自由问数多维图表与报告图形全部完成 | 验证表格/图表一致、截断/空结果；完整图形能力按 D16 推进 |
| G01 | A/全员 | P1 | PC/手机浏览器、中文英文界面 |  | 已有个人页/响应式局部验证，无完整双语及终端矩阵 | 部分 | 逐入口/空态/错误/窄屏/键盘和语言切换验收 |
| G02 | A/全员 | P1 | 全操作/AI调用日志与安全审计 |  | 已有审计与任务日志，不足全域贯通证明 | 部分 | 按资源/事件清单核对脱敏、操作者、版本及检索导出审计 |
| G03 | A/B/D | P2 | 历史导入、数据清洗、知识入库与回滚 |  | 原PRD交付项；未见完整客户演练 | 待完整交付 | 模板/映射/校验/失败补偿/回滚和客户数据责任 |
| G04 | A/D | P2 | 微服务/AI中台/多智能体与横向扩展 |  | 现有单API进程结构不等价最终企业架构 | 缺口 | 按部署架构验证任务认领、并发、隔离、扩容和恢复 |
| G05 | A/B/D | P2 | 私有化/离线/国产环境适配 |  | 组件部分部署，不等于全链路/国产交付 | 未完整验收 | OS/CPU/数据库/OCR/模型/字体资源与断网部署矩阵 |
| G06 | A/项目负责人 | P2 | 备份恢复、运维/业务分层培训、质保及7×24响应 |  | 原PRD交付服务必达；未见完整交付证据 | 待细化落实 | 确定责任/时限、演练、培训资料与服务交接，不自行删需求 |
| G07 | A/全员 | P1 | 数据生命周期、加密脱敏与不可信内容防护 |  | 统一需求 F02/F06/§7/§9/G1；现有授权/日志基础不足以覆盖完整链 | 部分基础实现，未获全域闭环证据；属于已批准安全要求，不是新增范围 | 验证原件/索引/缓存/下游/备份的更正删除、保存期限、加密与脱敏；恶意文件/提示注入不扩权、不触发副作用 |
| G08 | A/全员 | P2 | 完整交付包、安装升级与验证材料 |  | 统一需求 §9；已有启动/依赖/迁移脚本不代表完整客户交付 | 部分资料存在，客户交付与升级演练待完成 | 交源码、配置示例、依赖/离线包、初始化/演示数据、安装升级/回滚、演示步骤、验证结果和已知问题 |

## 指定技术逐项核对

本表中的依赖勾选独立于业务能力：组件应真正被使用，不以装包代替落实。

| 指定项 | 牵头 | 已实现 | 现状与剩余验收 |
| --- | --- | --- | --- |
| FastAPI / SQLAlchemy ORM / MySQL | A/全员 | × | 已有实际入口/模型/服务；跨域授权、迁移、禁止裸SQL边界仍逐项验收 |
| Milvus | B/D | × | 真实适配和语义搜索存在；RAG/删除撤权/质量仍需补齐 |
| PostgreSQL | D | × | ETL及副本访问已有代码/局部真实库证据，见 D03 |
| Redis | D/A | × | 客户端/中间件存在；API保护生效见 D02，缓存98%另验 |
| LangGraph | D | × | 真实图/检查点代码已使用；并发状态/恢复见 D04/D08 |
| LangChain | D |  | 当前仅依赖链不能证明指定能力已集成，交实际调用/职责/版本证据 |
| Dify | D |  | 部分部署不等于业务中台与编排完成，见 D09/D10 |
| Vanna.ai | D |  | 当前生成器与 Vanna 解耦，必须补原生接入，见 D14 |

## 原始质量/性能门槛（全部保留）

以下未获得完整达标报告，均留空；数字是冻结目标，不是当前结果。负责人须补测试定义/环境/数据集/公式/窗口/分位数及结果链接。

| ID | 目标 | 牵头 | 已实现/达标 | 通过证据 |
| --- | --- | --- | --- | --- |
| Q01 | 并发在线 ≥2000 | A/D |  | 定义在线/活跃/请求并发、业务混合、硬件及持续压测 |
| Q02 | 页面渲染 ≤1s | A/全员 |  | 终端网络、页面/计时边界、冷暖访问实测 |
| Q03 | 向量检索 ≤500ms | B/D |  | 数据量、Embedding/重排是否计入、延迟分布 |
| Q04 | 普通接口 ≤1s；大报表 ≤3–5s | A/D |  | 各类接口的数据量、同步响应/异步完成边界及 P95/P99 |
| Q05 | 系统可用率 ≥99.9%；AI ≥99.5% | A/D |  | 统计窗口、监控、依赖与故障/恢复证据，不用短时冒烟替代 |
| Q06 | 热点缓存命中 ≥98% | D |  | 明确缓存类型/分母/负载及撤权失效一致性 |
| Q07 | RAG 检索准确率 ≥95% | B/D |  | 授权样本/标注/公式/盲测，另验引用、拒答与零越权泄露 |
| Q08 | 匹配准确率 ≥90% | A/B |  | 定义正确性/排序口径、分层标注和完整简历证据 |
| Q09 | 简历解析准确率 ≥99% | B |  | 四格式与困难样本、字段定义、双人复核及独立报告 |
| Q10 | 私有化、国产化、安全与审计要求 | A/B/D |  | 环境适配、角色/行/字段负例、加密脱敏、备份恢复与责任确认 |

## 回传与关闭

回传格式：`条目ID / 提交 / 实现状态 / 接入状态 / 验证环境与真实命令 / 预期与实际 / 证据 / 未测 / 下个检查点`。部分能力必须说明缺哪一部分，不自行打 ×。Master 依据实际源码与结果决定标记；需求取代、阻塞解除和验收关闭均留链接。表中旧任务卡的“仅白名单/固定测评/画布非必需”等缩减口径按冻结需求失效处理，不能再按旧卡降低完成条件。

本表不是一次性的“完成率”汇总：P0 先看 A02/A06/B02/B07/B08/C08/D02/D07/D09/D10/D14，避免继续铺页面却保留关键偏差。D07 等近期持续变化的条目需按更新主干重新核对，不能用本快照阻止已经获得授权的进展。

---

## 2026-11-10 第二轮补登（基线 `7721a60`）

> **为什么补**：本表自 2026-11-09 首轮核验（快照 `141927c`）后未再更新，而 A/D 已在 11-10 上午至下午连续发布多个闭环到远端 master。本轮 MASTER 先完成云仓与本地全量同步（`7721a60`，见 [1732 收口记录](../../updates/MASTER/20261110-173200-云仓本地全量同步与分支清理收口.md)），再按 `git log --all` 与各角色 `docs/updates/<角色>/` 最新记录逐条回填。**只补登记、不改业务代码、不改写他人验证结论。**

**补登范围**：仅列 `141927c` 之后已进主干、但表内仍留空或状态过时的条目。其余条目状态与首轮一致，未复跑测试、不重复登记。

| 条目 | 补登内容 | 依据 |
| --- | --- | --- |
| **A02** | 由「空白·偏差待修复」改为「**部分**」：A02-1 子单元已交付（`5eb81c7`），`hard_fail` 恒 False、不符项/待核实进 `misses` 并随 Prompt C 交模型；但 **A02 整行未完成**，旧算法替代未消除、综合分 `overall_score` 仍被 D 的 `PROMPT_C_SCHEMA` 契约阻塞 | A 记录 `UPD-A-20261110-1100-A02-1取消硬门槛自动淘汰`；A→D 正式请求 `20261110-D-A02-2-PromptC综合分契约.md` 仍打开 |
| **A06** | 由「部分基础实现」改为「**×，整行收口**」：五个子单元全部交付并已推 master——A06-1 角色/职责门禁（`2eca164`）、A06-2 `users.department_id` + 部门行级过滤（`6ee1ebe`）、A06-3 字段级权限白名单 + 序列化裁剪（`e9e8015`）、A06-4 系统管理域 CRUD 路由整域接 `require_roles`（`78d946e`）、A06-5 `assessments`/`interviews` 内联 `ADMIN_ROLES` 行级判定统一迁移到 A06 角色门禁（`55a9010`） | A 记录 `UPD-A-20261110-1430-A06-5跨模块行级判定迁移`；C 记录 `20261110-144533-发布登记-635055e.md`、`20261110-150929-master检查-A06-5行级判定收口合入.md` |
| **A08** | 由「空白」改为「**×**」：`Application.input_snapshot_json` + `input_snapshot_at`、新迁移 `A_0011_app_snapshot`（挂 `A_0010` 成单 head）、`capture_application_input_snapshot` 五段纯读值拷贝 + `missing_sources`、列表按 `account_is_management` 分流 + 个人侧强制 `talent_profile_id = AccountScope.person_id` + 6 字段白名单 `_application_public_out` + 未确认终态结果置 None、`GET /applications/{id}/input-snapshot` 挂 `require_roles("system_admin","hr")`；定向 **exit 0 / 22 passed** | A 记录 `UPD-A-20261110-1644-A08申请输入快照与对外字段分级`；提交 `729fc60` |
| **D20** | 由「T-D21 未完成·待实现」改为「**×**」：`backend/app/modules/workflows/digital/` 新增三 ORM + 枚举白名单、三类 collector 全只读、`/api/workflows/digital` 10 端点；新迁移 `D_0012_digital_employee` 挂 `D_0011` 成单 head、83 表基线；`main.py` 注册 router（A 公共入口，跨模块）；`tests/D/test_digital_t21.py` **15 例**；新建 [数字员工契约](../../modules/D/数字员工契约.md) v1.0 | D 记录 `UPD-D-20261110-024500-T-D21数字员工纯站内版`；提交 `52b6d24`；**用户 2026-11-10 clarify 选定「方案一纯站内」** |

**补登未覆盖、仍需各角色回传的条目**（本轮只读核对，未获完整实现证据，故不打 ×）：A03 批次开始冻结档案版本（T-A42 只固定了模型/Prompt 版本与请求级幂等，**不等于** A03 的批次档案冻结语义，两者不可混同）、A11 审批链、A12 真实 AI JD 草稿、A14 员工花名册/任职记录、C08 防挂机与可信学习证据、C09 正式九宫格确认链、C10 发布时冻结题库、D02 限流环境开关（A 已交付 D02-A 但客户模式真实 Redis 串扰未验证）、D07 三类事件完整接线、D09/D10 Dify 业务对接与画布、D14 Vanna 原生问数、B02 OCR、B07/B08 制度与档案 RAG。

**遗留（与同步无关，非本次登记范围）**：A 的 `test_a06_department_scope.py` teardown 混删 id（空库首跑约 12 例假失败）；A→B/D 跨文件向量串扰请求仍打开；D 的 `PROMPT_C_SCHEMA` / `overall_score` 契约待补；主验收库 `ai_talent` 仍 stamp `D_0009_workflow_review` / 73 表，落后 head `A_0011`。

---

## 2026-11-10 第三轮补登（D11 / D12 / D19，基线 `a883222`）

> **为什么补**：用户问「D 角色有哪些可以继续」时逐项核对发现，D11 / D12 / D19 三行仍写「T-D19/20 未完成」「缺口」，但对应代码、路由注册与测试证据均已实核在主干 HEAD（`a883222`）上——属台账滞后而非实现缺口。本轮按用户「可以，继续」的指令补登三行，**只改台账、不改业务代码、不改写 D 的验证结论**。

| 条目 | 补登内容 | 实核依据 |
| --- | --- | --- |
| **D11** Skills 组件库与版本发布 | 由「T-D19 未完成·待实现」改为「**×**」：`backend/app/modules/workflows/skills_{router,service}.py`（组件受控生命周期：创建→审核→发布→引用）；`main.py:217-220` 注册 `/api/workflows/skills`；`tests/D/test_workflow_skills_t19.py` 10 例，定向 **10 passed**、`tests/D/` 全量 **177 passed / 12 skipped** | D 记录 `UPD-D-20261109-133022-T-D19-Skills组件库`；文件与路由注册在 HEAD 实核存在 |
| **D12** AI 中台 | 由「T-D20 未完成·待实现」改为「**×**」：`backend/app/modules/dashboard/{models,service,router}.py`（AiProvider / AiPromptVersion / AiCallLog 三 ORM；供应商 CRUD/启停/默认切换 CAS、Prompt 发布后不可变、record_call 审计旁路、usage_summary 看板）；`/api/ai-platform` 10 端点（`main.py:222-225`）；迁移 `D_0011_ai_platform` 挂 `D_0010`；`llm_client.py` 增 `provider_config_factory` + 成功/失败两路 `_record_call` 埋点；定向 + LLM 客户端回归 **46 passed** | D 记录 `UPD-D-20261109-150506-T-D20-AI中台`；文件、迁移与路由注册在 HEAD 实核存在 |
| **D19** 双端真实工作台与缺口告警 | 由「T-D22/23/24 未完整完成·缺口」改为「**×**」：**管理端** `dashboard/overview.py`（六类计数，复用 analytics `_GAP_SQL` 同口径）+ `GET /admin-overview` + `frontend/src/app/(main)/dashboard/` 六卡页面；**个人端** `dashboard/me.py`（me_overview 三要素 + `_require_personal` 门禁）+ `GET /me-overview` + `frontend/src/features/dashboard/` 卡片组件；`tests/D/test_dashboard_{t22,t23}.py` 共 20 例（12 + 8 passed），D 域全量 **209 passed / 12 skipped** | D 记录 `UPD-D-20261109-173107-T-D22管理员工作台`、`UPD-D-20261109-193831-T-D23个人工作台`；前后端文件在 HEAD 实核存在 |

**三行共同边界**：均为隔离测试（SQLite 夹具 / jsdom 组件测试）已达，真实 MySQL、真实浏览器与登录态验收未做；`AppShell.tsx` 菜单项与 `(person)/home` 挂载为 A 归属公共布局（D 跨归属透明登记，A 集成时可复核）；「已实现 ×」不等于业务验收完成，接入/验收边界见上表三行的「接入/验证边界」与「下一步/通过条件」列。

**补登后 D 线路未打 × 的条目**（维持原状，待推进或待回传）：D02（限流生效，剩客户模式真实 Redis 串扰复验）、D07（三类事件接线，待 B 生产方）、D08（定时触发/取消/重试完整运行测试）、D09/D10（Dify 业务对接与画布）、D13（指标六类，待 B/C 口径）、D14（Vanna 原生）、D16（图形进 DOCX/PDF）、D22（重排）、D25（智能解读 LLM 增强）。

---

## 2026-11-10 第四轮补登（A03 / A11，基线 `4b20144`）

> **为什么补**：第三轮补登后核对发现 A03 / A11 两行仍为空白，而 A 已于 18:40 / 18:42 前后交付并发布到 master（提交 `c926fbc` / `58e1240`，随 703a8bb 集成），A 本人记录已打 × 并列明边界。第二轮补登节当时写「未覆盖」是因为补登快照（`7721a60`）早于 A03/A11 发布；本轮按用户「可以，补上」指令补齐，**只改台账、不改业务代码、不改写 A 的验证结论**。

| 条目 | 补登内容 | 实核依据 |
| --- | --- | --- |
| **A03** 批次开始时冻结档案版本、匹配输入与历史快照 | 由空白改为「**×**」：`frozen_inputs_json`（迁移 `A_0012_profile_freeze` 单 head）+ `screen_service` 冻结函数组 + 两 worker 只读冻结输入 + 冻结结构 `A03-v1`（B 正式版本号 / `content_sha256` 等）+ DTO 摘要；定向 **20 passed**、既有 10 文件 **292 passed**、deselect 后全量 **909 passed / 0 failed**、前端 131 用例 | A 记录 `UPD-A-20261110-1742-A03批次档案冻结`；`job.py` 两处 `frozen_inputs_json`、迁移与测试文件在 HEAD 实核存在 |
| **A11** 岗位/人才需求审批及审批记录 | 由空白改为「**×**」：审批状态与业务 `status` 分离（5 列 + 只追加审计表 `demand_approval_records`，迁移 `A_0013` 单 head）+ 4 端点三套 `require_roles` 门禁 + 合法流转/409/四眼/版本失效 + `matchable` 判定；定向 **20 passed**、既有 5 文件 **205 passed**、空库全链 upgrade exit 0（88 表）、**原样全量 exit 0 / 932 passed / 0 failed**、前端 131 用例 | A 记录 `UPD-A-20261110-1840-A11需求审批链`；迁移、测试文件与 `router.py` 的 `submit`/`decision`/`withdraw` 端点在 HEAD 实核存在 |

**两行共同边界**：均为隔离库测试已达（真实 MySQL 迁移只在隔离库/临时空库演练，主验收库 `ai_talent` 未应用 `A_0012`/`A_0013`）；真实浏览器与人工验收未做；A03 的 A 记录中「原样全量 exit 1（唯一失败为已登记 B/D 向量串扰）」以 deselect 后 exit 0 作为发布门禁，**不把原样 exit 1 写成通过**；A11 记录中的 safe-delete 护栏 `SystemExit` 环境失败已由 A 如实定性为非代码回归。

**补登后 A 线路未打 × 的条目**：A02（A02-2 待 D 契约）、A05/A07/A09/A12/A14（待 A 回传）、A15（已打 ×，待与 A03 联调）。

---

## 2026-11-11 第五轮补登（D08 + P3 测试侧修复，基线 `57c7d07`）

> **为什么补**：按用户「同步云端仓库分支和主分支，然后继续」指令推进 D08 时，复跑发现 P1-P9 清单的 **P3 族仍在**——A06-1 统一门禁 `require_management_guard`（`backend/app/core/auth/scope.py:295`）内部固定开 `app.database.SessionLocal`（真实库）按 `user.id` 重查角色，D 域 SQLite 替身环境缺 `sys_user_roles` 绑定与用户行，`get_role_codes` 查询异常/查无此人 → 兜底 `("personal",)` → 整族 403（`UPD-MASTER-20261110-203044` 记录 72 例）。本轮按该清单的「D 测试改用真实库种子 / 契约替身」选项完成测试侧修复，D08 四项「下一步」核实在 T-D18 已全部实现，台账行补 ×。

**P3 修复（纯测试侧，零生产代码改动，11 文件 +123/-8）**：

| 修复手段 | 适用文件 |
| --- | --- |
| env/db 夹具 `monkeypatch.setattr("app.database.SessionLocal", factory)` 把门禁内部会话重定向到测试库 | `test_workflow_{lifecycle,models,nodes_t17,retry_cancel_t18,schedule_t18,skills_t19}.py`、`test_dashboard_{t22,t23}.py`、`test_digital_t21.py`、`test_ai_platform_t20.py`（10 文件；`test_reports_td11.py` 原有 `reports_service.SessionLocal` patch 已覆盖其自有门禁） |
| 建表清单补 `SysRole.__table__` / `SysUserRole.__table__`（空表使 `get_role_codes` 的 sys_user_roles 查询**成功返回空** → 回退 `users.role`，替身用户 role 字段生效） | 同上 11 文件 |
| 补种子 User 行（`db.get(User, uid)` 须命中才能回退角色） | `test_reports_td11.py`（id=2/4/5 hr/hr/personal）、`test_workflow_models.py`（id=9001 hr，对应 `_Actor(9001)`） |
| 订正 A06-3 统一拒绝文案的过时断言（`"403"/"管理侧"` → `DENY_MESSAGE`「无权访问该资源」，REQ-02 口径） | `test_dashboard_t22.py::test_personal_forbidden` |

**验证（真实退出码，本机 SQLite，无外部依赖）**：

| 项 | 结果 |
| --- | --- |
| 修复文件逐一单跑 | workflow 五件套 **51/51**；dashboard_t22 **12/12**；models+reports **20/20**；digital/skills/ai_platform/t23 全过 |
| **tests/D 全量**（`pytest tests/D/ -q --ignore=tests/D/test_etl_pgsql.py`，修复前 53 failed / 171 passed） | **exit 0，224 passed / 12 skipped / 0 failed**（295.09s）；12 skipped 为标记跳过的集成/条件用例，非失败 |
| `test_etl_pgsql.py`（真实 MySQL→PG 集成，本机 PG 未起） | 未跑，沿用户口径归集中验收 |
| 改动边界 | 仅 `tests/D/**` 与本表，未触碰 `backend/app/**` 生产代码、未改 `ownership.json`、未动他人文件 |

**修复后 P3 状态**：**测试侧收口**（tests/D 全绿）。P3 建议的另一选项「公共门禁提供可注入 db 的入口」留 A 评估（可选加固，非必需）；`reports/service.py` 自有门禁经既有 patch 覆盖，无需改动。

## 2026-11-11 MASTER 全量复核（97b952c）

本轮当前状态以上方主表为准，下方/前文各轮补登仅保留当时事实。主表更新OCR/档案问答/逐页证据、全文题库冻结及分阶段培养；撤回A06完整授权过宽标记，C06/C08/C09/C13/C14保留部分实现并说明剩余代码缺口。D16已有图形导出；D10仅DSL桥未等于交互画布；D22为Lon未集成分支成果。原76个业务项、10个质量项及全部原门槛不变。具体证据与验证边界见[本轮审核](20261111-项目进度审核.md)。本轮未执行业务验收、未发布修订。
