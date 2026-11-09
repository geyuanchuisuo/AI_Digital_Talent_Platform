# Skills 组件库契约（T-D19）

版本：v1.0（2026-11-09）。需求依据：PRD §2.7、统一需求 D-35、原 PRD 执行跟踪表 D11。
前置：T-D14 流程数据模型（版本生命周期与四眼审核同构复用）。

## 1. 表与职责

| 表 | 职责 | 约束 |
|---|---|---|
| `workflow_skills` | 组件定义：code/name/description + 工具绑定 + 输出解析器 | `uk_workflow_skills_code`(code) |
| `workflow_skill_versions` | 组件版本：prompt_template + params_json_schema 快照 + 受控生命周期 | `uk_workflow_skill_version`(skill_id, version)；`idx_wsv_skill_status` |

迁移：`D_0010_workflow_skills` 挂 `C_0006_course_capacity`（**77 表基线** = 75 + 2 skills）。

## 2. 组件 schema 四件套（需求 D-35）

| 件 | 落点 | 校验 |
|---|---|---|
| 提示词模板 | `workflow_skill_versions.prompt_template`（≤8000 字符） | 必填；`{{param}}` 占位由执行面渲染，组件库只存原文 |
| 参数 JSON Schema | `workflow_skill_versions.params_json_schema` | 受控子集：type=object + properties（逐参数含 type）+ required（不重复、必须已声明）+ additionalProperties；未知键 422 |
| 工具绑定 | `workflow_skills.tool` | 白名单：`none` / `analytics_query` / `workflow_run_query` / `profile_query`；白名单外 422 |
| 输出解析器 | `workflow_skills.output_parser` | 白名单：`raw_text` / `json_object` / `markdown_sections`；白名单外 422 |

工具执行面归各域授权服务（组件库只登记声明，不越域执行）；`profile_query` 声明 B 域能力，实际接入由 B 契约确认。

## 3. 生命周期（与流程定义版本同构）

`draft →(submit)→ pending_review →(审核通过)→ published →(停用)→ deprecated`；驳回退回 draft（留 review_note）。

- 创建/修改/提交/停用：管理侧（can_view_all_persons，403）；
- 审核：管理侧 + **四眼原则**——提交人不可自行审核（403）；
- 发布版本内容不可变（版本锁定）：`update_draft_skill_version` 非 draft 409。

## 4. 编排引用与审计

- 引用解析 `resolve_published_skill(skill_version_id)`：仅 `published` 可被新引用（draft/pending_review/deprecated 422——验收①引用面）；返回四件套快照。
- 引用方持有快照（不持状态机）：组件停用/新版本发布/回退不影响已引用实例（验收②）；已引用实例钉死 version_id，快照内容与状态流转解耦。
- 节点引用方式：节点 `config.skill_version_id` 落 `workflow_nodes.config_json`（落库可回查）；组件执行经 run_service 节点包装进 `workflow_run_steps` 运行审计（验收③）。真实 LLM 渲染执行待 Key（DEC-20261107-AGNES-VERIFY 同族），一期交付组件库与引用/审计通路。

## 5. API（`/api/workflows/skills`，管理侧门禁）

| 端点 | 说明 |
|---|---|
| GET `/meta` | 工具/解析器白名单元数据 |
| GET `` / POST `` | 组件列表 / 新建（code 唯一 409、白名单 422） |
| GET/POST `/{skill_id}/versions` | 版本列表（倒序）/ 新建草稿（版本号 max+1） |
| PUT `/versions/{version_id}` | 替换草稿内容（发布版本 409） |
| POST `/versions/{version_id}/submit` | 提交审核 |
| POST `/versions/{version_id}/review` | 审核（approve bool；四眼 403） |
| POST `/versions/{version_id}/deactivate` | 停用发布版本 |
| GET `/versions/{version_id}/resolve` | 编排引用解析（四件套快照） |

## 6. 验证与边界

- 测试：`tests/D/test_workflow_skills_t19.py` 10 例（白名单/403、schema 校验三反例、draft/pending 引用 422、四眼 403、发布解析与不可变、停用后新引用拒绝+快照不变、v2 发布不影响 v1 引用、节点 config 引用落库+步骤审计、停用后运行快照语义）。
- 表基线断言同步：`app/models/__init__.py` 75→77、`backend/tests/test_models_structure.py`（EXPECTED_COLUMNS/UNIQUE/INDEXES）。
- **未含**：真实 LLM 渲染执行（待 Key）、组件市场化（PRD §8 后置）、前端组件库页（影响结构内，随 T-D20 中台管理面统一交付）、`models_structure` 中 `training_courses`/`training_plan_items` 列预期与 `test_no_physical_foreign_keys` 三处失败为 C_0006 迁移后预期未同步的预存在问题（干净树同样失败，归属 C，本片未触碰）。
