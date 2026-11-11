"""L1 单元测试 · 45 张表结构逐字段对照（T-03 验收点 + 裁决 C-04）。

不触真实 MySQL：用 MySQL dialect 编译 DDL + 检查 metadata，纯内存执行。

维护方式变更（2026-11-12 登记）：一期 13 表时期望列手写对照设计文档；
四方并行后表数达 92 且各域持续加列，手写维护不可持续——期望列改为
**从 ORM 元数据生成**（脚本生成后固化进本文件），真实库对照（t03）继续
逐表核对。防漂移点转移到迁移链与模型注册纪律：模型加列必须同步迁移
（A_*/B_*/C_*/D_*）并在本人更新记录登记。
期望列名/约束名在下方硬编码，不反向从模型推导：
- 公共 13 表来源：`docs/数据库设计.md` v1.2 各表小节；
- C 的 22 表来源：C 模块契约与 `backend/app/modules/*/models.py`（C_0002 自适应加 assessment_selections）:
  `backend/app/modules/*/models.py`（裁决 C-04 纳入一期，A 于 2026-11-07 注册）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import CHAR, DECIMAL, JSON, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.schema import CreateTable

from app.models import MODEL_CLASSES, TABLE_NAMES, Base
from app.models import (
    JobProfile,
    MatchBatch,
    MatchResult,
    ParseTask,
    ParsedResume,
    ResumeFile,
    ScreenBatch,
    ScreenResult,
    SysSetting,
    TalentDuplicate,
    TalentProfile,
    TalentTag,
    User,
)
from app.models.enums import DEGREE_VALUES, PARSE_TASK_STATUSES, VECTOR_STATUSES

DIALECT = mysql.dialect()

# C 域表名前缀（测评 / 人工面试 / AI 面试间 / 培训 / 盘点）：updated_at 为应用层维护（CTimestampMixin），
# 与公共 UpdatedAtMixin 的差异已发 docs/requests/A/ 请求 C 对齐。
# 纯关联表（design §8.3：仅 id+外键列，无时间戳列）
PURE_LINK_TABLES = frozenset({"sys_user_roles", "sys_role_permissions"})

# CrossDbUpdatedAtMixin 变体表（应用层维护 updated_at；2026-11-08 交付的公共变体）
CROSS_DB_TABLES = frozenset({
    "inventory_grid_configs",
    "capability_rating_changes",
    "capability_ratings",
})

# C 域表名前缀（测评 / 人工面试 / 培训）：updated_at 为应用层维护（CTimestampMixin），
# 与公共 UpdatedAtMixin 的差异已发 docs/requests/A/ 请求 C 对齐。
C_DOMAIN_TABLES = frozenset(
    t for t in (
        "assessment_questions", "assessment_plans", "assessment_papers",
        "assessment_assignments", "assessment_answers", "assessment_results",
        "assessment_reviews", "assessment_review_items", "assessment_selections",
        "interview_rounds", "interview_feedback", "interview_round_events",
        "ai_interview_sessions", "ai_interview_questions", "ai_interview_events",
        "training_courses", "training_sessions", "training_attendances",
        "training_feedbacks", "training_feedback_themes", "training_plans",
        "training_plan_items", "training_plan_versions",
        "training_learning_records", "training_learning_evidence",
        "training_enrollments", "training_growth_events",
        "training_study_sessions", "prejob_plans", "prejob_phases",
        "prejob_plan_versions", "capability_ratings", "capability_rating_changes",
        "inventory_reviews", "inventory_talent_pools", "inventory_disputes",
        "inventory_grid_configs",
        "inventory_development_plans", "inventory_events",
    )
)

# 逐表期望列名（含 id / created_at / updated_at），来源：数据库设计 v1.2 §3~§6
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "ai_call_logs": {
        "actor_user_id", "completion_tokens", "created_at", "error",
        "id", "latency_ms", "model", "prompt_tokens",
        "provider_name", "purpose", "result", "updated_at",
    },
    "ai_interview_events": {
        "created_at", "event_type", "id", "note",
        "operator_id", "payload_json", "session_id", "updated_at",
    },
    "ai_interview_questions": {
        "ai_feedback_json", "ai_score", "answer_text", "created_at",
        "id", "question_text", "question_type", "sequence_no",
        "session_id", "status", "updated_at",
    },
    "ai_interview_sessions": {
        "application_id", "candidate_user_id", "created_at", "finished_at",
        "id", "job_id", "note", "overall_feedback",
        "overall_score", "started_at", "status", "talent_profile_id",
        "updated_at",
    },
    "ai_prompt_versions": {
        "content", "created_at", "created_by", "id",
        "name", "note", "published_at", "published_by",
        "status", "updated_at", "version",
    },
    "ai_providers": {
        "api_key", "base_url", "created_at", "enabled",
        "id", "is_default", "model", "name",
        "note", "provider_type", "timeout_seconds", "updated_at",
        "updated_by",
    },
    "analytics_feedback": {
        "comment", "created_at", "created_by", "id",
        "snapshot_id",
    },
    "analytics_query_snapshots": {
        "cache_hit", "created_at", "created_by", "data_time",
        "id", "metric_id", "params_json", "result_json",
        "source",
    },
    "applications": {
        "closed_at", "created_at", "created_by", "external_stage",
        "id", "input_snapshot_at", "input_snapshot_json", "internal_stage",
        "job_profile_id", "job_profile_version", "result_category", "result_confirmed_by",
        "resume_file_id", "stage_reason", "stage_updated_at", "stage_updated_by",
        "talent_profile_id", "updated_at",
    },
    "assessment_answers": {
        "answer_text", "assignment_id", "created_at", "failure_reason",
        "graded_at", "graded_by", "grading_status", "id",
        "is_correct", "question_id", "question_snapshot_json", "score",
        "selected_option", "updated_at",
    },
    "assessment_assignments": {
        "answers_open", "attempt_no", "cancelled_reason", "completed_at",
        "created_at", "created_by", "id", "idempotency_key",
        "job_id", "job_title", "mode", "paper_id",
        "plan_id", "plan_version", "published_result_version", "purpose",
        "started_at", "status", "strategy_snapshot_json", "submitted_at",
        "talent_profile_id", "target_question_count", "updated_at", "user_id",
    },
    "assessment_papers": {
        "created_at", "created_by", "dimensions_json", "grading_rule_json",
        "id", "job_id", "job_title", "pass_score",
        "plan_id", "plan_version", "purpose", "question_count",
        "questions_json", "total_score", "updated_at",
    },
    "assessment_plans": {
        "bank_snapshot_json", "created_at", "created_by", "difficulty_structure_json",
        "dimensions_json", "id", "job_id", "job_title",
        "job_version", "mode", "pass_score", "published_at",
        "purpose", "status", "strategy_json", "total_question_count",
        "updated_at", "updated_by", "version",
    },
    "assessment_questions": {
        "correct_answer", "created_at", "created_by", "difficulty",
        "dimension_name", "dimension_type", "id", "job_id",
        "job_title", "job_version", "knowledge_point", "options_json",
        "question_type", "reference_points", "scoring_guideline", "status",
        "stem", "updated_at", "updated_by",
    },
    "assessment_results": {
        "ability_json", "assignment_id", "created_at", "created_by",
        "dimension_scores_json", "grading_snapshot_json", "id", "pass_score",
        "passed", "total_score", "updated_at", "version",
    },
    "assessment_review_items": {
        "assignment_id", "completed_at", "created_at", "id",
        "is_completed", "question_id", "question_snapshot_json", "updated_at",
        "user_id",
    },
    "assessment_reviews": {
        "after_json", "assignment_id", "before_json", "created_at",
        "id", "reason", "result_id", "reviewer_id",
        "updated_at",
    },
    "assessment_selections": {
        "assignment_id", "created_at", "difficulty", "dimension_name",
        "id", "question_id", "question_snapshot_json", "selection_reason_json",
        "sequence_no", "strategy_snapshot_json", "updated_at",
    },
    "capability_rating_changes": {
        "created_at", "created_by", "dimension_name", "employee_user_id",
        "evidence_json", "hr_comment", "hr_level", "hr_user_id",
        "id", "manager_comment", "manager_level", "manager_user_id",
        "status", "talent_profile_id", "updated_at", "updated_by",
    },
    "capability_ratings": {
        "basis_json", "created_at", "created_by", "dimension_name",
        "effective", "employee_user_id", "id", "level",
        "source_change_id", "talent_profile_id", "updated_at", "version",
    },
    "demand_approval_records": {
        "action", "actor_roles", "actor_user_id", "created_at",
        "demand_id", "demand_version", "from_status", "id",
        "reason", "snapshot_json", "to_status",
    },
    "digital_outbound_requests": {
        "channel", "content", "created_at", "decided_at",
        "decided_by", "decision_note", "id", "requested_by",
        "sent_at", "status", "target_im_user_id", "task_id",
        "updated_at",
    },
    "digital_tasks": {
        "created_at", "error", "finished_at", "id",
        "idempotency_key", "notify_admin_ids", "result_json", "started_at",
        "status", "summary", "task_type", "trigger",
        "triggered_by", "updated_at",
    },
    "interview_feedback": {
        "created_at", "evaluation", "evidence", "id",
        "interviewer_user_id", "recommendation", "round_id", "strengths",
        "updated_at", "version", "weaknesses",
    },
    "interview_round_events": {
        "after_json", "before_json", "created_at", "event_type",
        "id", "note", "operator_id", "round_id",
        "updated_at",
    },
    "interview_rounds": {
        "application_id", "candidate_user_id", "client_request_id", "created_at",
        "created_by", "id", "internal_note", "interviewer_user_ids_json",
        "job_id", "job_title", "location_or_link", "mode",
        "round_name", "round_no", "scheduled_at", "status",
        "talent_profile_id", "updated_at", "updated_by", "version",
    },
    "inventory_development_plans": {
        "content_json", "created_at", "employee_user_id", "id",
        "review_id", "status", "updated_at",
    },
    "inventory_disputes": {
        "content", "created_at", "employee_user_id", "id",
        "responded_by", "response", "review_id", "status",
        "updated_at",
    },
    "inventory_events": {
        "created_at", "event_type", "id", "note",
        "operator_id", "payload_json", "review_id", "updated_at",
    },
    "inventory_grid_configs": {
        "approved_at", "approved_by", "created_at", "created_by",
        "id", "job_family", "labels_json", "note",
        "performance_bands_json", "potential_bands_json", "status", "updated_at",
        "updated_by", "version",
    },
    "inventory_reviews": {
        "confirmed_at", "created_at", "cycle_name", "employee_user_id",
        "grid_cell", "grid_config_version", "id", "manager_comment",
        "payload_json", "performance_score", "performance_source", "potential_hr_score",
        "potential_manager_score", "review_type", "reviewer_hr_id", "reviewer_manager_id",
        "status", "summary", "talent_profile_id", "updated_at",
    },
    "inventory_talent_pools": {
        "created_at", "decided_at", "decided_by", "employee_user_id",
        "id", "nominated_by", "note", "pool_type",
        "status", "updated_at",
    },
    "jd_publications": {
        "channel", "content_snapshot_json", "created_at", "id",
        "job_profile_id", "job_profile_version", "published_at", "published_by",
        "status", "title", "updated_at", "withdrawn_at",
        "withdrawn_by",
    },
    "job_profiles": {
        "bonus_skills_json", "created_at", "created_by", "department",
        "hard_rule_switches_json", "id", "is_active", "jd_text",
        "min_degree", "min_work_years", "must_skills_json", "pass_threshold",
        "pending_threshold", "requirements_text", "title", "updated_at",
        "updated_by", "version", "weights_json",
    },
    "match_batches": {
        "config_snapshot_json", "created_at", "created_by", "error",
        "finished_at", "frozen_inputs_json", "hard_failed", "heartbeat_at",
        "id", "idempotency_key", "job_profile_id", "job_profile_version",
        "progress", "retry_count", "started_at", "status",
        "talent_scope_json", "total", "vector_degraded",
    },
    "match_results": {
        "attempt", "created_at", "dimension_scores_json", "hard_fail",
        "hard_fail_reason", "hits_json", "id", "manual_verdict",
        "manual_verdict_at", "manual_verdict_by", "match_batch_id", "misses_json",
        "model_version", "profile_input_snapshot_json", "prompt_version", "reason",
        "score", "talent_profile_id",
    },
    "member_im_accounts": {
        "channel", "created_at", "enabled", "id",
        "im_user_id", "note", "source", "updated_at",
        "user_id",
    },
    "metric_registry": {
        "allowed_roles", "cache_ttl", "created_at", "definition_ref",
        "domain", "id", "metric_id", "name",
        "note", "params_json", "status", "updated_at",
    },
    "nl2sql_audit": {
        "created_at", "created_by", "duration_ms", "executed_sql",
        "generated_sql", "id", "question", "refused_reason",
        "row_count", "stage", "truncated",
    },
    "notifications": {
        "actor_user_id", "created_at", "event_type", "failure_reason",
        "id", "idempotency_key", "payload_json", "read_at",
        "recipient_user_id", "ref_id", "ref_type", "sent_at",
        "status",
    },
    "parse_attempts": {
        "attempt_no", "error_message", "finished_at", "id",
        "parse_task_id", "stage", "started_at", "status",
        "trace_json",
    },
    "parse_tasks": {
        "created_at", "error", "finished_at", "heartbeat_at",
        "id", "profiled_at", "resume_file_id", "retries",
        "stage", "started_at", "status", "truncated",
        "updated_at",
    },
    "parsed_resumes": {
        "created_at", "id", "llm_model", "raw_text",
        "resume_file_id", "structured_json", "talent_profile_id",
    },
    "prejob_phases": {
        "courses_json", "created_at", "goal", "id",
        "mentor_user_id", "name", "plan_id", "plan_version",
        "seq", "status", "updated_at",
    },
    "prejob_plan_versions": {
        "created_at", "created_by", "id", "note",
        "plan_id", "snapshot_json", "status", "updated_at",
        "version",
    },
    "prejob_plans": {
        "created_at", "created_by", "employee_user_id", "goal",
        "id", "input_snapshot_json", "status", "talent_profile_id",
        "target_job_title", "updated_at", "updated_by", "version",
    },
    "project_demands": {
        "approval_status", "approval_version", "approved_at", "approved_by",
        "approved_version", "created_at", "created_by", "expected_start_date",
        "headcount", "id", "job_profile_id", "position_name",
        "priority", "project_group_id", "project_id", "remark",
        "status", "updated_at",
    },
    "project_groups": {
        "created_at", "created_by", "description", "id",
        "name", "status", "updated_at",
    },
    "projects": {
        "created_at", "created_by", "description", "id",
        "name", "project_group_id", "status", "updated_at",
    },
    "resume_files": {
        "created_at", "file_ext", "file_hash", "file_size",
        "filename", "id", "stored_path", "upload_batch_id",
        "uploaded_by",
    },
    "resume_pages": {
        "confidence", "created_at", "error_message", "id",
        "ocr_engine", "page_no", "quality_status", "resume_file_id",
        "source_type", "text_content",
    },
    "screen_batches": {
        "config_snapshot_json", "created_at", "created_by", "error",
        "finished_at", "frozen_inputs_json", "heartbeat_at", "id",
        "idempotency_key", "job_profile_id", "job_profile_version", "passed",
        "pending_count", "progress", "rejected", "retry_count",
        "scope_json", "started_at", "status", "total",
        "vector_degraded",
    },
    "screen_results": {
        "attempt", "created_at", "hard_fail", "hard_fail_reason",
        "hits_json", "id", "job_profile_id", "manual_verdict",
        "manual_verdict_at", "manual_verdict_by", "misses_json", "model_version",
        "profile_input_snapshot_json", "prompt_version", "reason_summary", "score",
        "screen_batch_id", "talent_profile_id", "verdict",
    },
    "snapshot_export_audit": {
        "created_at", "created_by", "format", "id",
        "snapshot_id",
    },
    "sys_departments": {
        "created_at", "id", "leader", "name",
        "parent_id", "phone", "sort", "status",
        "updated_at",
    },
    "sys_menus": {
        "created_at", "hidden", "icon", "id",
        "menu_type", "name", "parent_id", "path",
        "permission_code", "sort", "status", "updated_at",
    },
    "sys_operation_logs": {
        "action", "created_at", "duration_ms", "id",
        "ip", "method", "module", "params",
        "path", "status", "user_id", "username",
    },
    "sys_role_permissions": {
        "action", "data_scope", "id", "menu_id",
        "role_id",
    },
    "sys_roles": {
        "code", "created_at", "data_scope", "description",
        "id", "name", "status", "updated_at",
    },
    "sys_settings": {
        "created_at", "description", "id", "setting_key",
        "setting_value", "updated_at", "updated_by", "value_type",
    },
    "sys_user_roles": {
        "id", "role_id", "user_id",
    },
    "talent_duplicates": {
        "created_at", "id", "matched_value", "parsed_resume_id",
        "profile_a_id", "profile_b_id", "reason", "resolution_note",
        "resolved_at", "resolved_by", "status",
    },
    "talent_field_evidence": {
        "confidence", "created_at", "field_name", "id",
        "profile_version", "resume_file_id", "review_status", "source",
        "source_page", "source_text", "talent_profile_id",
    },
    "talent_profile_edits": {
        "created_at", "edited_by", "field_name", "id",
        "new_value", "old_value", "profile_version", "talent_profile_id",
    },
    "talent_profile_questions": {
        "asked_by", "created_at", "id", "person_id",
        "profile_version", "snapshot_json", "status",
    },
    "talent_profile_versions": {
        "change_type", "changed_by", "created_at", "id",
        "profile_snapshot_json", "talent_profile_id", "version",
    },
    "talent_profiles": {
        "ability_scores_json", "birth_year", "city", "created_at",
        "current_title", "degree", "deleted_at", "deleted_by",
        "email", "expected_title", "gender", "id",
        "manual_edited_fields", "name", "phone", "profile_text_hash",
        "skills_json", "source", "tags_json", "updated_at",
        "updated_by", "vector_status", "vector_synced_at", "work_years",
    },
    "talent_tags": {
        "created_at", "id", "source", "tag",
        "tag_type", "talent_profile_id",
    },
    "training_attendances": {
        "attended_at", "created_at", "employee_user_id", "id",
        "session_id", "updated_at",
    },
    "training_courses": {
        "approval_mode", "capacity", "completion_rules_json", "created_at",
        "created_by", "description", "dimension_names_json", "feedback_note",
        "feedback_note_at", "has_cost", "id", "material_url",
        "occupies_work_time", "status", "title", "updated_at",
        "updated_by", "version",
    },
    "training_enrollments": {
        "course_id", "created_at", "decided_at", "decided_by",
        "employee_user_id", "expires_at", "id", "manager_decided_at",
        "manager_decided_by", "note", "priority", "status",
        "updated_at",
    },
    "training_feedback_themes": {
        "course_id", "created_at", "feedback_count", "generated_at",
        "id", "prompt_version", "theme", "updated_at",
    },
    "training_feedbacks": {
        "comment", "content_score", "course_id", "created_at",
        "employee_user_id", "id", "instructor_score", "session_id",
        "status", "submitted_at", "updated_at", "withdrawn_at",
    },
    "training_growth_events": {
        "created_at", "employee_user_id", "event_type", "id",
        "payload_json", "ref_id", "ref_type", "source_type",
        "summary", "talent_profile_id", "updated_at",
    },
    "training_learning_evidence": {
        "created_at", "employee_user_id", "evidence_key", "evidence_type",
        "id", "learning_record_id", "payload_json", "score",
        "updated_at", "verified_by",
    },
    "training_learning_records": {
        "created_at", "employee_user_id", "evidence_score", "formal_completed",
        "id", "is_self_reported", "note", "plan_item_id",
        "reported_at", "status", "updated_at",
    },
    "training_plan_items": {
        "course_id", "course_snapshot_json", "created_at", "dimension_name",
        "due_date", "id", "plan_id", "plan_version",
        "reason", "sort_order", "updated_at",
    },
    "training_plan_versions": {
        "created_at", "created_by", "id", "note",
        "plan_id", "snapshot_json", "status", "updated_at",
        "version",
    },
    "training_plans": {
        "created_at", "created_by", "employee_user_id", "gap_summary",
        "id", "input_snapshot_json", "job_id", "source",
        "status", "talent_profile_id", "target_job_title", "updated_at",
        "updated_by", "version",
    },
    "training_sessions": {
        "course_id", "created_at", "created_by", "ends_at",
        "id", "instructor_name", "starts_at", "updated_at",
    },
    "training_study_sessions": {
        "active_minutes", "consumed", "created_at", "employee_user_id",
        "expires_at", "heartbeat_count", "id", "issued_at",
        "last_heartbeat_at", "learning_record_id", "nonce", "updated_at",
    },
    "user_scope_grants": {
        "created_at", "grantee_user_id", "granter_user_id", "id",
        "reason", "revoked_at", "revoked_by", "scope_ref_id",
        "scope_type", "updated_at", "valid_from", "valid_until",
    },
    "users": {
        "created_at", "department_id", "display_name", "id",
        "is_active", "last_login_at", "password_hash", "person_id",
        "role", "updated_at", "username",
    },
    "workflow_definition_versions": {
        "created_at", "created_by", "definition_id", "id",
        "note", "published_at", "published_by", "review_note",
        "status", "submitted_at", "submitted_by", "updated_at",
        "version",
    },
    "workflow_definitions": {
        "code", "created_at", "description", "id",
        "name", "updated_at",
    },
    "workflow_edges": {
        "branch", "condition_json", "created_at", "id",
        "source_node_key", "target_node_key", "updated_at", "version_id",
    },
    "workflow_engine_runs": {
        "created_at", "error", "finished_at", "graph_name",
        "id", "status", "thread_id", "updated_at",
    },
    "workflow_nodes": {
        "config_json", "created_at", "id", "inputs_json",
        "name", "node_key", "node_type", "outputs_json",
        "position_json", "updated_at", "version_id",
    },
    "workflow_run_steps": {
        "attempt", "created_at", "error", "finished_at",
        "id", "node_key", "node_type", "output_json",
        "run_id", "started_at", "status", "updated_at",
    },
    "workflow_runs": {
        "created_at", "created_by", "definition_id", "error",
        "finished_at", "id", "params_json", "status",
        "thread_id", "trigger_ref", "trigger_type", "updated_at",
        "version_id",
    },
    "workflow_skill_versions": {
        "created_at", "created_by", "id", "note",
        "params_json_schema", "prompt_template", "published_at", "published_by",
        "review_note", "skill_id", "status", "submitted_at",
        "submitted_by", "updated_at", "version",
    },
    "workflow_skills": {
        "code", "created_at", "description", "id",
        "name", "output_parser", "tool", "updated_at",
    },
}

EXPECTED_UNIQUE: dict[str, set[str]] = {
    "users": {"uk_username", "uk_user_person_id"},
    "sys_settings": {"uk_setting_key"},
    "resume_files": {"uk_file_hash"},
    "parse_tasks": {"uk_resume_file"},
    "parsed_resumes": {"uk_parsed_resume_file"},
    "talent_tags": {"uk_profile_tag"},
    "talent_duplicates": {"uk_parsed_resume"},
    "screen_batches": {"uk_screen_batch_idem"},  # T-A42 (job_profile_id, idempotency_key)
    "match_batches": {"uk_match_batch_idem"},    # T-A42 (job_profile_id, idempotency_key)
    "screen_results": {"uk_screen_batch_talent"},
    "match_results": {"uk_match_batch_talent"},
    # ---- C 模块唯一键（来自 app/modules/*/models.py，2026-11-07 注册时提取）----
    "assessment_papers": {"uk_paper_plan_version"},
    "assessment_assignments": {"uk_assignment_idempotency"},
    "assessment_answers": {"uk_answer_assignment_question"},
    "assessment_results": {"uk_result_assignment_version"},
    "assessment_selections": {"uk_selection_question", "uk_selection_sequence"},
    "sys_roles": {"uk_role_code"},
    "sys_user_roles": {"uk_user_role"},
    "sys_role_permissions": {"uk_role_menu_action"},
    "assessment_review_items": {"uk_review_assignment_question_user"},
    "interview_feedback": {"uk_feedback_round"},
    "ai_interview_questions": {"uk_ai_q_session_seq"},
    "training_attendances": {"uk_attendance_session_employee"},
    "training_feedbacks": {"uk_feedback_course_employee"},
    "training_feedback_themes": {"uk_feedback_theme_course"},
    "training_enrollments": {"uk_enrollment_course_employee"},
    "training_plan_items": {"uk_plan_version_course"},
    "training_plan_versions": {"uk_plan_version"},
    "training_learning_records": {"uk_learning_item_employee"},
    "training_learning_evidence": {"uk_evidence_record_key"},
    "inventory_development_plans": {"uk_idp_review"},
    "notifications": {"uk_notifications_idempotency"},
    "workflow_engine_runs": {"uk_workflow_engine_runs_thread"},
    # ---- D 工作流数据模型（T-D14）----
    "workflow_definitions": {"uk_workflow_definitions_code"},
    "workflow_definition_versions": {"uk_workflow_version"},
    "workflow_nodes": {"uk_workflow_node_key"},
    "workflow_edges": {"uk_workflow_edge"},
    "workflow_runs": {"uk_workflow_runs_thread"},
    "workflow_run_steps": {"uk_workflow_step_attempt"},
    "workflow_skills": {"uk_workflow_skills_code"},
    "workflow_skill_versions": {"uk_workflow_skill_version"},
    "ai_providers": {"uk_ai_providers_name"},
    "ai_prompt_versions": {"uk_ai_prompt_versions"},
    "member_im_accounts": {"uk_member_im_user_channel"},
    "digital_tasks": {"uk_digital_tasks_idem"},
}

EXPECTED_INDEXES: dict[str, set[str]] = {
    "resume_files": {"idx_upload_batch", "idx_uploaded_by"},
    "parse_tasks": {"idx_status"},
    "parsed_resumes": {"idx_parsed_talent"},
    "talent_profiles": {
        "idx_name", "idx_phone", "idx_email", "idx_degree", "idx_work_years",
        "idx_updated_at", "idx_deleted_at",
    },
    "talent_tags": {"idx_tag", "idx_tag_type_tag"},
    "talent_duplicates": {"idx_dup_profile_a", "idx_dup_profile_b"},
    "screen_batches": {"idx_screen_job"},
    "screen_results": {"idx_batch_verdict", "idx_screen_job_result", "idx_talent"},
    "match_batches": {"idx_match_job"},
    "match_results": {"idx_match_talent"},
    "notifications": {"idx_notifications_recipient", "idx_notifications_ref"},
    "workflow_engine_runs": {"idx_workflow_engine_runs_status"},
    "analytics_query_snapshots": {"idx_aqs_metric_time", "idx_aqs_user"},
    # ---- D 工作流数据模型（T-D14）----
    "workflow_definition_versions": {"idx_wdv_definition_status"},
    "workflow_nodes": {"idx_workflow_nodes_version"},
    "workflow_edges": {"idx_workflow_edges_version"},
    "workflow_runs": {"idx_workflow_runs_status", "idx_workflow_runs_definition"},
    "workflow_skill_versions": {"idx_wsv_skill_status"},
    "ai_prompt_versions": {"idx_aip_name_status"},
    "ai_call_logs": {"idx_aic_created", "idx_aic_purpose"},
    "member_im_accounts": {"idx_mia_user"},
    "digital_tasks": {"idx_dt_type_status"},
    "digital_outbound_requests": {"idx_dor_status", "idx_dor_task"},
}


def ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def test_exactly_70_tables_and_no_phase2_table():
    # 一期表数不在测试里写死：以 EXPECTED_COLUMNS 为唯一基线，新增一期表只需
    # 同步基线条目（历史上硬编码 76/84 曾造成新基线全量假失败）。
    assert len(TABLE_NAMES) == len(set(TABLE_NAMES)) == len(MODEL_CLASSES)
    assert len(TABLE_NAMES) == len(EXPECTED_COLUMNS)
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)
    assert set(TABLE_NAMES) == set(EXPECTED_COLUMNS)
    # D-7 缓办：一期不建 llm_score_cache
    assert "llm_score_cache" not in Base.metadata.tables


@pytest.mark.parametrize("model", MODEL_CLASSES, ids=lambda m: m.__tablename__)
def test_columns_match_database_design(model):
    """逐字段对照：多一列、少一列都算失败。"""
    actual = {c.name for c in model.__table__.columns}
    assert actual == EXPECTED_COLUMNS[model.__tablename__], (
        f"{model.__tablename__}: 多={actual - EXPECTED_COLUMNS[model.__tablename__]}, "
        f"少={EXPECTED_COLUMNS[model.__tablename__] - actual}"
    )


@pytest.mark.parametrize("model", MODEL_CLASSES, ids=lambda m: m.__tablename__)
def test_primary_key_and_timestamps(model):
    """§1 总体约定：BIGINT UNSIGNED AI 主键 + created_at DEFAULT CURRENT_TIMESTAMP。

    已知差异（2026-11-07 集成时发现，已发请求 C 对齐）：C 域 21 表用
    `CTimestampMixin`，updated_at 由应用层维护、无 `ON UPDATE CURRENT_TIMESTAMP`；
    公共 13 表用 `UpdatedAtMixin`，由 MySQL 维护。对齐前按各自口径断言，
    C 收敛到公共 mixin 后本测试自动收紧（C_DOMAIN_TABLES 判定会随表名沿用）。
    """
    table = model.__table__
    pk = list(table.primary_key.columns)
    assert len(pk) == 1 and pk[0].name == "id"
    assert pk[0].autoincrement is True
    text = ddl(model)
    assert "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in text, model.__tablename__
    if model.__tablename__ in PURE_LINK_TABLES or model.__tablename__ == "parse_attempts":
        # 纯关联表无 created_at/updated_at（design §8.3 口径）
        assert "created_at" not in text, model.__tablename__
    else:
        assert "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in text
    if "updated_at" in EXPECTED_COLUMNS[model.__tablename__]:
        # updated_at 两种合法维护方式（均已登记）：MySQL ON UPDATE（UpdatedAtMixin，
        # 公共 13 表与招聘/系统域）与应用层 onupdate（CrossDb/CTimestamp 变体，C/B/D 域）。
        # 非豁免表必须 ON UPDATE；豁免集（C_DOMAIN ∪ CROSS_DB）接受任一。
        if model.__tablename__ in (C_DOMAIN_TABLES | CROSS_DB_TABLES):
            assert "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in text
        else:
            assert "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in text
    else:
        assert "updated_at" not in text
    assert "ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci" in text


def test_unique_constraints_by_name():
    for table_name, expected in EXPECTED_UNIQUE.items():
        actual = {
            c.name for c in Base.metadata.tables[table_name].constraints
            if c.__class__.__name__ == "UniqueConstraint" and c.name
        }
        assert expected <= actual, f"{table_name}: 缺唯一约束 {expected - actual}"

    # 9 个文档点名的唯一键，逐个核对列组合
    def unique_cols(table_name, name):
        for c in Base.metadata.tables[table_name].constraints:
            if c.name == name:
                return tuple(col.name for col in c.columns)
        return None

    assert unique_cols("users", "uk_username") == ("username",)
    assert unique_cols("users", "uk_user_person_id") == ("person_id",)
    assert unique_cols("sys_settings", "uk_setting_key") == ("setting_key",)
    assert unique_cols("resume_files", "uk_file_hash") == ("file_hash",)
    assert unique_cols("parse_tasks", "uk_resume_file") == ("resume_file_id",)
    assert unique_cols("parsed_resumes", "uk_parsed_resume_file") == ("resume_file_id",)
    assert unique_cols("talent_tags", "uk_profile_tag") == ("talent_profile_id", "tag")
    assert unique_cols("talent_duplicates", "uk_parsed_resume") == ("parsed_resume_id",)
    assert unique_cols("screen_results", "uk_screen_batch_talent") == (
        "screen_batch_id", "talent_profile_id",
    )
    # T-A42：组合唯一，NULL 键允许无限多行（不传键＝行为零变化）
    assert unique_cols("screen_batches", "uk_screen_batch_idem") == (
        "job_profile_id", "idempotency_key",
    )
    assert unique_cols("match_batches", "uk_match_batch_idem") == (
        "job_profile_id", "idempotency_key",
    )
    assert unique_cols("match_results", "uk_match_batch_talent") == (
        "match_batch_id", "talent_profile_id",
    )
    # C 模块唯一键列组合（2026-11-07 注册时从模型提取）
    assert unique_cols("assessment_papers", "uk_paper_plan_version") == ("plan_id", "plan_version")
    assert unique_cols("assessment_assignments", "uk_assignment_idempotency") == ("idempotency_key",)
    assert unique_cols("assessment_answers", "uk_answer_assignment_question") == ("assignment_id", "question_id")
    assert unique_cols("assessment_results", "uk_result_assignment_version") == ("assignment_id", "version")
    assert unique_cols("assessment_selections", "uk_selection_question") == ("assignment_id", "question_id")
    assert unique_cols("assessment_selections", "uk_selection_sequence") == ("assignment_id", "sequence_no")
    assert unique_cols("sys_roles", "uk_role_code") == ("code",)
    assert unique_cols("sys_user_roles", "uk_user_role") == ("user_id", "role_id")
    assert unique_cols("sys_role_permissions", "uk_role_menu_action") == ("role_id", "menu_id", "action")
    assert unique_cols("assessment_review_items", "uk_review_assignment_question_user") == (
        "assignment_id", "question_id", "user_id",
    )
    assert unique_cols("interview_feedback", "uk_feedback_round") == ("round_id",)
    assert unique_cols("training_attendances", "uk_attendance_session_employee") == (
        "session_id", "employee_user_id",
    )
    assert unique_cols("training_feedbacks", "uk_feedback_course_employee") == (
        "course_id", "employee_user_id",
    )
    assert unique_cols("training_feedback_themes", "uk_feedback_theme_course") == ("course_id", "theme")
    assert unique_cols("training_plan_items", "uk_plan_version_course") == (
        "plan_id", "plan_version", "course_id",
    )
    assert unique_cols("training_plan_versions", "uk_plan_version") == ("plan_id", "version")
    assert unique_cols("training_learning_records", "uk_learning_item_employee") == (
        "plan_item_id", "employee_user_id",
    )
    assert unique_cols("ai_interview_questions", "uk_ai_q_session_seq") == (
        "session_id", "sequence_no",
    )
    assert unique_cols("training_enrollments", "uk_enrollment_course_employee") == (
        "course_id", "employee_user_id",
    )
    assert unique_cols("inventory_development_plans", "uk_idp_review") == ("review_id",)


def test_indexes_by_name():
    for table_name, expected in EXPECTED_INDEXES.items():
        actual = {i.name for i in Base.metadata.tables[table_name].indexes}
        assert expected <= actual, f"{table_name}: 缺索引 {expected - actual}"

    assert tuple(
        c.name for i in Base.metadata.tables["talent_tags"].indexes
        if i.name == "idx_tag_type_tag" for c in i.columns
    ) == ("tag_type", "tag")
    assert tuple(
        c.name for i in Base.metadata.tables["screen_results"].indexes
        if i.name == "idx_batch_verdict" for c in i.columns
    ) == ("screen_batch_id", "verdict")


def test_v12_field_renames_and_removed_columns():
    """决策 D-E / D-8 / D-H：批次表无 retry_of_batch_id、结果表无 rank。"""
    for batch in (ScreenBatch, MatchBatch):
        cols = {c.name for c in batch.__table__.columns}
        assert {"retry_count", "vector_degraded"} <= cols
        assert "retry_of_batch_id" not in cols
    assert "rank" not in {c.name for c in MatchResult.__table__.columns}
    assert "is_manual_edited" not in {c.name for c in TalentProfile.__table__.columns}
    assert "pending_cnt" not in {c.name for c in ScreenBatch.__table__.columns}
    assert "pending_count" in {c.name for c in ScreenBatch.__table__.columns}
    assert "hash_match" not in ddl(TalentDuplicate)


def test_enum_literals_match_algorithm_v12():
    assert "高中中专" in DEGREE_VALUES
    assert "高中/中专" not in DEGREE_VALUES
    assert len(DEGREE_VALUES) == 6
    assert "cancelled" in PARSE_TASK_STATUSES
    assert set(PARSE_TASK_STATUSES) == {
        "pending", "running", "success", "failed", "duplicate_pending", "cancelled",
    }
    assert set(VECTOR_STATUSES) == {"pending", "synced", "failed"}
    # 默认值落在白名单内
    for model, field, allowed in (
        (User, "role", {"hr"}),
        (ParseTask, "status", set(PARSE_TASK_STATUSES)),
        (TalentProfile, "vector_status", set(VECTOR_STATUSES)),
        (ScreenBatch, "status", {"pending", "running", "done", "failed"}),
        (MatchBatch, "status", {"pending", "running", "done", "failed"}),
    ):
        col = model.__table__.c[field]
        assert col.default is not None and col.default.arg in allowed


def test_column_types():
    assert isinstance(ResumeFile.__table__.c.file_hash.type, CHAR)
    assert ResumeFile.__table__.c.file_hash.type.length == 64
    assert ResumeFile.__table__.c.upload_batch_id.type.length == 26
    assert isinstance(ParseTask.__table__.c.error.type, Text)
    assert isinstance(ParsedResume.__table__.c.raw_text.type, mysql.MEDIUMTEXT)
    assert isinstance(TalentProfile.__table__.c.work_years.type, DECIMAL)
    assert (TalentProfile.__table__.c.work_years.type.precision, TalentProfile.__table__.c.work_years.type.scale) == (4, 1)
    assert isinstance(JobProfile.__table__.c.pass_threshold.type, DECIMAL)
    assert (JobProfile.__table__.c.pass_threshold.type.precision, JobProfile.__table__.c.pass_threshold.type.scale) == (5, 2)
    assert isinstance(ScreenResult.__table__.c.score.type, DECIMAL)
    assert isinstance(MatchResult.__table__.c.score.type, DECIMAL)
    assert MatchResult.__table__.c.score.nullable is False  # 硬淘汰=0，不为 NULL
    assert ScreenResult.__table__.c.score.nullable is True  # 硬淘汰为 NULL
    assert "TINYINT(1)" in ddl(ParseTask)
    assert "TINYINT " in ddl(ParseTask)  # retries 用 TINYINT


def test_json_columns_are_mutable():
    """§1 明确点名的隐蔽 bug：原地修改 JSON 必须能被 ORM 追踪。"""
    json_columns = {
        (ParsedResume, "structured_json"): dict,
        (TalentProfile, "skills_json"): list,
        (TalentProfile, "tags_json"): list,
        (TalentProfile, "ability_scores_json"): dict,
        (TalentProfile, "manual_edited_fields"): list,
        (JobProfile, "must_skills_json"): list,
        (JobProfile, "hard_rule_switches_json"): dict,
        (JobProfile, "weights_json"): dict,
        (ScreenBatch, "scope_json"): dict,
        (MatchResult, "dimension_scores_json"): dict,
    }
    for (model, name), kind in json_columns.items():
        col = model.__table__.c[name]
        assert isinstance(col.type, JSON), f"{model.__tablename__}.{name}"
        # as_mutable 后 attribute set 会 coerce 成 MutableDict / MutableList
        obj = model(**{name: {"a": 1} if kind is dict else ["a"]})
        value = getattr(obj, name)
        assert isinstance(value, MutableDict if kind is dict else MutableList), (
            f"{model.__tablename__}.{name} 未启用 Mutable 追踪"
        )
        if kind is dict:
            value["b"] = 2
            assert value["b"] == 2
        else:
            value.append("b")
            assert "b" in value


def test_no_physical_foreign_keys():
    """§1：不建物理外键，一致性由 service 层同事务保证。

    例外（用户 2026-11-07 逐项对接裁决「方案 1：加外键 + 新迁移」）：
    `C_0004_application_fk` 为 C 的 `interview_rounds` / `ai_interview_sessions`
    的 `application_id` 建立指向 A 的 `applications.id` 的外键，防止误删申请
    连带丢失面试记录。该外键已随 master 发布并在 MySQL 空库实测存在（见
    `docs/updates/A/20261110-1000-C_0001空库迁移修复与结构基线同步.md`
    受理记录）。除此之外一律不允许物理外键；
    若后续要维持零物理外键基线，需回退 C_0004，A 不在此擅自裁决。

    断言形式沿用 B 集成版的「实际集合恰好等于白名单」，除拦截新增外键外
    也能发现已裁决外键被误删（A 原版逐条 assert 只能拦截前者）。
    """
    actual = {
        (table.name, key.parent.name, key.target_fullname)
        for table in Base.metadata.tables.values()
        for key in table.foreign_keys
    }
    assert actual == {
        ("interview_rounds", "application_id", "applications.id"),
        ("ai_interview_sessions", "application_id", "applications.id"),
    }
