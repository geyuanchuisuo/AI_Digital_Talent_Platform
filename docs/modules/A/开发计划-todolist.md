# 开发计划 · Todolist · 板块 A

> 版本：v0.1　|　日期：2026-11-06 21:09　|　身份：A　|　分支：`feature/A/continuation`　|　基线：`df6dae1`
> 上游：`docs/需求文档.md` **V2.2**、`docs/源码接入与需求决策.md`、`docs/四人分工与试行方案.md` §2、`docs/技术框架.md`、`tasks/A.md`、`docs/modules/A/设计方案.md` v0.1、`.collaboration/ownership.json`。
> 用法：**按 M1 → M6 顺序**；每项满足「验收点」才勾选，勾选必须写实际命令/结果/证据落点；编号 `T-Axx` 供沟通引用。进度同步 `docs/modules/A/开发日志.md` Layer 3/4 与 `docs/updates/A/` 交接记录。
> ⚠️ 本表是台账，**不是完成声明**；`[x]` 只表示「有实证且已登记」。3–4 天是首轮检查窗口，M1 实跑后按证据重排。

---

## 批次总览

| 批次 | 内容 | 任务数 | 前置依赖 | 状态 |
|---|---|---|---|---|
| M0 | 分支同步与接力底座 | 4 | 无 | ✅ 完成（纯文档，未提交） |
| M1 | 第 1 天环境与能力探针 | 5 | M0 | ⬜ **下一步，最高优先级** |
| M2 | 身份、person_id 绑定、双端授权 | 5 | M1、B 映射契约 | ⬜ 部分阻塞 |
| M3 | 人才需求、AI JD、招聘状态与邀约 | 4 | M1、Agnes 可用 | ⬜ 阻塞 |
| M4 | AI 综合评分替换来源评分 | 4 | M1、B 档案快照、D 模型契约 | ⬜ 阻塞 |
| M5 | 分数线、快照、逐结果失败、人工结论分离 | 4 | M4、迁移链验证 | ⬜ 阻塞 |
| M6 | 公共集成与回归 | 3 | M2–M5 | ⬜ 未开始 |

---

## M0 · 分支同步与接力底座 ✅

- [x] **T-A00a 同步远端 master** ✅ 2026-11-06 完成（`git fetch` 全分支 + 本地 `master` 快进 `07677bc → df6dae1`，未切到 master、未产生提交；见[开发日志](开发日志.md) Layer 3 §A-00）
- [x] **T-A00b 建立并登记 A 工作分支** ✅ 完成（`feature/A/continuation` @ `df6dae1`；`setup_collaboration.py --actor A --branch ...` 写入 `collab.actor` / `collab.branch` / `core.hooksPath=.githooks`；`check_scope.py --staged` 与 `--base master` 均 `Scope OK, changed_paths=0`）
- [x] **T-A00c 建立分层日志 + 勾选式 todolist + 设计方案** ✅ 完成（沿用旧 `main/` 体例：Layer 0–6 日志、本 todolist、[设计方案 v0.1](设计方案.md)；落在 `docs/modules/A/**` 授权目录，根级同名文件未创建）
- [x] **T-A00d 首条交接记录 + 跨包请求 + 集成索引** ✅ 完成（`docs/updates/A/20261106-210900-...md`、`docs/requests/A/` 3 份、根 `更新记录.md` 追加一条）
- [x] **T-A00e 全仓只读静态架构核查** ✅ 2026-11-06 21:26 完成（360 文件 / 60,758 行；132 py AST 0 错、内部导入 0 断裂、归属表覆盖偏差 31/278；勘误评分链路措辞；结论见[开发日志](开发日志.md) Layer 3 §A-01、H-A11/H-A12）。**仅静态，运行态未验证，不能替代 M1**

## M1 · 第 1 天环境与能力探针（未开始 → **接力 Agent 从这里开工**）

- [ ] **T-A01 依赖、建库与启动探针** — 建 venv、`pip install -r backend/requirements.txt`、`init_db.py`、`check_env.py --skip-llm`、`uvicorn app.main:app --workers 1`、`curl /api/health`。验收点：7 项自检无 FAIL、`status=ok`、worker alive。`--workers 1` 是硬约束。证据：新增 `docs/updates/A/<时间戳>-M1首轮探针.md`
- [ ] **T-A02 现有招聘链路浏览器复现** — `hr/hr123456` 登录 → 建岗位 → 传 1 份 `docs/imports/hhy_gycs_master/eval/resumes/*.docx` → 解析 → 筛选 → 匹配 → 人工改判。验收点：**记录实际浏览器是否验证**、接口返回、耗时；不得用源码存在替代实测。
- [ ] **T-A03 权限与失败边界反例** — 无 token / 停用账号 / 重复提交 / 单份失败 / 重启后任务恢复。验收点：401/403、幂等不重复建结果、runner 恢复不永久卡 running。
- [ ] **T-A04 契约差距核对** — 抓 `/api/job-profiles`、`/api/screen/batches`、`/api/match/batches` 与 OpenAPI，逐字段比对 `docs/modules/A/源码接入基线.md`。验收点：差异写进 `docs/modules/A/` 契约文件并登记 Layer 4。
- [ ] **T-A05 外部能力核对（与 D 协同）** — Agnes `agnes-3.0-flash` 鉴权/超时/结构化输出、独立 Embedding、Milvus collection 维度。验收点：**未验证即写未验证**，不假定 Agnes 提供 Embedding/OCR。
- [ ] **T-A06 按探针结果重排工期** — 更新本表 M2–M6 状态、阻塞与下一检查点；不把 3–4 天写成承诺。

## M2 · 身份、person_id 绑定与双端授权（F09 / 需求 §3、§12.1）

- [ ] **T-A11 User 与账号端类型** — `backend/app/models/user.py` + `models/__init__.py` + `scripts/init_db.py`：增 `person_id` 绑定、端类型，seed 1 管理员 + ≥2 个人账号（虚构数据）。验收点：迁移可重放、旧账号仍可登录。
- [ ] **T-A12 JWT / UserOut / AuthUser 贯通 person_id** — `service/auth_service.py`、`schemas/auth.py`、`dao/users.py`、`frontend/src/lib/auth.ts`、`lib/types.ts`。验收点：登录返回含 person_id，刷新不丢；旧字段保留兼容。
- [ ] **T-A13 后端本人数据授权** — `backend/app/core/auth/**`、`modules/identity/**`、`api/deps.py`：`require_admin` / `require_self(person_id)`。验收点：**改请求 ID 访问他人档案/成绩/学习记录返回 403**；前端隐藏按钮不算授权。
- [ ] **T-A14 双端入口与端分发** — `frontend/src/app/login/**`、`app/page.tsx`、`components/{AppShell,AuthGuard}.tsx`、`frontend/src/features/identity/**`。验收点：切换账号后菜单与数据正确（F09）。⚠️ **R-A01**：`frontend/src/app/(person)/**` 未登记归属，路由壳需用户单独授权后落地，不擅自改 `ownership.json`。
- [ ] **T-A15 双端隔离回归** — 个人端不可见管理端功能、不可读他人数据；管理员可关联账号与档案。验收点：越权反例 + 重启持久化。

## M3 · 人才需求、AI JD、招聘状态与邀约（F01）

- [ ] **T-A21 人才需求实体与页面** — `backend/app/modules/recruitment/**`、`frontend/src/app/(main)/job-profiles/**`、`frontend/src/features/recruitment/**`。字段：岗位、`headcount`、说明、状态；验收点：创建后可重新打开修改。
- [ ] **T-A22 AI 可编辑 JD 草稿** — 真实模型生成、保存岗位版本 + 模型/Prompt 版本 + 人工编辑值；验收点：**模型失败明确提示，不返回样例**；保存后可查。⚠️ 依赖 R-A02。
- [ ] **T-A23 招聘状态与邀约记录** — 候选人 person_id、岗位版本、状态、邀约时间/方式/状态/说明、面试备注；只存记录，不发邮件/短信。验收点：同一候选人的筛选→邀约→面试引用可连续查询。
- [ ] **T-A24 需求人数口径展示** — 页面显示需求人数、人工标记进入下一步人数、剩余需求并写明口径（人工推进 ≠ 已录用，§12.3）。验收点：与数据库人工核算一致；重复提交不重复创建。

## M4 · AI 综合评分替换来源评分（F03 / C05）

- [ ] **T-A31 评分服务改造** — `service/match_engine.py`、`core/prompts/match.txt`、`backend/app/modules/matching/**`：模型分析岗位 + 完整档案 → 0–100 分 + 依据 + 不足 + 待核实；替代 `hard_gate` 与 `0.7/0.3` 加权公式，**保留来源实现作兼容对照记录**。验收点：至少两份档案对同一岗位产生有差异、有依据的结果。
- [ ] **T-A32 正式 Worker 输入补全** — `tasks/screen_worker.py`、`tasks/match_worker.py` 接 B 的带版本档案快照（经历/项目证据、`current_title`、tags、能力分、档案版本）。验收点：**正式链路**实测，不用历史评测脚本输入冒充。⚠️ 依赖 R-A03。
- [ ] **T-A33 输出校验与结果快照** — 后端校验分数范围/字段，保存岗位版本、档案输入快照、模型名、Prompt 版本、模型原始返回。验收点：刷新只展示已保存结果，不触发重算；失败单独状态。
- [ ] **T-A34 评分边界** — 等于分数线归通过、技能词边界（Java≠JavaScript、C≠C++）、缺失证据标待核实、年龄性别不参与、语义相似度不等于业务分、不把模型自报准确率当实测。验收点：逐条反例测试。

## M5 · 分数线、快照、失败记录与人工结论分离（F03 / §12.1）

- [ ] **T-A41 初筛两档 + 人工复核三态** — 通过/未通过 + 待复核/建议进入下一步/暂不推进 + 备注；源码三档登记映射，不全仓重命名、不改写历史数据。验收点：等于分数线、评分失败、人工调整后刷新保留。
- [ ] **T-A42 批次快照与幂等** — 固定岗位版本、分数线、人员名单、档案输入快照、模型/Prompt 版本；retry 只补失败项、不重圈人；岗位/档案变更新建一轮。验收点：重复请求不重复建结果，历史人工意见不被覆盖。
- [ ] **T-A43 逐结果失败与人工结论字段** — `models/job.py` 批次/结果补 `model_version`、`prompt_version`、`failure_reason`、`attempt`；`match_results` 补人工结论字段对齐 `screen_results.manual_verdict*`。验收点：单份失败可单独重试，不影响同批成功项。
- [ ] **T-A44 迁移链实跑** — `backend/migrations/versions/A_*.py`（**目录当前不存在，需先建骨架**）+ `models/__init__.py` 13 表断言 + `init_db.py`、`backup_db.py` 清单同步；**`create_all` 不 ALTER**，必须验证「已有库升级」与空库两条路径。验收点：升级后旧历史批次仍可追溯。

## M6 · 公共集成与回归

- [ ] **T-A51 入口与依赖集成** — `backend/app/main.py` 注册新路由；`requirements.txt`、`frontend/package.json`、`.env.example` 由 A 统一集成（加包先走 `docs/requests/A/`，不替 B/C/D 改）。验收点：`--workers 1` 下全路由可达，健康检查正常。
- [ ] **T-A52 全链路回归** — 登录双端、越权、阈值等于、评分失败、重复请求、批次快照、重启持久化、浏览器实测；与 C 联调面试引用。验收点：每项记录输入/预期/实际/证据位置。
- [ ] **T-A53 指标口径交付 + 集成索引** — 向 D 提供岗位需求/人工推进/缺口/匹配复核的 metric 定义（时间字段、去重键、允许维度、权限范围）；根 `更新记录.md` 追加集成摘要。验收点：D 的白名单可核对到 A 的业务记录。

---

## 阻塞登记（接力 Agent 先看这张表）

| 编号 | 阻塞 | 影响任务 | 当前处理 |
|---|---|---|---|
| R-A01 | `frontend/src/app/(person)/**` 未登记归属，`owner_of()` = None | T-A14 | 等用户明确授权后**单独**更新 `ownership.json` 与规则；不擅自改 |
| R-A02 | Agnes 协议/结构化输出、独立 Embedding、Milvus 未验证 | T-A05、T-A22、T-A31 | 与 D 在 M1 共同验证；未过按需求 §7 记阻塞，不伪造成功 |
| R-A03 | B 的 `talent_profile_id → person_id` 映射与档案版本契约未回 | T-A11、T-A32 | 请求已发 `docs/requests/A/20261106-B-...md`；A 先做不依赖映射的部分 |
| R-A04 | 13 表冻结、迁移链未实跑 | T-A11、T-A44 | M1 先确认本机 DB 状态；M5 单独验升级路径 |
| R-A05 | 本机 `python` 无 pytest；`backend/.env`、`.venv`、`frontend/node_modules` 均缺失 | T-A01 及全部测试 | M1 建 venv 后按 `pytest.ini` / `npm test` 实跑；未跑不写通过。⚠️ 8000/3306/19530 端口虽在监听但属旧 main 实例与既有基础设施（H-A11），不能当验收证据 |

## 跨包请求状态

| 请求 | 目标 | 状态 | 等待什么 |
|---|---|---|---|
| [B 档案版本与证据契约](../../requests/A/20261106-B-档案版本与证据契约.md) | B | 待响应 | person_id 映射、不可变输入快照、证据读取接口 |
| [D 模型任务与评分版本契约](../../requests/A/20261106-D-模型任务与评分版本契约.md) | D | 待响应 | Agnes 验证结论、模型/Prompt 版本标识、任务状态约定 |
| [C 人工面试引用契约](../../requests/A/20261106-C-人工面试引用契约.md) | C | 待响应 | 招聘记录所需的引用 ID / 状态 / 可见范围 |

> 维护人在自己的 `docs/updates/<角色>/` 新增响应并链接回请求；请求方不得替对方改代码，也不得把「待响应」写成「已实现」。

## 变更记录

- v0.1（2026-11-06）：建立 A 的勾选式 todolist；M0 四项完成，M1–M6 待办；登记 5 项阻塞与 3 份请求。未实施业务代码。
- v0.1.1（2026-11-06 21:26）：M0 补 T-A00e 静态架构核查（已完成、仅静态）；T-A44 标注 migrations 目录待建；R-A05 补环境缺口与端口错觉。M1 仍是唯一下一步。
