# C 模块接口与数据契约

日期：2026-11-06；更新：2026-11-07（自适应测评 + 一期扩表：盘点/AI 面试间/报名审批/学习证据）；2026-11-11（MASTER 集成收口 C06/C08/C09/C13/C14：报名职责审批链、学习心跳防挂机与按课程完成条件、经理本人初评与负责范围、成长轨迹最新事实与 F10 隔离、正式评级授权范围，迁移 C_0011）；2026-11-12（MASTER 按 REQ-MASTER-20261112-004136-C 修复：C06 容量口径含待确认预留、C08 心跳三键分离与计费容差、乱序/重复语义入契约；C_0011 迁移文件改名 `C_0011_enroll_study.py`，revision id 缩短至 ≤32 字符，MySQL VARCHAR(32) 版本列可升级）；维护角色：C；状态：实施中（C 自有代码与测试已落地后更新验证结论）。
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

32 张表，均使用 `app.database.Base`、`PKMixin`、C 自有可移植时间 Mixin（`created_at/updated_at` 默认 `CURRENT_TIMESTAMP`，应用层维护 `updated_at`，避免 MySQL `ON UPDATE` DDL 阻断 SQLite 测试）。

测评：`assessment_questions`、`assessment_plans`、`assessment_papers`、`assessment_assignments`、`assessment_selections`、`assessment_answers`、`assessment_results`、`assessment_reviews`、`assessment_review_items`。
面试：`interview_rounds`、`interview_feedback`、`interview_round_events`。
AI 面试间：`ai_interview_sessions`、`ai_interview_questions`、`ai_interview_events`。
培训：`training_courses`、`training_sessions`、`training_attendances`、`training_feedbacks`、`training_feedback_themes`、`training_plans`、`training_plan_items`、`training_plan_versions`、`training_learning_records`、`training_growth_events`、`training_enrollments`、`training_learning_evidence`、`training_study_sessions`（C08 学习会话心跳留痕，迁移 C_0011）。
岗前培养：`prejob_plans`、`prejob_phases`、`prejob_plan_versions`（F05/C12，面向新入职/待上岗人员的分阶段计划，与在职培训计划分离，迁移 C_0009）。
正式能力评级：`capability_ratings`、`capability_rating_changes`（F05/C14，按维度版本化的正式评级 + 变更审核链，迁移 C_0010）。`capability_ratings` 仅由 HR 审核通过的变更追加新版本（`effective` 标记当前生效版本，历史不覆盖）；`capability_rating_changes` 记录「实践成果证据 + 经理评价 → HR 审核」状态机（`pending_hr`/`approved`/`rejected`）。
盘点：`inventory_reviews`、`inventory_talent_pools`、`inventory_disputes`、`inventory_development_plans`、`inventory_events`、`inventory_grid_configs`（F05a/C09，九宫格标准阈值经 HR 批准的版本表）。

关键字段与语义：

- 题目：岗位、维度、维度类型（professional/general）、难度（easy/medium/hard）、题型（single_choice/short_answer）、题干、选项、标准答案、参考要点、评分说明、状态（pending/approved/disabled）。
- 方案：用途（recruitment/employee）、岗位及岗位版本、`dimensions_json`（名称/类型/权重，权重合计 100）、目标题量、总分线、难度结构、状态、`version`；`mode`（adaptive 默认 / fixed 兼容）、`strategy_json`（自适应冻结策略）、`bank_snapshot_json`（发布时题库快照：题目 id、数量、冻结时间与题目全文/答案/评分版本快照 `questions`）。
- 试卷：固定模式方案版本对应的冻结快照（题目全文、维度题量、满分、评分规则、总分线）；同一方案版本只生成一套试卷。自适应模式无试卷。
- 分配：plan/paper 版本（自适应的 `paper_id` 为空）、`mode`、策略快照、目标题量、答题人 user_id、talent_profile_id（B 契约映射，可空）、状态、公开版本、答案开放标记、幂等键、复测序号。
- 选题留痕：自适应逐题记录 `sequence_no`、题目快照、维度/难度、选题理由（覆盖/配额/回退、期望与实际难度、上一题得分）与当次策略快照；同一任务内题目不重复。
- 答案：题目快照、选择/文本、是否正确、得分（0–100）、评分状态（pending/graded/failed/manual）、失败原因。
- 结果：版本化，保存维度百分制分、总分（DECIMAL(6,2) 展示，比较使用未舍入值）、是否通过、`ability_json`（雷达数据、维度档位、强项/短板与评级）；`assessment_reviews` 保存人工修分审计。
- 面试轮次：候选人/岗位、轮次名、时间、方式、地点或链接、面试人列表、状态（pending/completed/cancelled）、内部备注、客户端幂等键 `client_request_id`（唯一、可空）、版本；变更写 `interview_round_events`。
- 面试反馈：每轮一条，评价/事实依据/优势/不足/推进建议（advance/hold/reject），作者与版本；**无面试总分字段**。
- 课程：标题、介绍、真实材料链接、适用维度、有效状态、名额上限（`capacity`，空为不限）、版本、统一改进说明（`feedback_note`，不随版本回写历史计划）。C06/C08 扩列（迁移 C_0011）：`approval_mode`（`none` 免审批 / `manager` 直属经理审批 / `admin` 管理员审批，默认 `admin`）、`has_cost`（涉费）、`occupies_work_time`（占用工时）、`completion_rules_json`（按课程完成条件：`min_evidence_score` 最低证据分、`required_evidence_types` 必备证据类型，空则用全局默认阈值）。
- 期次：课程、讲师、开始/结束时间；反馈窗口从该期次结束时间起 7 天。
- 出勤：期次 + 员工唯一，仅登记过的员工具备该课程评价资格。
- 评价：课程 + 员工唯一（跨期合并为一份），课程/讲师 1–5 分、可选匿名文字、状态 submitted/withdrawn、来源期次与时间。
- 反馈主题：课程维度保存模型归纳后的主题标签与支持份数，只保存达到 5 份支持的主题，不保存原文。
- 培训计划：员工 user_id、目标岗位、来源（ai/manual）、状态（draft/assigned）、证据快照、计划版本；项目保存课程快照，不随课程后续编辑回写；`training_plan_versions` 保存历史快照。
- 学习记录：按计划项+员工唯一，状态 not_started/learning/completed，备注、自报时间；完成仅代表自报，不等于正式完成。
- 学习证据：`evidence_type` 仅 `attendance`（本人该课程真实出勤）/`assessment`（本人已通过的员工测评）/`study_session`（服务端签发的一次性学习会话凭证）；`score` 与 `verified_by` 只能由服务端可信来源计算（`system.attendance`/`system.assessment`/`system.study_session`），客户端不可直传；`learning_record_id + evidence_key` 唯一，阻止同一来源重复计入与凭证重放。C08：`study_session` 有效学时改由服务端心跳累计（见下），不再按签发后经过时间折算。
- 学习会话（C08，`training_study_sessions`）：`nonce`（唯一，HMAC 签名绑定学习记录/员工）、`issued_at`/`expires_at`（有效期 `STUDY_SESSION_TTL_MINUTES`，默认 120 分钟）、`last_heartbeat_at`、`heartbeat_count`、`active_minutes`、`consumed`（一次性兑换标记）。员工学习期间周期性上报心跳，服务端仅累计相邻心跳间隔 ≤（空闲窗口 + 容差）的有效学时，单区间累计 ≤ 单次上限；需 ≥ 最少心跳次数且有效学习 ≥ 最短时长方可结算证据，挂机等待（无心跳）不计学时。**计时参数全部配置化**（`config.py`）：`STUDY_HEARTBEAT_INTERVAL_SECONDS`（发送周期，默认 30）、`STUDY_HEARTBEAT_IDLE_SECONDS`（空闲窗口，默认 90）、`STUDY_HEARTBEAT_GAP_TOLERANCE_SECONDS`（计费容差，默认 10，仅吸收调度/网络抖动，不放宽离开判定）、`STUDY_HEARTBEAT_CREDIT_CAP_SECONDS`（单次上限，默认 90）、`STUDY_HEARTBEAT_MIN_COUNT`（默认 3）、`STUDY_SESSION_MIN_MINUTES`（默认 5）。发送周期应明显小于空闲窗口。**时序语义**：计时全用服务端时间，客户端自报分钟/is_active 被 schema 拒绝（422）；乱序心跳（早于上次）400；同一时刻重复心跳幂等（不加时不加次数）；过期/已结算会话拒绝；行锁 + 条件更新防多端并发把同一区间累计两次。凭证兑换后 `consumed=1`，重放拒绝。
- 报名（C06，`training_enrollments`）：状态扩 `pending_admin_review`（经理已通过、涉费/工时待管理员复核）；新增 `manager_decided_by`/`manager_decided_at`（经理本人审批留痕）。审批链按课程 `approval_mode`：`none` 且无费用/工时→报名即通过；`manager`→直属经理本人审批（管理员不可越过），涉费/工时再转 `pending_admin_review` 由管理员复核；`admin`→管理员审批。**容量口径**：满员判定 = 正式占位（`pending`/`pending_admin_review`/`approved`）+ 未过期 `awaiting_confirmation` 预留合计 ≥ 容量；新报名不得抢走已递补给候补的待确认名额。报名/递补/确认共用课程行锁串行化。候补递补 `awaiting_confirmation` 须员工本人 24h 内确认，经理/管理员不可代确认；**本人确认的容量预检排除自身预留**（确认只是预留转占位，总量不变），他人真实占位已满仍拒绝。waitlisted 仅可取消或经递补→本人确认→审批链，不可直批。
- 成长事件：追加式记录测评完成、培训分配、课程自报完成（`course_completed`，`self_reported=true`）与正式完成（`course_formally_completed`，`self_reported=false`，引用计划版本与可信证据分/验证人）；正式完成仅在 `formal_completed` 0→1 时追加一次，重复确认不重复写入。

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
9. 题库快照：发布时把方案维度内全部 `approved` 题目 id、数量与题目全文/答案/评分版本快照（`questions` 映射）冻结进 `bank_snapshot_json`；可用题量少于目标题量或某维度无题时拒绝发布。出题与评分读冻结快照，发布后改题重审不影响旧方案；答题时仍按当前题库状态排除已停用题目。仅有 id 快照的历史方案回退读当前题库内容。
10. 选题：先覆盖未作答维度；再按权重配额缺口选维度；难度取该维度上一题得分 ≥70 升一档、否则降一档（无历史时用起始难度），目标难度无题时就近回退；同难度候选按「同岗位优先 + 任务 id 稳定哈希」排序，实现千人千卷且同一任务可复现。每次选题写入 `assessment_selections` 留痕，同任务不重复出题。
11. 出题与交卷：`next-question` 幂等，返回当前未答题目；单选保存即确定性评分，用于调整下一题难度；简答保持待评分。结束条件为达到最多题量、或已达最少题量且覆盖全部维度并达到目标题量；题库耗尽时返回 `bank_exhausted`，由本人交卷。未作答题目交卷时按 0 分计。
12. 能力分析：结果按维度分生成雷达数据、维度档位、强项/短板、总分评级与摘要，存入 `ability_json`；评级档位默认「优秀≥90、良好≥80、合格≥70、待提升≥60、不合格<60」，可由方案策略覆盖，默认档位待 HR 确认。
13. 模型题库供给：管理员可调用 `questions/generate-draft` 由真实模型按岗位/维度/难度/题型生成待审核题目；生成结果一律 `pending`，人工审核通过后才可被选题使用；模型失败返回 503 且不写入。

## 4. 后端路由（prefix `/api`，由 A 在 main.py 注册）

测评 `/api/assessments`：

- `POST /questions`、`GET /questions`、`PATCH /questions/{id}`、`POST /questions/{id}/approve`、`POST /questions/generate-draft`（管理员；模型生成待审核题目）
- `POST /plans`、`GET /plans`、`GET /plans/{id}`、`PATCH /plans/{id}`、`POST /plans/{id}/publish`（管理员；自适应发布返回策略与题库快照，不返回试卷）
- `POST /assignments`（管理员分配；`idempotency_key` 幂等；`attempt_no` 递增形成复测新记录，不覆盖旧结果）
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
- `GET /growth/trajectory`（C13/F05 跨域成长轨迹聚合：管理员可按 `employee_user_id`/`talent_profile_id` 查询，员工强制查本人。返回 `domains`（assessment/interview/training/hr_ability 四域事件计数与正式/自报拆分）、`dimensions`（维度状态 `formal`/`assessed`/`not_assessed`，正式取生效能力评级版本、测评取最新结果分，皆无则标未评估）、`timeline`（按真实事件追加，逐条引用 `source_type`/`ref_type`/`ref_id`/`version` 并标 `self_reported`/`formal`）。仅归集已登记事实，不推断能力提升幅度；未评估维度不代表能力为 0。测评完成事件带 `source_type=assessment`、面试轮次完成事件带 `source_type=interview`、正式评级变更带 `source_type=hr_ability` 作为轨迹来源。**C13 收口**：同维度多次测评按事实时间 `(fact_at, version, id)` 倒序取最新结果（不再按 assignment 升序首次命中），`assessed` 来源带 `ref_id`/`assignment_id`/`assessed_at` 便于定位；F10 隔离——员工视角过滤 `hr_ability` 域事件、不返回内部正式评级维度（`formal` 仅管理侧/负责经理可见），与盘点内部评级不可见规则对齐。）
- `POST /courses/{id}/sessions`、`GET /courses/{id}/sessions`、`POST /sessions/{id}/attendance`、`GET /sessions/{id}/attendance`（管理员：期次与实际出勤）
- `GET /courses/{id}/feedback/mine`（员工本人状态）、`POST /courses/{id}/feedback`、`PATCH /courses/{id}/feedback`、`POST /courses/{id}/feedback/withdraw`（员工本人，窗口内）
- `GET /courses/{id}/feedback/summary`、`POST /courses/{id}/feedback-themes/refresh`、`PUT /courses/{id}/feedback-note`（管理员：匿名汇总、主题归纳、统一改进说明）
- `POST /enrollments`、`GET /enrollments`、`POST /enrollments/{id}/decide`、`POST /enrollments/{id}/confirm`（员工报名必修/选修；有 `capacity` 时按 pending 占位、approved 占用计数，满额进入 waitlisted。名额释放后 `_promote_waitlist` 把最早候补转为 `awaiting_confirmation`（预留，非正式占位）并投递 `course.waitlist_promoted`，员工 24h 内经 `confirm` 确认后按课程审批模式进入相应审批；未确认或审批超时由 `release-expired` 置 `expired` 并递补下一位。`awaiting_confirmation` 阶段不允许管理员/经理直接 decide 或代确认。预留口径：pending+pending_admin_review+approved+awaiting_confirmation ≤ capacity，防超发；候补通知≠正式占位，确认≠自动报名。**C06 审批链**：报名按课程 `approval_mode` 分流——`none` 且无费用/工时→直接 approved；`manager`→pending 待直属经理本人审批；`admin`→pending 待管理员审批。`GET /enrollments` 按角色范围返回（管理侧全部、经理本部门、员工本人）。）
- `POST /enrollments/{id}/manager-decide`（C06 直属经理本人审批，仅 `manager` 模式课程、仅负责部门员工、仅 pending；`approved`/`rejected`。涉费/工时通过后转 `pending_admin_review` 并重置 24h 复核截止，否则直接 approved；拒绝触发候补递补。管理员不可越过经理环节直接 decide manager 模式 pending 报名。）
- `POST /enrollments/{id}/admin-review`（C06 培训管理员复核，仅 `pending_admin_review` 状态；`approved`/`rejected`，拒绝触发候补递补。）
- `POST /enrollments/release-expired`（管理员/外部调度：把超时未决定的 pending/pending_admin_review 与超期未确认的 awaiting_confirmation 置为 `expired` 释放占位/预留，并按课程递补候补；有期次时投递 `course.waitlist_promoted`）
- `POST /prejob-plans`、`GET /prejob-plans`、`GET /prejob-plans/{id}`、`PATCH /prejob-plans/{id}`、`POST /prejob-plans/{id}/assign`、`POST /prejob-plans/{id}/revise`（C12/F05 岗前培养计划：管理员为待上岗人员创建分阶段（目标、阶段、课程、导师）草稿，`assign` 生效并追加 `prejob_assigned` 成长事件，`revise` 生成新版本草稿保留历史。阶段课程须为目录内有效课程，失效/不存在即拒绝，不生成假课程；员工仅读本人计划；岗前计划不自动报名、不投递课程指派通知，与在职培训分配分离。）
- `POST /learning-records/{id}/study-sessions`（员工本人领取服务端签名的学习会话凭证，含有效期；**三键分离下发**：`heartbeat_interval_seconds` 前端发送周期、`idle_seconds` 服务端计费空闲窗口，两者独立配置不再同源）
- `POST /learning-records/{id}/study-heartbeat`（C08 员工本人学习心跳，body 仅 `session_token`（额外字段 422）；服务端按相邻心跳间隔 ≤（`idle_seconds` + 容差）累计 `active_minutes`（单区间 ≤ 上限）并递增 `heartbeat_count`，返回当前心跳数/有效学时/有效期。乱序 400、同一时刻重复幂等、过期/已结算拒绝；无心跳的挂机等待不计学时。）
- `POST /learning-evidence`、`GET /learning-records/{id}/evidence`（员工提交学习证据：出勤/已通过测评/学习会话；分数与验证人由服务端计算，客户端字段 `score`/`verified_by` 被严格拒绝；`study_session` 须 ≥ 最少心跳次数且有效学习 ≥ 最短时长（均配置化，默认 3 次/5 分钟），凭证一次性兑换；列表本人或管理员可见）
- `POST /learning-records/{id}/confirm-completion`（管理员依据服务端校验证据认定正式完成：无证据返回 400；**C08 按课程完成条件**——读取课程 `completion_rules_json`，最高证据分须 ≥`min_evidence_score`（缺省全局阈值），且须覆盖 `required_evidence_types` 全部必备证据类型，未达返回 400 并说明缺阈值或缺类型。）
- `POST /plan-items/{id}/extension`（管理员延期必修期限，`new_due_date` + `reason` 必填；留 `training_due_extended` 事件并通知）
- `POST /reminders/scan`（管理员/外部调度：按 `today`、`days_ahead` 扫描到期与逾期，经 D 通知中心投递，幂等）
- `POST /capability-rating-changes`、`GET /capability-rating-changes`、`POST /capability-rating-changes/{id}/review`、`GET /capability-ratings`（C14/F05 正式能力评级变更链：经理提交「实践成果证据 + 经理评价（`manager_level`/`manager_comment`）」→ HR 审核（`approve` + 可选 `final_level`）生成版本化正式评级。**C14 收口**：创建/审核经 A06 负责范围校验（经理仅本部门员工、HR 按数据范围），越权返回 403；`GET /capability-ratings` 与 `GET /capability-rating-changes` 对员工返回空（内部正式评级不对员工可见，与 F10 内部评级不可见规则对齐），管理侧/负责经理按范围可见。）

AI 面试间 `/api/ai-interviews`：

- `POST /sessions`、`GET /sessions`、`GET /sessions/{id}`（管理员创建/查看面试会话）
- `POST /sessions/{id}/start`、`POST /sessions/{id}/finish`（候选人启动/结束；结束时计算 `overall_score` 为已评分题目均分）
- `POST /sessions/{id}/questions`、`GET /sessions/{id}/questions`（管理员添加题目，含题干与期望要点；按 `sequence_no` 排序）
- `POST /questions/{id}/answer`（候选人作答；真实模型评分，失败标记 `failed` 可重试）、`POST /questions/{id}/retry-judge`（重评）

盘点 `/api/inventory`（F10 九宫格/人才池）：

- `POST /reviews`、`GET /reviews`、`GET /reviews/{id}`、`PATCH /reviews/{id}`、`POST /reviews/{id}/submit`、`POST /reviews/{id}/confirm`（管理侧或负责部门经理创建盘点；绩效+HR 潜力计算 `grid_cell`；提交需绩效/经理本人初评/HR 潜力齐全，确认仅对已提交复核生效并强制完整状态链、记确认人 `reviewer_hr_id`，已确认不可重复确认或修改。**C09 收口**：`POST /reviews` 与 `GET /reviews` 按 A06 数据范围授权（管理侧按 scope、经理仅本部门、员工仅本人），部门经理不再被统一管理门禁拒绝；提交/确认强制 `reviewer_manager_id` 非空，即经理分须来自经理本人初评入口，管理侧代填不计。）
- `POST /reviews/{id}/manager-assessment`（C09 经理本人潜力初评：仅 `MANAGER` 角色、仅负责部门员工、仅草稿阶段；body `potential_manager_score`（1–5）+ 可选 `comment`。`reviewer_manager_id` 强制记为初评经理本人，写 `manager_comment` 与 `manager_initial_reviewed` 事件；已提交/确认拒绝修改。）
- `POST /pools`、`GET /pools`、`POST /pools/{id}/decide`（人才池提名/审批：nominated/approved/rejected/removed）
- `POST /reviews/{id}/disputes`、`POST /disputes/{id}/respond`（员工异议、HR 回复）
- `PUT /reviews/{id}/idp`（个人发展计划，review 唯一）
- `GET /summary`（管理员部门匿名汇总；单格 <5 条隐藏到 `_under_threshold`）
- `GET /summary/export`（管理员导出匿名汇总 CSV，按 `cycle_name` 可选过滤；写 `summary_exported` 审计事件，文件不含个人身份）
- 可见性：员工本人查看不返回 `grid_cell` 与内部评分；非管理员查看汇总 403。

## 5. 后端执行与依赖边界

- 模型调用复用 `app.core.llm_client.chat_json`，C 自带 system prompt 与 Pydantic schema，不修改 D 的 `core/prompts` 注册表；提示版本写入结果/草稿快照。
- 本期不新增 TaskRunner kind（其合法类型由 D 维护）。交卷评分同步执行，失败项通过 `retry-grading` 重试；D 接入统一任务框架后可将同一服务方法作为 handler，C 不复制调度器。
- 表注册与路由注册需 A 集成：`backend/app/models/__init__.py` 导入 C 模型（或 init_db 显式导入）、`backend/app/main.py` 引入并 `include_router`。C 提供 `backend/app/modules/assessments/registry.py` 供 A 一次性引用。
- 本地测试使用 SQLite 持久化 + 注入式模型替身；MySQL、真实模型、真实浏览器联调未执行前不标验收通过。

## 6. 一期范围（2026-11-07 用户裁决：原"一期不做"清单全部纳入一期）

固定试卷保留为兼容模式，不作为自适应替代；在线运行时不自动生成并直接使用未审核题目（生成题一律 `pending` 待人工审核）。其余原"不做"项全部纳入一期实施：

- AI 面试间：在线考试与结构化问答组织记录、AI 评判；音视频/情绪推断涉及个人信息的须先完成告知授权与隐私评审后启用（隐私评审前不上线音视频）。
- 防挂机/有效学习证据：学习进度须由服务端可校验的学习证据支撑，自报与正式完成分离。具体规则：`study_session` 凭证由服务端 HMAC 签名并绑定学习记录/员工，凭证有效期配置化（默认 120 分钟）、同一 nonce 只能兑换一次；**C08 收口**：有效学时由员工周期性心跳上报、服务端按相邻心跳间隔 ≤（空闲窗口 + 容差）累计（`training_study_sessions.active_minutes`，单区间 ≤ 上限），需 ≥ 最少心跳次数且有效学习 ≥ 最短时长方可结算（参数均配置化，默认 90s 窗口/10s 容差/90s 上限/3 次/5 分钟），无交互的挂机等待不计学时（不再按签发后经过时间折算）；乱序拒绝、重复幂等、行锁 + 条件更新防多端双计；前端仅页面可见且近期有真实学习交互时发送心跳，隐藏/空闲暂停明示。心跳/浏览器活动仅为有限学习证据，不能独自证明知识掌握；正式完成按课程 `completion_rules_json`（最低证据分 + 必备证据类型）判定，缺省用全局阈值。出勤与已通过测评各按其真实记录键去重。
- 课程审批/候补/名额占位/24h 超时：员工可自主报名必修/选修课，审批占位、候补队列、24h 未确认超时释放。
- 九宫格/人才池（F10）：绩效（最近 HR 确认周期）+ 潜力（经理初评、HR 审核）→ 九宫格；人才池进出审批、年度/提前复核、员工异议、个人发展计划、部门匿名汇总 <5 隐藏、导出审计。
- 有效学时证明：按服务端可信证据认定正式完成，无证据不认定；一期固定阈值——出勤或已通过员工测评计 100 分并达标，`study_session` 按累计时长/45 分钟折算，正式完成需可信证据最高分 ≥60 分。课程级完成条件配置未落库前使用该统一常量，后续接入按课程取值。
- 面试总分与自动招聘推进仍不做（人工面试不计总分、不自动推进招聘状态）；自动人事决策不做；候选人培训不做（候选人入职后由 A 改员工角色后再分配）。

## 7. 培训评价规则（D-34 / REQ-TRN-001 AC-6，2026-11-06 纳入一期）

1. 资格：仅员工本人；必须在对应课程期次有出勤登记。反馈窗口为该期次 `ends_at` 起 7 天（含截止时刻）；课程未结束或窗口关闭时拒绝提交/修改/撤回。
2. 去重：同一员工对同一课程跨期最多一份反馈；再次提交更新同一行并切换到最近一次有效出勤期次，汇总始终按一人一份计数。
3. 内容：课程满意度、讲师满意度各 1–5 分，可选文字意见（≤1000 字）；撤回在窗口内生效，撤回后不计入汇总，窗口关闭后本人仍可只读查看。
4. 匿名汇总：仅管理员（`admin/hr`）可查看；有效反馈少于 5 份时只返回份数，不返回均分与主题；达到 5 份才返回课程/讲师均分。接口永不返回文字原文或员工身份。
5. 主题归纳：管理员触发真实模型（`c-training-feedback-theme-v1`）对匿名文字归纳标签，只保存并展示支持份数 ≥5 的主题；模型失败返回 503 且不写入主题。
6. 改进说明：管理员依据汇总发布统一 `feedback_note`，不改课程版本；员工在本人评价状态中可见。
7. 角色边界：一期无讲师/部门经理角色，匿名汇总暂由管理员持有；后续开放需 A 提供角色与授权映射，C 不放宽现有权限。

## 8. 本轮实现与验证状态（2026-11-09 更新）

- 已实现：`backend/app/modules/assessments`（模型/自适应策略与选题/评分/服务/路由/registry/模型出题）、`interviews`（人工面试 + AI 面试间）、`training`（课程/计划/评价 + 报名审批/候补/名额上限/超时释放 + 学习证据 + 到期/逾期/延期）、`inventory`（F10 九宫格/人才池/异议/IDP/匿名汇总/导出审计）四域与 32 张表；前端 `frontend/src/app/(main)/{assessments,interviews,target,training,inventory}` 页面、类型与接口封装，含自适应逐题答题、能力雷达、员工评价、管理员期次/反馈汇总、`/inventory`（盘点/人才池/汇总/异议/IDP）、`/interviews/ai`（会话/题目/逐题作答）、`/training/enrollments`（报名审批/候补/超时释放/学习证据）、培训计划到期日录入与管理员延期、课程名额维护与盘点 CSV 导出入口。
- C09/C10/C13 缺口关闭（2026-11-11）：**C09** `inventory/service.py` 确认状态链——`submit_review` 需绩效/经理初评/HR 潜力齐全，`confirm_review` 仅对 `submitted` 生效、强制完整状态链、重算九宫格、记 `reviewer_hr_id`，已确认不可重复确认或修改（前端 UI 早已按 draft→提交、submitted→确认  gating，本轮使后端与之一致）。**C10** `_publish_adaptive` 把题目全文/答案/评分版本冻结进 `bank_snapshot_json.questions`，`next_question` 出题与评分读冻结快照（仍按当前状态排除停用题；仅有 id 的历史方案回退当前内容），发布后改题重审不影响旧方案。**C13** `confirm_formal_completion` 在 `formal_completed` 0→1 时追加 `course_formally_completed` 成长事件（`self_reported=false`，引用计划版本/可信证据分与验证人），重复确认不重复追加。九宫格 4/3 档位映射仍为一期 agreed 标准规则；F10 设计稿的「阈值配置版本/未批准不产出正式评级」超出本期简化模型，未在本轮超建（见交接剩余范围）。已验证：`tests/C` 全量 **71 项通过**（新增 C09 五例、C10 两例、C13 一例；SQLite + 注入式替身），前端 `pnpm run typecheck` exit 0；本轮仅改后端，未重跑 `build`，真实 MySQL/模型/浏览器联调仍未验证。
- 学习证据可信化（2026-11-09，C08）：`submit_evidence` 不再接收客户端 `score`/`verified_by`（DTO 严格字段，直传返回 422），三类证据的分数与验证人由服务端计算：`attendance` 读本人该课程真实出勤（100 分）、`assessment` 读本人已通过员工测评（100 分，招聘测评不计）、`study_session` 凭服务端 HMAC 签名的一次性会话券兑换并按服务端时间差计有效分钟数（累计/45 分钟折算）。`evidence_key`（出勤 id/测评结果版本/会话 nonce）同记录唯一，重复提交或凭证重放返回 400；`confirm_formal_completion` 只认 `system.*` 验证人的证据且最高分须 ≥60。迁移 `C_0007_learning_evidence_key` 新增该列与唯一约束。
- C06/C08/C09/C13/C14 深度收口（2026-11-11，MASTER 集成，迁移 `C_0011_enrollment_chain_study_session`，单头）：本条取代上一条 2026-11-11 笔记中「阈值配置版本/未批准不产出正式评级未超建」的表述——`confirm_review` 现已强制经 HR 批准的九宫格配置版本（`_active_grid_config`，无批准配置拒绝产出正式评级）。**C06**：课程 `approval_mode`（none/manager/admin）+ `has_cost`/`occupies_work_time` 触发管理员复核；新增 `manager-decide`（经理本人、本部门、manager 模式、pending；涉费/工时转 `pending_admin_review`）与 `admin-review`（仅 pending_admin_review）端点；管理员不可越过经理环节；候补 `awaiting_confirmation` 须员工本人确认，经理/管理员不可代确认；占位计数含 pending_admin_review；`GET /enrollments` 按角色范围（管理侧全部/经理本部门/员工本人）。**C08**：新增 `training_study_sessions` 与 `study-heartbeat` 端点，有效学时按相邻心跳间隔 ≤90 秒累计（`active_minutes`），需 ≥3 次心跳且 ≥5 分钟，挂机不计；`confirm_formal_completion` 按课程 `completion_rules_json`（`min_evidence_score` + `required_evidence_types`）判定。**C09**：新增 `manager-assessment` 端点（仅 MANAGER、本部门、草稿；`reviewer_manager_id` 强制记初评经理本人 + `manager_comment`），`submit`/`confirm` 强制 `reviewer_manager_id` 非空（管理侧代填不计）；`create`/`list` 经 A06 范围授权，部门经理不再被统一管理门禁拒绝。**C13**：同维度多次测评按 `(fact_at, version, id)` 倒序取最新事实，`assessed` 来源带 `ref_id`/`assignment_id`/`assessed_at`；员工视角过滤 `hr_ability` 域与内部正式评级维度（F10 隔离）。**C14**：评级变更创建/审核经负责范围校验（经理本部门、HR 按 scope，越权 403）；员工 `capability-ratings`/`capability-rating-changes` 列表返回空（内部评级不可见）。`training/authz.py` 提供 C 域 tier/scope 复用 A06 `resolve_data_scope`/`get_visible_department_ids`；`TrainingStudySession` 已注册 `registry_models.ALL_MODELS`。已验证：`tests/C` 全量 **127 项通过**（SQLite 内存库 + X-Test-User 替身身份；新增报名审批链 14 例、心跳防挂机/按课程完成条件、经理初评与越权反例、轨迹最新事实/F10 隔离、评级范围隔离），前端 `tsc --noEmit` exit 0、`vitest` **131 项通过（29 文件）**。未验证：真实 MySQL 实跑 `C_0011`、真实 JWT/账号权限、真实浏览器页面验收、通知真实投递、并发容量与每日定时——均不得表述为验收通过。前端 `/training/enrollments`（经理审批/管理员复核/学习心跳）与 `/inventory`（经理初评 tab）已按既有 `isManager` 模式接入，仅类型/单测验证，未实机浏览器验证。
- 时间列：C 域 32 张表统一使用公共 `CrossDbUpdatedAtMixin`（2026-11-09 收敛，删除自建 `CTimestampMixin`；列结构与 DDL 等价，无需迁移）。
- application_id 契约（2026-11-07 对接 A 的 M3）：`interview_rounds.application_id` 与 `ai_interview_sessions.application_id` 已对齐 A 的 `applications.id`（`PK_TYPE`，MySQL 下 BIGINT UNSIGNED）并加数据库级外键（RESTRICT，申请不物理删除），迁移 `C_0004_application_fk`。语义：`applications` 为同人多岗位独立申请，C 侧仅引用其主键，不复制其状态机。**2026-11-09 改挂**：`C_0004` 的 `down_revision` 由 `A_0007_scope_grants` 改为 `B_0002_merge_workflow_review`（master 合入 A_0008/B_0001/B_0002/D_0001~D_0009 后原挂点会造成双 head；迁移从未在任何库执行，改挂安全）。新增 `C_0005_plan_item_due_date`（`training_plan_items.due_date DATE NULL`）与 `C_0006_course_capacity`（`training_courses.capacity INT NULL`，报名名额上限）与 `C_0007_learning_evidence_key`（`training_learning_evidence.evidence_key VARCHAR(100) NOT NULL` + `uk_evidence_record_key`，历史行回填 `legacy:<id>`），当前单头 `C_0007_learning_evidence_key`。
- 通知接入（2026-11-09，只读调用 D 通知中心）：`course.assigned`（计划指派）、`course.enrollment_reviewed`（报名审批）、`course.waitlist_promoted`（候补递补，课程存在未结束期次时以该期次为 ref，否则跳过）、`training.due_reminder` / `training.overdue`（到期/逾期扫描）、`training.extension_decided`（延期）六类事件按 D 契约 ref 与幂等键投递。
- 员工测评完成事件（2026-11-09，响应 `REQ-D-20261109-103009`；同日按用户决定改为采用 D 已发布的桥接）：`purpose=employee` 的测评在 `submit_assignment`、`retry_grading` 首次进入 `completed` 时，调用 D 的 `run_service.start_employee_assessment_run(db, assignment_id=..., result_id=..., actor=当前登录人)`；事件码 `employee_assessment_completed`、事件 ID `{assignment_id}:v{result_version}` 由 D 侧生成并按其 `WorkflowDefinition.code` 解析 published 版本；招聘测评不触发；重复交卷/补评不重复触发（D 侧 UUIDv5 thread_id 幂等）；触发失败（含流程未发布返回 `None`）不回滚已提交的测评结果，返回值仅新增内部字段 `workflow_event`（不进入 HTTP 响应体）。触发人校验为“测评本人或该测评分配人”，不使用全权管理员账号代发。
- 已验证：`$env:PYTHONPATH="backend"; python -m unittest discover -s tests/C -p "test_*.py"` 运行 **63 项全部通过**（SQLite 内存库 + FastAPI TestClient + 注入式评分/出题/草稿/主题/评委替身；本轮新增 C08 四例：客户端直传 score/verified_by 与非法证据类型 422、出勤证据需真实出勤且重复计入 400、学习会话凭证短时长/伪造/重放被拒且未达阈值不能完成、他人不可提交本人证据）。
- 已验证：`npm run typecheck` 与 `npm run build`（frontend）均通过；构建路由清单含 `/inventory`、`/interviews/ai`、`/training/enrollments`；application_id 外键的 MySQL DDL 编译验证（`BIGINT UNSIGNED` + `REFERENCES applications(id)`，类型与 A 的 PK_TYPE 匹配）；本轮 `training_learning_evidence` 表的 MySQL DDL 编译验证含 `uk_evidence_record_key` 唯一约束。
- 已验证（MySQL 集成环境，2026-11-09 补测）：本机 MySQL 以 Docker 容器 `aitalent-yb-env-mysql` 提供（`127.0.0.1:3307` → `3306`，库 `ai_talent_yb`），启动后在空库执行 `alembic upgrade` 已通过 `C_0001`~`C_0002`、A 链与 `D_0001_notifications`（`C_0001` 使用临时本地补丁，见下条；未提交、文件已还原）。
- 已发现/待 A 修复（`REQ-C-20261109-121506`）：`C_0001` 用 `create_all` 读取当前模型元数据，会把 `application_id` 外键提前带入 `interview_rounds`/`ai_interview_sessions`，而 `applications` 由 A_0005 创建，导致**从空库**升级在 `C_0001` 报 `1824`。该文件按 `ownership.json` 归 A，C 已提交补丁请求 A 处理；本地用该补丁验证后续链可跑到 `D_0001`。
- 未验证（进行中）：全链从空库继续执行在 `D_0002_workflow_checkpoint` 处阻塞——该迁移导入 `langgraph`（requirements 已声明，本机 Python 3.10 与 `backend/.venv` 均未安装，pip 因网络 SSL 失败无法安装），因此 `C_0004`~`C_0007` 的 MySQL 实跑仍未完成；`C_0007` 目前仅完成 MySQL 方言 DDL 编译。真实 LLM 评分/出题/主题归纳/AI 面试评分、真实 JWT 与账号权限、真实浏览器页面、A 的 `AppShell` 菜单集成（新增三页仍缺入口）、通知的真实投递与每日定时触发均未验证；前端 `/training/enrollments` 已去掉自报分数输入并接入 `study-sessions` 领券与结算，但未实机浏览器验证。上述未验证项不得表述为验收通过。
- 接入方式：`main.py` 已循环 `from app.modules.assessments.registry import ALL_ROUTERS` 注册全部 C 路由；`app/models/__init__.py` 已由 A 整合为 75 表（含 C 全部 32 表）；新增前端菜单入口待 A 在 `AppShell` 添加，见 `docs/requests/C/20261107-A-一期扩表集成与申请契约.md` 第 6 项。
