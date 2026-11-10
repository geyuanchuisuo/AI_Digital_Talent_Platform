# 开发计划 · Todolist · 板块 A

> 版本：v0.1.15　|　日期：2026-11-10 11:00　|　身份：A　|　分支：`feature/A/continuation`　|　`origin/master` = **`3cb0064`**（本轮开工时四 ref 对齐基线；补齐 v0.1.14 未升的头部版本号）
> 上游：`docs/需求文档.md` **V2.6（2026-11-07 已冻结）**、`docs/源码接入与需求决策.md`、`docs/四人分工与试行方案.md` §2、`docs/技术框架.md`、**`docs/modules/MASTER/原PRD统一执行跟踪表.md`（全员统一执行入口）**、`tasks/A.md`、`docs/modules/A/设计方案.md` v0.1.1、`.collaboration/ownership.json`。
> 用法：按 M1 → M6 顺序；每项满足「验收点」才勾选，勾选必须写实际命令/结果/证据落点；编号 `T-Axx` 供沟通引用。进度同步 `docs/modules/A/开发日志.md` Layer 3/4 与 `docs/updates/A/` 交接记录。
> ⚠️ 本表是台账，**不是完成声明**；`[x]` 只表示「有实证且已登记」。Master 统一执行表口径：`×` 仅代表代码已实现，不等于接入/业务验收；本表 `[x]` 同理不代表浏览器/客户验收完成，人工验收单列在文末「未收口清单」。
> 🔴 **T-A06 已重排（v0.1.13）**：需求基线以 **V2.6 冻结版** 为准；11-07～11-10 落地成果回填至「M3-V2.6」与「状态快照」两节；**活跃状态以 `docs/updates/A/` 最新记录 + 根 `更新记录.md` 尾部 + `git log` 为准**，不再按本表头部旧版本号判断进度。历史 v0.1.x 变更记录 append-only 保留，不回改。

---

## 批次总览

| 批次 | 内容 | 任务数 | 前置依赖 | 状态 |
|---|---|---|---|---|
| M0 | 分支同步与接力底座 | 6 | 无 | ✅ 完成（已提交 `6f49dc3` + `834ebfc` + `3636165`，纯文档） |
| M1 | 第 1 天环境与能力探针 | 8 | M0 | 🟡 自动探针完成（T-A01/04/05/07 已勾；T-A06 本轮重排完成；T-A02/03 人工项、T-A08 `next build` 环境受阻记为未验证） |
| M2 | 身份、person_id 绑定、双端授权 | 5 | M1、B 映射契约 | 🟡 阶段 1 完成（T-A11/12/13/14）；T-A15 双端隔离回归待人工反例 |
| M3 | 人才需求、AI JD、招聘状态与邀约 | 4 | M1、Agnes 可用 | 🟡 旧 V2.2 口径已由「M3-V2.6」取代；T-A60a~f 代码与自动化断言已落地，**业务/浏览器验收未完成** |
| M4 | AI 综合评分替换来源评分 | 4 | M1、B 档案快照、D 模型契约 | 🟡 **A02-1 已交付**（取消硬门槛自动淘汰、不符项/待核实仍可见、全员进 Prompt C，768 用例全绿）；**A02-2 待 D 契约**（模型综合分替换 0.7/0.3 与固定权重公式）；T-A31/T-A34 仍未收口 |
| M5 | 分数线、快照、逐结果失败、人工结论分离 | 4 | M4、迁移链验证 | 🟡 T-A41/43 完成；T-A42 批次幂等未收口；T-A44「目录当前不存在」表述已过期，迁移链实跑证据见状态快照 |
| M6 | 公共集成与回归 | 3 | M2–M5 | 🟡 T-A53 完成；T-A51/52、C-08 幽灵规则清理、C-09 OpenAPI `response_model` 补齐待收口 |
| M7 | 交付环境封装（用户独立任务） | 3 | 不进入 M1–M6 主链 | ✅ 完成（T-A70/71/72；`next build` 客户侧干净环境复跑仍未验证） |

---

## M0 · 分支同步与接力底座 ✅

- [x] **T-A00a 同步远端 master** ✅ 2026-11-06 完成（`git fetch` 全分支 + 本地 `master` 快进 `07677bc → df6dae1`；22:28 二次同步 `df6dae1 → 60f7967`，见 T-A00f；未切到 master、未产生提交；见[开发日志](开发日志.md) Layer 3 §A-00 / §A-02）
- [x] **T-A00b 建立并登记 A 工作分支** ✅ 完成（`feature/A/continuation` @ `df6dae1`；`setup_collaboration.py --actor A --branch ...` 写入 `collab.actor` / `collab.branch` / `core.hooksPath=.githooks`；`check_scope.py --staged` 与 `--base master` 均 `Scope OK, changed_paths=0`）
- [x] **T-A00c 建立分层日志 + 勾选式 todolist + 设计方案** ✅ 完成（沿用旧 `main/` 体例：Layer 0–6 日志、本 todolist、[设计方案 v0.1](设计方案.md)；落在 `docs/modules/A/**` 授权目录，根级同名文件未创建）
- [x] **T-A00d 首条交接记录 + 跨包请求 + 集成索引** ✅ 完成（`docs/updates/A/20261106-210900-...md`、`docs/requests/A/` 3 份、根 `更新记录.md` 追加一条）
- [x] **T-A00e 全仓只读静态架构核查** ✅ 2026-11-06 21:26 完成（360 文件 / 60,758 行；132 py AST 0 错、内部导入 0 断裂、归属表覆盖偏差 31/278；勘误评分链路措辞；结论见[开发日志](开发日志.md) Layer 3 §A-01、H-A11/H-A12）。**仅静态，运行态未验证，不能替代 M1**
- [x] **T-A00f 二次同步远端 master 与基线变基** ✅ 2026-11-06 22:28 完成（`git fetch --all --prune` → `git branch -f master origin/master` → `git rebase origin/master`；基线 `df6dae1 → 60f7967`，3 个本地提交重放零冲突，新号 `6f49dc3/834ebfc/3636165`；`--base master` 8 / `--staged` 0 / behind 0 ahead 3；见[开发日志](开发日志.md) Layer 3 §A-02、[交接记录](../../updates/A/20261106-222800-同步远端master与基线变基.md)）。**未 push、未合并 master**

## M7 · 交付环境封装（用户 22:27 独立任务，不进入 M1–M6 主链）

- [x] **T-A70 一键自动创建运行环境** ✅ 2026-11-06 22:45 实现（`scripts/A/bootstrap.py` + `.cmd`/`.sh`、根 `.env.example`、`backend/.env.example` 真实默认值、`deploy/milvus/docker-compose.yml` +mysql、README 一键章节、[设计说明](交付环境封装.md)）。验证：`py_compile` / `--help`、临时目录配置生成 **11/11**、`docker compose config --quiet`（4 服务 + mysql 卷）、`check_scope --staged`。**端到端证据已由 T-A71 补齐**（见下条）；演练在隔离副本/独立端口完成，未触碰现有 3306/ai_talent。详见 [开发日志](开发日志.md) Layer 3 §A-03 / §A-04、[交接记录](../../updates/A/20261106-224500-交付环境封装.md)。
- [x] **T-A71 隔离环境端到端演练** ✅ 2026-11-06 23:35 完成并通过：干净 clone 副本 `D:/HHHYYY/_aitalent_e2e_20261106` + 独立 MySQL 3307/`ai_talent_e2e`、Milvus 19531/`talent_vectors_e2e`、容器前缀 `aitalent_e2e`、后端 8100/前端 3100。真实跑通配置生成→4 容器→venv(Python 3.13.14)→`npm ci`(327 包)→13 表 + seed→`check_env` 全绿→`--start` 前后端就绪；`/api/health` 200(db/milvus/worker 全 true)、`/login`/`/screen`/`/talents` 200 无 `__next_error__`、登录 200 返回 JWT、4 个受保护接口 200、CORS 200；模拟 Ctrl+C 后 8100/3100 均释放；演练后 `3306/ai_talent` 13 表/users=1/job_profiles=1 **与演练前一致**。过程中发现并修复 2 个真缺陷（`npm exec -p` 被吞、Windows 孤儿进程）。**未验证**：`.cmd`/`.sh` 包装入口单独实跑、业务 pytest/vitest/浏览器点击流/真实模型调用。证据：[2335 交接记录](../../updates/A/20261106-2335-T-A71端到端演练通过.md)、[开发日志](开发日志.md) Layer 3 §A-04、[交付环境封装](交付环境封装.md) §4。
- [x] **T-A72 按授权发布** ✅ 2026-11-07 00:05 完成（先同步再推）：网络授权放行后 `git fetch --all --prune`；`git push -u origin feature/A/continuation` 建远端分支；首次 `push master` 因他人并发提交 `0a24747` 被 `fetch first` 正确拒绝 → 重新 `fetch` + `git rebase origin/master`（9/9 零冲突、`README.md` 双方内容共存）→ `--base master` `Scope OK, changed_paths=23` → `push --force-with-lease` 更新个人分支 → 末次 `fetch` + 祖先检查 → `push origin master:master`（`0a24747..8ab2f30`）。复检：四个 ref 同为 `8ab2f30`、工作区干净、远端 master 树 7/7 交付文件齐备、README「一键交付」章节 1 处。**未验证**：发布版未重跑 T-A71 演练（rebase 只改提交号、树内容未变）、`.cmd`/`.sh` 单独实跑与业务 pytest/vitest/浏览器/真实模型调用。证据：[0005 交接记录](../../updates/A/20261107-0005-T-A72发布完成.md)、[开发日志](开发日志.md) Layer 3 §A-05；受阻历史见 [2345 记录](../../updates/A/20261106-2345-推送受阻待授权.md)（不回改）。
## M1 · 第 1 天环境与能力探针（自动探针已完成；人工/浏览器项按用户约定排最后）

- [x] **T-A01 依赖、建库与启动探针** ✅ 2026-11-07 09:49 完成：主仓库隔离环境（3308/`aitalent_main`/19532/8101，不碰 3306/`ai_talent`、19530 旧实例、3307 e2e）由 `bootstrap.py` 一键建成——venv(Python 3.13.5) + npm ci 327 包 + `init_db` 幂等 13 表 + **完整模式自检 7/7 ✅（含真实 LLM `spark-x2.6` 9.3s finish_reason=stop、真实 Embedding `text-embedding-v3` 1024 维）**；`uvicorn --workers 1`（8101）启动后 `/api/health` 200、`status=ok`、db/milvus/worker 全 true（heartbeat 09:49、concurrency 2、backlog 全 0），验证后服务已停、8101 已释放。全量 pytest **0 失败**、vitest **66/66**（见 T-A01 下方与 [092927 交接记录](../../updates/A/20261107-092927-架构问题清单逐条裁决与落地.md)）。00:46 副本探针的历史：4 项失败已分别定位（C-01 断言已修、3 项向量 401 已由真实 Key 消除）；当时 vitest 2 失败本次未复现（测试环境/时序类，印证 0100 定位）
- [ ] **T-A02 现有招聘链路浏览器复现** — `hr/hr123456` 登录 → 建岗位 → 传 1 份 `docs/imports/hhy_gycs_master/eval/resumes/*.docx` → 解析 → 筛选 → 匹配 → 人工改判。验收点：**记录实际浏览器是否验证**、接口返回、耗时；不得用源码存在替代实测。
- [ ] **T-A03 权限与失败边界反例** — 无 token / 停用账号 / 重复提交 / 单份失败 / 重启后任务恢复。验收点：401/403、幂等不重复建结果、runner 恢复不永久卡 running。⏸ **00:46 只完成无 token / 伪造 token 两例**（5 个受保护接口 401 / 带 JWT 200）；停用账号、重复提交、单份失败、重启恢复需造业务数据，留待人工阶段，保持 `[ ]`
- [x] **T-A04 契约差距核对** ✅ 2026-11-07 01:00 完成：实测 OpenAPI 30 路由、4 个列表接口结构、`auth/me` 字段、登录 `data.token`，逐字段比对 [源码接入基线](源码接入基线.md)，差异 K-01～K-06 写入 [接口契约核对.md](接口契约核对.md) 并登记 Layer 4。新发现 C-09（37 个 2xx 仅 1 个 schema）。**未造业务数据**，`screen/match` 批次与结果字段未做运行态验证
- [x] **T-A05 外部能力核对（与 D 协同）** ✅ 2026-11-07 09:49 按裁决 U-04 完成当前实配部分：用户选定**复用旧 main Key**（`AI数字化人才平台-相关文档/main/backend/.env` → 本机根 `.env`，`git check-ignore` 确认不进 Git）。实测：LLM `spark-x2.6` 真实调用 9.3s finish_reason=stop 非空；Embedding `text-embedding-v3` 1024 维真实调用成功；Milvus 2.6.24（19532 独立实例）连通、pymilvus 3.0.2 兼容；**之前 3 个向量集成测试（HTTP 401）随真实 Key 全部转绿**。⚠️ **Agnes `agnes-3.0-flash` 仍未验证**（当前实配为历史 Spark+百炼；Agnes 是待 D 验证的切换目标，未验证即写未验证，不假定 Agnes 提供 Embedding/OCR）——留 D 验证后按 README「覆盖三行」口径切换
- [x] **T-A06 按探针结果重排工期** ✅ 2026-11-10 完成：口径由 V2.2 改回 **V2.6 冻结版**；回填 11-07～11-10 成果（招聘域五表 / sys 六表 / 个人端申请视图 / 75 表断言 / 迁移链单 head `C_0007` / `C_0001` 空库迁移 P0 修复 / 403 语义分级 / 限流豁免 / 前端 104 用例）；新增「状态快照」与 Master 统一执行表 A 条目映射。**不把代码落地写成业务验收，不写 3–4 天承诺**。证据：[1000 记录](../../updates/A/20261110-1000-C_0001空库迁移修复与结构基线同步.md) 与根 `更新记录.md` 同 ID 条目。
- [x] **T-A07 跨模块架构冲突检测** ✅ 2026-11-07 00:46 完成：归属表全量覆盖（主干源码 0 无归属、C 分支 53 文件全归 C）、`git merge-tree` 对 3 个他人分支无文件级冲突、13 表断言 vs C 的 21 模型、C 模块路由/菜单未注册、配置契约与测试隔离等冲突全部定级编号。结论与待拍板项见 [待确认清单](架构待确认问题清单.md)、[0050 记录](../../updates/A/20261107-0050-M1探针与架构冲突检测.md)。**未合并任何分支、未 push**
- [ ] **T-A08 前端生产构建探针** ⛔ **环境受阻（C-10）**：`next build` 5 次均失败（3 次 `EPERM: distDir/trace`、1 次 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 删除数达 50 阈值、1 次全新 `distDir` + 预建 trace 仍 EPERM）；手动等效写入成功排除路径权限，Next 14.2.35 `to-json.js:77` 硬编码 trace reporter 无开关。判为运行环境拦截、非代码错误；**记为未验证**，客户侧干净环境复跑。`next dev`（T-A71）已实测可用。证据 [0100 订正](../../updates/A/20261107-0100-架构补充核查与订正.md) §3.4

## M2 · 身份、person_id 绑定与双端授权（F09 / 需求 §3、§12.1）

- [x] **T-A11 User 与账号端类型** ✅ 2026-11-07 分阶段完成：person_id 列（迁移 A_0002 幂等 ALTER+往返验证）+ seed personal 演示账号（绑定 person_id=398 真实库验证）；迁移可重放/旧账号可登录实测。见 [1145 记录](../../updates/A/20261107-1145-person_id列落地.md)、[1230 记录](../../updates/A/20261107-1230-数据范围授权落地.md)。
- [x] **T-A12 JWT / UserOut / AuthUser 贯通 person_id** ✅ 2026-11-07：UserOut 增量兼容字段（登录 + /auth/me 两处生效）、前端 AuthUser 类型同步、断言更新；auth 34 用例全过。见 [1250 记录](../../updates/A/20261107-1250-auth贯通person_id.md)。
- [x] **T-A13 后端本人数据授权（阶段 1）** ✅ 2026-11-07：`core/auth/scope.py`（AccountScope/authorize_profile_access/authorize_file_access，deny-by-default）+ deps `AccountScopeDep`；9 用例全过。⚠️ `require_self` 挂载到 B/C/D 具体接口由其联调（负例归 B REQ-02 验收）；六类角色矩阵待 V2.5 冻结。见 [1230 记录](../../updates/A/20261107-1230-数据范围授权落地.md)。
- [x] **T-A14 双端入口与端分发（骨架）** ✅ 2026-11-07：R-A01 已解除（U-03 用户授权）；(person) 路由组 + PersonalShell + /home 首页 + 登录双端分流（personal→/home）；4 新测试过。见 [1300 记录](../../updates/A/20261107-1300-双端入口骨架.md)。⚠️ 个人侧业务页面等 B/C 组件。
- [ ] **T-A15 双端隔离回归** — 个人端不可见管理端功能、不可读他人数据；管理员可关联账号与档案。验收点：越权反例 + 重启持久化。

## M3 · 人才需求、AI JD、招聘状态与邀约（F01）

- [ ] **T-A21 人才需求实体与页面** — `backend/app/modules/recruitment/**`、`frontend/src/app/(main)/job-profiles/**`、`frontend/src/features/recruitment/**`。字段：岗位、`headcount`、说明、状态；验收点：创建后可重新打开修改。
- [ ] **T-A22 AI 可编辑 JD 草稿** — 真实模型生成、保存岗位版本 + 模型/Prompt 版本 + 人工编辑值；验收点：**模型失败明确提示，不返回样例**；保存后可查。⚠️ 依赖 R-A02。
- [ ] **T-A23 招聘状态与邀约记录** — 候选人 person_id、岗位版本、状态、邀约时间/方式/状态/说明、面试备注；只存记录，不发邮件/短信。验收点：同一候选人的筛选→邀约→面试引用可连续查询。
- [ ] **T-A24 需求人数口径展示** — 页面显示需求人数、人工标记进入下一步人数、剩余需求并写明口径（人工推进 ≠ 已录用，§12.3）。验收点：与数据库人工核算一致；重复提交不重复创建。

## M4 · AI 综合评分替换来源评分（F03 / C05）

- [ ] **T-A31 评分服务改造** — `service/match_engine.py`、`core/prompts/match.txt`、`backend/app/modules/matching/**`：模型分析岗位 + 完整档案 → 0–100 分 + 依据 + 不足 + 待核实；替代 `hard_gate` 与 `0.7/0.3` 加权公式，**保留来源实现作兼容对照记录**。验收点：至少两份档案对同一岗位产生有差异、有依据的结果。
- [x] **T-A32 Worker 输入补全（阶段 1）** ✅ 2026-11-07：H-A07 修复——profile_input 5→完整画像+结构化经历证据（内联查最新 parsed_resumes.structured_json，未解析标 missing_fields）；真实模型冒烟 84.4 vs 77.25 有差异有依据。⚠️ B 快照服务落地后切换输入源（B 契约 §5）。见 [1330 记录](../../updates/A/20261107-1330-评分输入补全.md)。
- [x] **T-A33 结果快照（表结构+写入）** ✅ 2026-11-07：screen_results+4/match_results+7 列（model_version/prompt_version/attempt/profile_input_snapshot_json + match 人工结论三列），worker 写入真实数据；迁移 A_0003+merge A_0004。⚠️ 「模型原始返回」全量存档与失败单独状态（failure_reason 触发语义）留 M5 逐结果重试专项。见 [1400 记录](../../updates/A/20261107-1400-结果快照字段.md)。
- [ ] **T-A34 评分边界** — 等于分数线归通过、技能词边界（Java≠JavaScript、C≠C++）、缺失证据标待核实、年龄性别不参与、语义相似度不等于业务分、不把模型自报准确率当实测。验收点：逐条反例测试。

## M5 · 分数线、快照、失败记录与人工结论分离（F03 / §12.1）

- [x] **T-A41 人工复核三态** ✅ screen 侧 F1-10 已有（源码）；2026-11-07 补 match 侧对齐（PATCH /api/match/results/{id} 三态+撤销，详情可见操作人，6 用例全反例）。备注字段未做（需求未明确）。见 [1430 记录](../../updates/A/20261107-1430-人工改判接口.md)。
- [ ] **T-A42 批次快照与幂等** — 固定岗位版本、分数线、人员名单、档案输入快照、模型/Prompt 版本；retry 只补失败项、不重圈人；岗位/档案变更新建一轮。验收点：重复请求不重复建结果，历史人工意见不被覆盖。
- [x] **T-A43 字段层** ✅ 2026-11-07：两结果表快照/版本列 + match 人工结论三列全部落地（迁移 A_0003）；match 人工改判接口 done（[1430 记录](../../updates/A/20261107-1430-人工改判接口.md)）。⚠️ 「单份失败单独重试」的逐结果重试语义待定义（failure_reason 触发条件），列 M5 专项。
- [ ] **T-A44 迁移链实跑** — `backend/migrations/versions/A_*.py`（**目录当前不存在，需先建骨架**）+ `models/__init__.py` 13 表断言 + `init_db.py`、`backup_db.py` 清单同步；**`create_all` 不 ALTER**，必须验证「已有库升级」与空库两条路径。验收点：升级后旧历史批次仍可追溯。

## M6 · 公共集成与回归

- [ ] **T-A51 入口与依赖集成** — `backend/app/main.py` 注册新路由；`requirements.txt`、`frontend/package.json`、`.env.example` 由 A 统一集成（加包先走 `docs/requests/A/`，不替 B/C/D 改）。验收点：`--workers 1` 下全路由可达，健康检查正常。
- [ ] **T-A52 全链路回归** — 登录双端、越权、阈值等于、评分失败、重复请求、批次快照、重启持久化、浏览器实测；与 C 联调面试引用。验收点：每项记录输入/预期/实际/证据位置。
- [x] **T-A53 指标口径交付** ✅ 2026-11-07：[指标口径交付](../../modules/A/指标口径交付.md)（三域字段表+人工推进链+缺口清单+消费方式建议）；缺口指标待 M3/V2.5 冻结。见 [1440 记录](../../updates/A/20261107-1440-指标口径交付.md)。

---

## 阻塞登记（接力 Agent 先看这张表）

| 编号 | 阻塞 | 影响任务 | 当前处理 |
|---|---|---|---|
| R-A01 | ~~`(person)` 未登记归属~~ | T-A14 | ✅ **已解除并落地**（U-03 授权 + (person) 壳上线） |
| R-A02 | Agnes 协议/结构化输出、独立 Embedding、Milvus 未验证 | T-A05、T-A22、T-A31 | 🔄 **进行中**（U-04：用户裁决复用旧 main Key，已写入本机 `.env` 不进 Git；本轮隔离环境真实验证） |
| R-A03 | ~~契约未回~~ → **契约已回**（B 002845：person_id=talent_profiles.id；阶段 1 契约可用、快照服务未实现） | T-A11、T-A32 | A 已按契约完成阶段 1（T-A11/T-A32）；服务落地后 A 切换 worker 输入源 |
| R-A04 | 13 表冻结、迁移链未实跑 | T-A11、T-A44 | C-04 裁决覆盖：一期 34 表口径确认，M6 统一注册；迁移链 `A_*.py` + `C_*.py` 双前缀（U-05） |
| R-A05 | 本机 `python` 无 pytest；`backend/.env`、`.venv`、`frontend/node_modules` 均缺失 | T-A01 及全部测试 | 🔄 **进行中**：本轮 bootstrap 隔离环境（3308/`aitalent_main`/19532/8101/3101，不碰 3306/`ai_talent`、19530 旧实例、3307 e2e）解决；⚠️ 8000/3306/19530 属旧 main 实例与既有基础设施（H-A11），不能当验收证据 |

## 跨包请求状态

| 请求 | 目标 | 状态 | 等待什么 |
|---|---|---|---|
| [B 档案版本与证据契约](../../requests/A/20261106-B-档案版本与证据契约.md) | B | 待响应（**U-05 已催办**，2026-11-07） | person_id 映射、不可变输入快照、证据读取接口 |
| [D 模型任务与评分版本契约](../../requests/A/20261106-D-模型任务与评分版本契约.md) | D | 待响应（**U-05 已催办**，2026-11-07） | Agnes 验证结论、模型/Prompt 版本标识、任务状态约定 |
| [C 人工面试引用契约](../../requests/A/20261106-C-人工面试引用契约.md) | C | 已回（C 分支 58baaf7「明确人工面试引用契约并支持创建幂等键」，M6 联调核对） | — |
| [D→A 工作流/RBAC/站内通知契约](../../requests/D/REQ-A-20261106-workflow-rbac-notification-contract.md) | D → **A**（本方待响应） | A 待响应（站内事件清单 **U-02 已确认：以 D 请求清单为准**，含招聘阶段变化与面试安排、课程指派与报名审批、候补递补、必修提醒/逾期、Agent 审批待办与运行失败；面试邀约/改期留二期） | A 按已确认清单响应契约，不删既有事件、不扩大集合 |

> 维护人在自己的 `docs/updates/<角色>/` 新增响应并链接回请求；请求方不得替对方改代码，也不得把「待响应」写成「已实现」。

## 变更记录

- v0.1（2026-11-06）：建立 A 的勾选式 todolist；M0 四项完成，M1–M6 待办；登记 5 项阻塞与 3 份请求。未实施业务代码。
- v0.1.1（2026-11-06 21:26）：M0 补 T-A00e 静态架构核查（已完成、仅静态）；T-A44 标注 migrations 目录待建；R-A05 补环境缺口与端口错觉。M1 仍是唯一下一步。
- v0.1.2（2026-11-06 21:34）：M0 全部内容已随 `cd76b80` 本地提交（**未 push、未合并 master**）；M0 任务数 4→5；提交后 `--base master` = `changed_paths=8`。M1 仍是唯一下一步。
- v0.1.3（2026-11-06 21:35）：补记状态订正提交 `c139566`；实时 HEAD 以 `git log -1` 为准。M1 仍是唯一下一步。
- v0.1.4（2026-11-06 22:28）：二次同步远端 master `60f7967` + rebase，M0 任务数 5→6、补 T-A00f、登记 D→A 新契约请求；**下一步改为用户要求的「交付环境封装」，随后才是 M1 探针**。
- v0.1.5（2026-11-06 22:45）：新增独立批次 **M7**（T-A70 已实现、T-A71 隔离环境端到端演练待做）；交付封装未端到端验证，接力 Agent 第一优先级是 T-A71 补证据，其次才是 M1。
- v0.1.6（2026-11-06 23:35）：**T-A71 已通过**（隔离副本真实实跑，含 2 个缺陷修复）；新增 T-A72 按授权发布。此后第一优先级是 T-A72，其次才是 M1 探针。
- v0.1.7（2026-11-06 23:45）：T-A72 因远端 403 / 网络权限被拒而**阻塞**，未 push、未合并 master；第一优先级改为「等待用户放行网络或修复凭据」，恢复步骤已登记。
- v0.1.8（2026-11-07 00:05）：**T-A72 已完成**——同步远端 → rebase 到他人并发提交 `0a24747` → push 个人分支 → 快进合入并 push `master`（`8ab2f30`）；远端 master 现含一键交付封装，客户默认 clone 即拿到。发布侧阻塞解除；**第一优先级回到 M1 探针**（T-A01 起），D→A 工作流/RBAC/通知契约仍待响应且站内事件清单待用户确认。

- v0.1.9（2026-11-07 00:46）：用户「先把架构都跑出来，人工测试放最后，问题放一起睡醒一起回」。M1 自动探针与架构冲突检测已实跑（588 后端用例 / 14 前端测试文件 / 健康与鉴权反例 / 归属与合并预演），发现 8 类技术冲突 + 8 组需求裁决，全部汇总到 [架构待确认问题清单](架构待确认问题清单.md)。T-A01 因 4 项稳定失败未勾选；新增并勾选 T-A07。**第一优先级改为「等用户一次性回复」**，人工浏览器测试排最后，业务开发未启动、未 push。
- v0.1.10（2026-11-07 01:00）：用户「请继续完成未完成的任务」。同步远端发现 `master` 已被 C 推进到 `2ca3980` 并 rebase 零冲突；补跑 T-A04 契约核对（勾选）、前端 2 例失败定位、后端 4 项失败精确断言、`next build` 诊断；订正 C-04/C-05/C-08，新增 C-09～C-11 / U-09 与受阻的 T-A08，全部汇总到 [清单 §5](架构待确认问题清单.md)。**第一优先级仍是「等用户一次性回复」**，人工浏览器测试排最后，业务开发未启动、未 push。
- v0.1.11（2026-11-07 09:29）：用户「问题一个一个问我」逐条裁决 C-01～C-11 / U-01～U-09（结论登记 [清单 §6](架构待确认问题清单.md)），并追加「推送前记得测试，测试通过才能推送合并」。落地：C-01 断言修复、C-07 默认前缀 `aitalent-app`、C-02/C-03 README 澄清、U-03 ownership 变更（单独提交）、U-04 旧 Key 本机 `.env`。R-A01 解除。**M2 暂缓（U-07）**。同轮测试完成（09:52）：隔离环境 bootstrap 自检 7/7（真实 LLM/Embedding 调用成功）、health 200 worker alive、全量 pytest **0 失败**、vitest **66/66**；T-A01/T-A05 勾选；新坑 H-A26（`.env` 隔离名覆盖 `test_config` 默认值断言，改回默认）。**U-09 前提「测试通过」已满足**，进入授权推送流程；人工浏览器测试关键节点再叫（U-08）。
- v0.1.12（2026-11-07 14:50）：**需求冻结（V2.6）**——用户指令按 design 四件与 PRD 核对需求文档，冲突 4 处按原需求改回（系统管理域 RBAC 表体系、JD 生成投放、岗前培养计划、候选人管理实体），状态改为已冻结；AGENTS/README 基线对齐。M3+ 重排解锁：F01 增 JD 投放、F05 增岗前培养、系统管理域表体系（sys_*）纳入——A 下一批任务按 V2.6 重排（人才需求实体含 headcount、JD 投放接口、招聘状态机）。同轮功能 6/7 已集成（T-A41/T-A53）。
- v0.1.13（2026-11-10 10:00）：**T-A06 重排落地**。同步 `origin/master 824809e → bca345e`（52 提交），`tasks/A.md` 新增 Master 统一执行表入口；头部口径改回 V2.6，批次总览与 M3-V2.6 的 T-A60a~f 按实证勾选，新增「状态快照」与「未收口清单」。同轮受理 `REQ-C-20261109-121506` 修复 `C_0001` 空库外键 P0、同步三列与物理外键白名单断言；隔离库空库升级到 `C_0007` 单 head 通过、后端 759 / 前端 104 用例全绿。**未 push、未合 master、未做人工验收**。证据：[1000 记录](../../updates/A/20261110-1000-C_0001空库迁移修复与结构基线同步.md)。
- v0.1.14（2026-11-10 10:50）：用户明确「继续」的新口径（拉云仓对齐→跑 A→每单元测试→拉云仓→推送→合并主 master）。本轮拉云仓 `bca345e → 2c494da`（7 提交），rebase 解决与 B 的结构断言冲突（采用 B 严格集合断言 + 保留 A 裁决依据）；新基线 76 表、单 head `B_0007`、空库 31 步、后端 761 / 前端 113 用例全绿。**跑通单元 D02-A「限流环境开关」**：`config.py` 加开关、`main.py` 抽出 `rate_limit_exempt_paths()`、两份 `.env.example` 补部署说明、5 个新用例（9 passed）覆盖 429/403/降级，默认行为零变化。客户模式真实 Redis 端到端串扰复验仍记为未验证。证据：[1050 记录](../../updates/A/20261110-1050-D02限流环境开关与云仓对齐.md)。
- v0.1.15（2026-11-10 11:00）：用户「再拉下来检查一下，没问题继续跑」。开工 `fetch` 后四 ref 全部 `3cb0064`、无新更新、祖先复核 exit 0；跑通 **A02-1「取消硬门槛自动淘汰」**（V2.6 §5 / Master 执行表 A02 P0 子单元）：引擎删除硬淘汰提前返回、规则层结果改「不符项 / 待核实」提示并随 Prompt C 输入、两处 `estimated_llm_calls` 不再折减、`match.txt` 改全员评分口径；按新口径重写三份测试而非删弱。定向 **33 passed**、后端全量 **768 用例 / 767 passed / 1 skipped / 0 failed**、静态门禁全绿。**A02-2 未完成**：模型综合分需 D 先改 `PROMPT_C_SCHEMA`，已发 [请求](../../requests/A/20261110-D-A02-2-PromptC综合分契约.md)，A 不越界代改。台账回填 M4 / 状态快照 / 执行表 A02 映射 / 未收口清单。**发布前再 fetch 发现 `origin/master 3cb0064 → 3eed209`（23 提交，D 的 T-D18~D24 与迁移链单头收敛）**，双方零重叠、rebase 零冲突，本地 A02-1 提交 `9cfb607 → 5eb81c7`；新基线单 head `D_0012_digital_employee`、84 表 / 17 路由，已重建隔离库复验。证据：[1100 记录](../../updates/A/20261110-1100-A02-1取消硬门槛自动淘汰.md) + [1130 并发更新补记](../../updates/A/20261110-1130-并发更新合并与复验补记.md)（append-only，基线数字以补记为准）。

## M3-V2.6 · 招聘域重排（需求冻结后按 V2.6 口径，2026-11-07）

> 原 M3（T-A21～T-A24）基于 V2.2 编写；冻结版 V2.6 增补 JD 生成投放（F01）、岗前培养（F05，归 C）、系统管理域表体系（F09）。以下为 A 侧重排清单，编号 T-A60x 供沟通引用。

> 口径：以下 `[x]` 表示**代码 + 自动化断言有实证且已登记**（后端 759 用例全量、前端 104 用例全量均已通过），**不等于浏览器 / 客户业务验收**；Master 统一执行表的 `×` 同为代码标记。

- [x] **T-A60a 招聘域数据层** ✅ `modules/recruitment/models.py` 五表（`project_groups` / `projects` / `project_demands` 含 `headcount` / `jd_publications` / `applications` 含五阶段映射）+ 迁移 `A_0005_m3_recruitment`；注册后一期 **75 表断言**（`backend/app/models/__init__.py:193`，70 + 5 B history）与去重 75 均通过。证据：`backend/tests/test_recruitment_m3.py::test_five_stage_mapping_matches_confirmed_baseline`、`test_models_structure.py`。⚠️ 完整申请输入快照见执行表 A08，尚未落地。
- [x] **T-A60b 人才需求与项目 CRUD** ✅ `recruitment/{router,service}.py`，创建 / 列表 / 详情 / 状态流转 / 重复提交 409 有集成用例。证据：`backend/tests/test_recruitment_api.py`（`headcount=3` 回读、同岗位同项目重复需求 409、未带 token 401、`status=in_progress` + `headcount=5` 更新）。⚠️ 需求人数与「人工推进 ≠ 已录用」缺口口径的**页面核对**未做。
- [x] **T-A60c 申请与五阶段状态机** ✅ applications 创建（同岗在途 409）+ 内部阶段流转自动映射对外阶段 + 终态 `result_category` + 非法阶段拒绝。证据：`test_recruitment_api.py::test_application_lifecycle_five_stage_mapping`（`new→submitted`、`screening→screening`、`invited→interviewing`、`deciding→deciding`、`offer_accepted→closed/hired`）。⚠️ 越权 403 的完整角色矩阵与浏览器刷新保留归执行表 A06，未收口。
- [x] **T-A60d JD 投放记录与撤回（代码层）** ✅ `jd-publications` 创建 published 记录、内容快照、撤回与重复撤回反例。证据：`test_recruitment_api.py::test_jd_publication_withdraw`。⚠️ **真实 AI 可编辑 JD 草稿 + 人工确认发布闭环（执行表 A12）未实现**，外部招聘渠道实际投递按客户边界，不默认接入；投放记录不等于渠道投递成功。
- [x] **T-A60e 系统管理域表体系** ✅ 六表 `sys_departments` / `sys_roles` / `sys_user_roles` / `sys_role_permissions` / `sys_menus` / `sys_operation_logs` + 迁移 `A_0008_sys_admin`；`data_scope` 三值（`all` / `dept` / `self`）在 `system/models.py` 与 `core/auth/scope.py` 可用，`init_db.seed()` 幂等写入 6 个基础角色。⚠️ 完整组织/角色/行/字段权限矩阵归执行表 A06，未收口。
- [x] **T-A60f 个人端申请视图** ✅ `frontend/src/app/(person)/applications/page.tsx` + `page.test.tsx`；对外阶段中文标签、未绑定档案不发请求、内部枚举值不出现在 DOM、`result_published=true` 才显示结果类别均有前端用例。证据：前端 vitest 22 文件 / 104 用例全绿。⚠️ **跨申请隔离的浏览器点击流未验**，简历上传 file upload 需用户手动验收。

---

## 状态快照（v0.1.15，2026-11-10 11:00）

> 本节为**活状态锚点**，覆盖上方按时间线写的历史台账。权威顺序：`docs/updates/A/` 最新记录 > 根 `更新记录.md` 尾部 > `git log` > 本表。

### 基线与结构

| 项 | 当前值 |
|---|---|
| 分支 / HEAD | 开工 `fetch` 后四 ref 全部 `3cb0064`；**发布前再 fetch 发现 `origin/master 3cb0064 → 3eed209`（23 提交）**，按规则 rebase 对齐（双方零重叠、0 冲突），A02-1 本地提交 `9cfb607 → 5eb81c7`，rebase 后祖先复核 exit 0、工作区干净。按用户 10:39 口径执行本轮闭环，最终 hash 以远端为准；详见 [1130 补记](../../updates/A/20261110-1130-并发更新合并与复验补记.md) |
| 迁移链 | 新基线 `alembic heads` 单 head **`D_0012_digital_employee (head)`**；D 的 `D_0010` 挂 `C_0006` → `D_0011` 挂 `D_0010` → `C_0007` 改挂 `D_0011`，`D_0012` 挂 `B_0007_merge_learning_evidence`；`C_0001` 空库修复在新链下复验见测试门禁 |
| 表 | 一期断言 **84 张 / 模型 84**（D 新增 8 表） |
| 路由 | `backend/app/main.py` **17 个** `include_router`（D 新增 3） |
| 运行态 | 后端 8101 / 前端 3101 / MySQL 3308 / Milvus 19532，容器 `aitalent_main-*`；**本轮未重启** |

### 测试门禁（真实退出码）

| 项 | 结果 |
|---|---|
| 后端全量（隔离库 `ai_talent_a_probe`，补种子后，**`3eed209` 新基线**） | **exit 0**，784 用例 / 783 passed / 1 skipped / 0 failed / 0 error（首次 1 例失败为 A 侧表数硬编码 76→84 过期，已改动态一致性断言后串行全量复跑通过） |
| 前端 `npm test` | **exit 0**，29 文件 / 131 用例（新基线复跑，594s） |
| `tsc --noEmit` | **exit 0** |
| 空库 `alembic upgrade head` | **exit 0**，**34 步**到 `D_0012_digital_employee`；`C_0001` 修复在新链下仍有效 |
| A02-1 定向三文件（引擎 + 筛选 + 匹配） | **exit 0，33 passed**（旧硬淘汰用例按新口径重写，非删弱） |
| D02-A 单元单测 `test_app_integration_t02.py` | **exit 0，9 passed**（保留） |
| 静态 | `diff --check` **exit 0**；**275** 个受控 backend py 文件 AST 0 错；17 个 `include_router`；84 表 / 去重 84 / 模型 84；单 head `D_0012`；Prompt C 的 `required` 与 `MatchScoreOutput` 字段集合实测一致，D 侧断言字符串仍成立 |

> 纪律：禁用 `pytest | tail`（`tail` 的 0 会掩盖真实失败）；一律重定向日志 + 单独 `echo $?`；`--basetemp` 用项目内 `.pytest_tmp_A_*`。主库 `ai_talent` 的 `alembic_version` 超前于实际 schema（缺三列 / 两条外键、表数 76），只读取证未处理，重建需用户授权。

### Master 统一执行表 · A 侧映射（P0 优先）

| ID | 优先级 | 状态 | 下一步 |
|---|---|---|---|
| A02 | P0 | **A02-1 已交付**（`3cb0064` 之后本轮）：硬门槛不再淘汰，无 `score=NULL/0`，不符项/待核实进 `misses` 并随 Prompt C 输入，全员评分；`estimated_llm_calls` 不再折减；后端 768 用例全绿。**A02-2 未完成**：分数仍是 `0.7/0.3` 向量混合 + `40/30/15/15` 加权 | A02-2 需 D 先改 `PROMPT_C_SCHEMA`（已发 [请求](../../requests/A/20261110-D-A02-2-PromptC综合分契约.md)），之后 A 改模型综合分；三档 `verdict` → 两档内部参考标签 + 人工三态属 M5，一并联调 |
| A06 | P0 | 部分（`core/auth/scope.py` 基础） | 补部门经理/管理层/培训职责权限，逐入口/任务/导出越权反例；对应 T-A15 |
| D02（A/D） | P0 | **A 侧开关已交付**（`RATE_LIMIT_ENABLED`，默认 false 演示豁免；true 只豁免 `/api/health`），5 用例覆盖 429/403/降级 | 客户模式在**真实 Redis + 多环境并存**下的串扰复验未做；D 归属 `ratelimit.py`/`client.py` 未改，代码级键前缀隔离需 A 发请求、D 实现 |
| A03 / A08 / A11 / A12 / A14 | P1 | 空白或未获完整实现证据 | 批次档案冻结、申请输入快照、审批链、真实 AI JD 草稿、员工花名册 |
| A01 / A04 / A10 / A13 / A15 | P1 | `×`（代码已实现） | 按执行表「通过条件」逐条接入/验收，**A 不替 Master 改表** |
| A05 / A09（配合 D/B） | P1 | 待联动 | A 交付授权需求与人工录用口径、招聘推荐规则 |
| G01/G02/G07、G03~G06/G08、Q01~Q10 | P1/P2 | 部分或待验收 | 按执行表逐项回传证据，人工/客户演练排最后 |

## 未收口清单（按用户约定，人工上场时再叫）

| 项 | 类型 | 说明 |
|---|---|---|
| 浏览器点击流 / 双端隔离回归 | 人工 | T-A02/T-A03/T-A15；`hr/hr123456` 登录→岗位→上传→解析→筛选→匹配→人工改判，以及越权反例 |
| 简历上传 file upload | 人工 | IAB 不支持文件上传，必须用户手动验收 |
| `/analytics` 运行时缺陷复验 | 人工 | [A 发出的请求](../../requests/A/20261109-D-analytics页运行时缺陷.md) 待 D 修后复验关闭（T-A02 阻塞项） |
| 主验收库 `ai_talent` schema 偏差 | 需授权 | stamp 已到 `C_0007` 但实际缺列/外键；重建或补迁移需用户明确授权 |
| `next build` / Agnes / 干净客户环境 | 未验证 | `next build` 受运行环境拦截记为未验证；Agnes `agnes-3.0-flash` 待 D 验证 |
| C 的 `REQ-C-20261109-121506` 关闭 | 对方 | A 已受理并交付证据；C 需在干净空库复跑 `upgrade head` 到 `C_0007` 并回填 `alembic current` 后自行关闭 |
| **A02-2 模型综合分 / D 契约** | 协作 | 等 D 响应 [A 发出的请求](../../requests/A/20261110-D-A02-2-PromptC综合分契约.md)：`PROMPT_C_SCHEMA` 加 `overall_score` 并同步断言；A 不越界改 D 的 `schemas.py` / `test_prompts.py` |
| 三档 `verdict` → 两档内部标签 + 人工三态 | 代码 | 设计方案 M5 / V2.6 §12.2；本轮三档仍为**内部参考标签**，不改申请状态、不自动推进/淘汰，待 A02-2 一并联调 |
| C-08 / C-09（M6） | 代码 | 幽灵规则清理、OpenAPI `response_model` 补齐 |
| B 的 9 表设计稿对照注册、D 通知接线 | 协作 | 按各自工作包推进，A 不代做 |

> 发布：用户 10:39 已把「继续」定义为「拉云仓对齐 master → 跑 A → 每跑通一个单元都测试 → 拉云仓 → 推送 → 合并主 master」，本轮 A02-1 按该闭环发布；发布前仍按 H-A17 再 `fetch` + `merge-base --is-ancestor`，最终提交号以远端为准。早期 v0.1.13 / 09:55 的「未授权 push」口径已被新口径取代，历史记录 append-only 保留供追溯。
