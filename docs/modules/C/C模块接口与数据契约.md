# C 模块接口与数据契约

日期：2026-11-06；更新：2026-11-07（自适应测评）；维护角色：C；状态：实施中（C 自有代码与测试已落地后更新验证结论）。
依据：[一期需求细化](一期需求细化.md)、[需求细化与确认](需求细化与确认.md) C-Q01～15、[跨模块配合清单](跨模块配合清单.md)。

本文件只描述 C 自有实现，不用“已完成/已验收”描述待 A/B/D 接入的部分。所有接口统一使用现有 `{code,message,data}` 信封，成功由 `app.core.response.ok()` 返回；错误由 `BizError` 抛出。

## 1. 权限与身份口径（一期临时边界，待 A 契约替换）

- 管理员：`User.role ∈ {"admin","hr"}`；沿用现有种子账号 `hr/hr123456`。一期无 RBAC，该判断是 C 业务层临时口径，A 提供正式身份上下文后替换。
- 员工：`User.role == "employee"`；候选人：非 `employee` 且非管理员。
- 本人：以服务端 `CurrentUser.id` 判定，不接受客户端自报 user_id/person_id。
- 员工资格只用于培训；C 不根据简历推断是否在职。候选人入职后由 A 维护身份关联并改为员工角色，C 再允许培训分配。
- 面试授权：管理员全部可见；面试人（`interviewer_user_ids` 含当前用户）可见本人被安排的轮次及内部反馈；候选人只通过 `candidate_user_id` 查看本人安排，不返回反馈。
- 成绩公开：招聘结果仅 `published_result_version` 对应版本对本人可见；员工结果在评分完成（`completed`）后本人可见最新版本。管理员始终可见。

## 2. 数据表（C 归属，模型位于 backend/app/modules）

22 张表，均使用 `app.database.Base`、`PKMixin`、C 自有可移植时间 Mixin（`created_at/updated_at` 默认 `CURRENT_TIMESTAMP`，应用层维护 `updated_at`，避免 MySQL `ON UPDATE` DDL 阻断 SQLite 测试）。

测评：`assessment_questions`、`assessment_plans`、`assessment_papers`、`assessment_assignments`、`assessment_selections`、`assessment_answers`、`assessment_results`、`assessment_reviews`、`assessment_review_items`。
面试：`interview_rounds`、`interview_feedback`、`interview_round_events`。
培训：`training_courses`、`training_sessions`、`training_attendances`、`training_feedbacks`、`training_feedback_themes`、`training_plans`、`training_plan_items`、`training_plan_versions`、`training_learning_records`、`training_growth_events`。

关键字段与语义：

- 题目：岗位、维度、维度类型（professional/general）、难度（easy/medium/hard）、题型（single_choice/short_answer）、题干、选项、标准答案、参考要点、评分说明、状态（pending/approved/disabled）。
- 方案：用途（recruitment/employee）、岗位及岗位版本、`dimensions_json`（名称/类型/权重，权重合计 100）、目标题量、总分线、难度结构、状态、`version`；`mode`（adaptive 默认 / fixed 兼容）、`strategy_json`（自适应冻结策略）、`bank_snapshot_json`（发布时题库快照：题目 id、数量与冻结时间）。
- 试卷：固定模式方案版本对应的冻结快照（题目全文、维度题量、满分、评分规则、总分线）；同一方案版本只生成一套试卷。自适应模式无试卷。
- 分配：plan/paper 版本（自适应的 `paper_id` 为空）、`mode`、策略快照、目标题量、答题人 user_id、talent_profile_id（B 契约映射，可空）、状态、公开版本、答案开放标记、幂等键、复测序号。
- 选题留痕：自适应逐题记录 `sequence_no`、题目快照、维度/难度、选题理由（覆盖/配额/回退、期望与实际难度、上一题得分）与当次策略快照；同一任务内题目不重复。
- 答案：题目快照、选择/文本、是否正确、得分（0–100）、评分状态（pending/graded/failed/manual）、失败原因。
- 结果：版本化，保存维度百分制分、总分（DECIMAL(6,2) 展示，比较使用未舍入值）、是否通过、`ability_json`（雷达数据、维度档位、强项/短板与评级）；`assessment_reviews` 保存人工修分审计。
- 面试轮次：候选人/岗位、轮次名、时间、方式、地点或链接、面试人列表、状态（pending/completed/cancelled）、内部备注、客户端幂等键 `client_request_id`（唯一、可空）、版本；变更写 `interview_round_events`。
- 面试反馈：每轮一条，评价/事实依据/优势/不足/推进建议（advance/hold/reject），作者与版本；**无面试总分字段**。
- 课程：标题、介绍、真实材料链接、适用维度、有效状态、版本、统一改进说明（`feedback_note`，不随版本回写历史计划）。
- 期次：课程、讲师、开始/结束时间；反馈窗口从该期次结束时间起 7 天。
- 出勤：期次 + 员工唯一，仅登记过的员工具备该课程评价资格。
- 评价：课程 + 员工唯一（跨期合并为一份），课程/讲师 1–5 分、可选匿名文字、状态 submitted/withdrawn、来源期次与时间。
- 反馈主题：课程维度保存模型归纳后的主题标签与支持份数，只保存达到 5 份支持的主题，不保存原文。
- 培训计划：员工 user_id、目标岗位、来源（ai/manual）、状态（draft/assigned）、证据快照、计划版本；项目保存课程快照，不随课程后续编辑回写；`training_plan_versions` 保存历史快照。
- 学习记录：按计划项+员工唯一，状态 not_started/learning/completed，备注、自报时间；完成仅代表自报。
- 成长事件：追加式记录测评完成、培训分配、课程完成。

## 3. 固定与自适应测评及计分规则

固定模式（兼容）：

1. 发布校验：维度数 ≤ 总题量；权重合计 100；总分线 0–100；每个维度至少分配 1 题；按权重最大余数法分配剩余题量。
2. 选题：只从 `approved` 题目中选；优先同岗位，其次通用（岗位为空）；按难度结构（默认 medium）与题目 id 升序固定选择，不使用随机数。任一维度可用题量不足则拒绝发布，返回缺口维度与缺口数量。
3. 试卷冻结：发布时保存题目全文、选项、标准答案/参考要点、评分说明、维度分配、权重、总分线；后续改题或改方案不影响已发布试卷和历史结果。
4. 计分：单选按标准答案精确匹配，答对 100、答错/未答 0；简答调用真实模型按评分说明返回 0–100；维度得分 = 该维度题目得分之和 / 题目数（每题满分 100）；总分 = Σ(维度得分 × 权重)/100；总分 ≥ 总分线（含相等）为通过。不设维度淘汰线。
5. 交卷：未答题记 0 分；重复交卷幂等，不重复评分；评分中的简答题以答案行为幂等单位，重试只处理 `failed`，已成功分数不变；模型失败不计零分，不生成结果、不公布。
6. 结果版本：首次完成生成 v1；人工修分后重新计算维度分、总分、通过结论并生成新版本；招聘新版本须管理员再次确认公布，旧公开版本仍可由本人查看；员工新版本评分完成即可见。
7. 答案开放：独立于成绩公开；招聘默认关闭，管理员可开放；员工在 `completed` 后本人可复习。开放前不返回标准答案、评分说明与错题解析。

自适应模式（默认，用户 2026-11-07 裁决）：

8. 策略冻结：发布时校验并冻结 `strategy_json`：最少/目标/最多题量（`min ≤ 目标 ≤ max`，`min` 不少于维度数，`max ≤ 200`）、起始难度、是否按表现升降难度、评级档位（必须含 0 分档）。不提前冻结每名考生的全部题目。
9. 题库快照：发布时把方案维度内全部 `approved` 题目 id 与数量冻结进 `bank_snapshot_json`；可用题量少于目标题量或某维度无题时拒绝发布。后续题库变化不影响已发布方案；答题时仍排除已停用题目。
10. 选题：先覆盖未作答维度；再按权重配额缺口选维度；难度取该维度上一题得分 ≥70 升一档、否则降一档（无历史时用起始难度），目标难度无题时就近回退；同难度候选按「同岗位优先 + 任务 id 稳定哈希」排序，实现千人千卷且同一任务可复现。每次选题写入 `assessment_selections` 留痕，同任务不重复出题。
11. 出题与交卷：`next-question` 幂等，返回当前未答题目；单选保存即确定性评分，用于调整下一题难度；简答保持待评分。结束条件为达到最多题量、或已达最少题量且覆盖全部维度并达到目标题量；题库耗尽时返回 `bank_exhausted`，由本人交卷。未作答题目交卷时按 0 分计。
12. 能力分析：结果按维度分生成雷达数据、维度档位、强项/短板、总分评级与摘要，存入 `ability_json`；评级档位默认「优秀≥90、良好≥80、合格≥70、待提升≥60、不合格<60」，可由方案策略覆盖，默认档位待 HR 确认。
13. 模型题库供给：管理员可调用 `questions/generate-draft` 由真实模型按岗位/维度/难度/题型生成待审核题目；生成结果一律 `pending`，人工审核通过后才可被选题使用；模型失败返回 503 且不写入。

## 4. 后端路由（prefix `/api`，由 A 在 main.py 注册）

测评 `/api/assessments`：

- `POST /questions`、`GET /questions`、`PATCH /questions/{id}`、`POST /questions/{id}/approve`、`POST /questions/generate-draft`（管理员；模型生成待审核题目）
- `POST /plans`、`GET /plans`、`GET /plans/{id}`、`PATCH /plans/{id}`、`POST /plans/{id}/publish`（管理员；自适应发布返回策略与题库快照，不返回试卷）
- `POST /assignments`（管理员分配；`idempotency_key` 幂等）
- `GET /assignments`（管理员列表）、`GET /my/assignments`（本人）
- `GET /assignments/{id}`（本人/管理员，按答案开放裁剪；自适应返回已出题目）
- `POST /assignments/{id}/next-question`（本人；自适应逐题出题，幂等）、`GET /assignments/{id}/selections`（管理员始终可见，本人答案开放后可见）
- `POST /assignments/{id}/start`、`PUT /assignments/{id}/answers/{question_id}`、`GET /assignments/{id}/answers`、`POST /assignments/{id}/submit`、`POST /assignments/{id}/retry-grading`（本人）
- `GET /assignments/{id}/result`（按第 1 节公开规则裁剪）
- `POST /assignments/{id}/publish`、`POST /assignments/{id}/open-answers`、`PATCH /assignments/{id}/answers/{question_id}/score`（管理员；修分必填理由）
- `POST /assignments/{id}/review-items/{question_id}/complete`（本人标记复习完成）

面试 `/api/interviews`：

- `POST /rounds`、`GET /rounds`、`GET /rounds/{id}`、`PATCH /rounds/{id}`、`POST /rounds/{id}/cancel`（管理员；`POST` 可带 `client_request_id`，同键重复提交返回首次创建的轮次）
- `GET /my/rounds`（候选人看本人安排，面试人看被授权轮次）
- `PUT /rounds/{id}/feedback`、`GET /rounds/{id}/feedback`（管理员/获授权面试人；轮次须 completed；`round_id` 唯一，重复保存为更新）
- 无面试总分、无自动招聘推进；`status` 与招聘状态独立。

培训 `/api/training`：

- `POST /courses`、`GET /courses`、`PATCH /courses/{id}`（管理员；材料链接必须 http/https）
- `POST /plans/draft`（管理员；真实课程 + 测评/档案输入调用模型生成草稿）
- `POST /plans`（管理员手工草稿）、`PATCH /plans/{id}`（仅 draft）、`POST /plans/{id}/assign`、`POST /plans/{id}/revise`（已分配计划生成新版本草稿）
- `GET /plans`、`GET /plans/{id}`（管理员）
- `GET /my/plans`（仅员工本人；候选人 403）
- `PUT /plan-items/{item_id}/progress`（员工本人自报学习状态）
- `POST /plans/draft-from-assessment/{assignment_id}`（固定流程入口；招聘测评返回 `generated=false` 与跳过原因，不生成候选人培训）
- `GET /growth`（管理员按员工/人员查询；员工查本人）
- `POST /courses/{id}/sessions`、`GET /courses/{id}/sessions`、`POST /sessions/{id}/attendance`、`GET /sessions/{id}/attendance`（管理员：期次与实际出勤）
- `GET /courses/{id}/feedback/mine`（员工本人状态）、`POST /courses/{id}/feedback`、`PATCH /courses/{id}/feedback`、`POST /courses/{id}/feedback/withdraw`（员工本人，窗口内）
- `GET /courses/{id}/feedback/summary`、`POST /courses/{id}/feedback-themes/refresh`、`PUT /courses/{id}/feedback-note`（管理员：匿名汇总、主题归纳、统一改进说明）

## 5. 后端执行与依赖边界

- 模型调用复用 `app.core.llm_client.chat_json`，C 自带 system prompt 与 Pydantic schema，不修改 D 的 `core/prompts` 注册表；提示版本写入结果/草稿快照。
- 本期不新增 TaskRunner kind（其合法类型由 D 维护）。交卷评分同步执行，失败项通过 `retry-grading` 重试；D 接入统一任务框架后可将同一服务方法作为 handler，C 不复制调度器。
- 表注册与路由注册需 A 集成：`backend/app/models/__init__.py` 导入 C 模型（或 init_db 显式导入）、`backend/app/main.py` 引入并 `include_router`。C 提供 `backend/app/modules/assessments/registry.py` 供 A 一次性引用。
- 本地测试使用 SQLite 持久化 + 注入式模型替身；MySQL、真实模型、真实浏览器联调未执行前不标验收通过。

## 6. 一期明确不做

AI 面试、音视频/监考/人脸/防挂机、面试总分与自动推进、候选人培训、培训自动分配/报名、课程审批候补、九宫格/人才池、自动人事决策、有效学时证明。固定试卷保留为兼容模式，不作为自适应替代；在线运行时自动生成并直接使用未审核题目不做。

## 7. 培训评价规则（D-34 / REQ-TRN-001 AC-6，2026-11-06 纳入一期）

1. 资格：仅员工本人；必须在对应课程期次有出勤登记。反馈窗口为该期次 `ends_at` 起 7 天（含截止时刻）；课程未结束或窗口关闭时拒绝提交/修改/撤回。
2. 去重：同一员工对同一课程跨期最多一份反馈；再次提交更新同一行并切换到最近一次有效出勤期次，汇总始终按一人一份计数。
3. 内容：课程满意度、讲师满意度各 1–5 分，可选文字意见（≤1000 字）；撤回在窗口内生效，撤回后不计入汇总，窗口关闭后本人仍可只读查看。
4. 匿名汇总：仅管理员（`admin/hr`）可查看；有效反馈少于 5 份时只返回份数，不返回均分与主题；达到 5 份才返回课程/讲师均分。接口永不返回文字原文或员工身份。
5. 主题归纳：管理员触发真实模型（`c-training-feedback-theme-v1`）对匿名文字归纳标签，只保存并展示支持份数 ≥5 的主题；模型失败返回 503 且不写入主题。
6. 改进说明：管理员依据汇总发布统一 `feedback_note`，不改课程版本；员工在本人评价状态中可见。
7. 角色边界：一期无讲师/部门经理角色，匿名汇总暂由管理员持有；后续开放需 A 提供角色与授权映射，C 不放宽现有权限。

## 8. 本轮实现与验证状态（2026-11-07）

- 已实现：`backend/app/modules/assessments`（模型/自适应策略与选题/评分/服务/路由/registry/模型出题）、`interviews`、`training` 三域与 22 张表；前端 `frontend/src/app/(main)/{assessments,interviews,target,training}` 页面、类型与接口封装，含自适应逐题答题与能力雷达。
- 已验证：`$env:PYTHONPATH="backend"; python -m unittest discover -s tests/C -p "test_*.py" -v` 运行 42 项全部通过（SQLite 内存库 + FastAPI TestClient + 注入式评分/出题/草稿/主题替身，覆盖固定组卷、自适应策略校验、覆盖优先与难度升降、任务种子分散与不重复、题量/维度结束规则、发布题库快照与不足拒绝、逐题幂等出题、单选即时评分、选题留痕权限、能力雷达/评级、模型出题 503 不写入、交卷幂等、评分失败重试、结果版本/公开、答案开放、面试可见性裁剪、培训分配/修订/自报/成长事件、评价资格/7 天窗口/跨期合并/撤回/5 份门槛/主题支持）。
- 已验证：`npm run typecheck` 与 `npm run build`（frontend）均通过（Next.js 静态页 `/assessments` 正常产出）。
- 未验证：MySQL/Alembic 实际迁移（C_0002 依赖 A 的 C_0001 链，本地无链文件与 MySQL 环境）、真实 LLM 评分/出题/主题归纳、真实 JWT 与账号权限、真实浏览器页面（自适应答题与雷达图未实机查看）、A 的 `main.py`/`models/__init__.py`/`AppShell` 集成。上述未验证项不得表述为验收通过。
- 接入方式：A 可 `from app.modules.assessments.registry import ALL_MODELS, ALL_ROUTERS`，在 `models/__init__.py` 导入模型（或 `init_db` 显式导入）并在 `main.py` 逐个 `include_router`；具体请求见 `docs/requests/C/20261106-A-公共集成接入请求.md`。
