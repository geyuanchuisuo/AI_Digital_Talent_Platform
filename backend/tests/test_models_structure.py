"""L1 单元测试 · 45 张表结构逐字段对照（T-03 验收点 + 裁决 C-04）。

不触真实 MySQL：用 MySQL dialect 编译 DDL + 检查 metadata，纯内存执行。
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
        "inventory_reviews", "inventory_talent_pools", "inventory_disputes",
        "inventory_development_plans", "inventory_events",
    )
)

# 逐表期望列名（含 id / created_at / updated_at），来源：数据库设计 v1.2 §3~§6
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "talent_profile_versions": {"id", "talent_profile_id", "version", "change_type", "profile_snapshot_json", "changed_by", "created_at"},
    "talent_field_evidence": {"id", "talent_profile_id", "profile_version", "field_name", "resume_file_id", "source_page", "source_text", "source", "confidence", "review_status", "created_at"},
    "talent_profile_edits": {"id", "talent_profile_id", "profile_version", "field_name", "old_value", "new_value", "edited_by", "created_at"},
    "resume_pages": {"id", "resume_file_id", "page_no", "text_content", "ocr_engine", "confidence", "source_type", "quality_status", "error_message", "created_at"},
    "parse_attempts": {"id", "parse_task_id", "attempt_no", "status", "stage", "error_message", "started_at", "finished_at", "trace_json"},
    "users": {
        "id", "username", "password_hash", "display_name", "role", "person_id",
        "department_id", "is_active", "last_login_at", "created_at", "updated_at",
    },
    "sys_settings": {
        "id", "setting_key", "setting_value", "value_type", "description",
        "updated_by", "created_at", "updated_at",
    },
    "resume_files": {
        "id", "filename", "stored_path", "file_hash", "file_ext", "file_size",
        "upload_batch_id", "uploaded_by", "created_at",
    },
    "parse_tasks": {
        "id", "resume_file_id", "status", "stage", "error", "retries", "truncated",
        "started_at", "heartbeat_at", "profiled_at", "finished_at",
        "created_at", "updated_at",
    },
    "parsed_resumes": {
        "id", "resume_file_id", "talent_profile_id", "structured_json",
        "raw_text", "llm_model", "created_at",
    },
    "talent_profiles": {
        "id", "name", "gender", "birth_year", "phone", "email", "city", "degree",
        "work_years", "current_title", "expected_title", "skills_json", "tags_json",
        "ability_scores_json", "profile_text_hash", "vector_status",
        "vector_synced_at", "source", "manual_edited_fields", "deleted_at",
        "deleted_by", "updated_by", "created_at", "updated_at", "department_id",
    },
    "talent_tags": {
        "id", "talent_profile_id", "tag", "tag_type", "source", "created_at",
    },
    "talent_duplicates": {
        "id", "profile_a_id", "profile_b_id", "parsed_resume_id", "reason",
        "matched_value", "status", "resolution_note", "resolved_by", "resolved_at",
        "created_at",
    },
    "job_profiles": {
        "id", "title", "department", "jd_text", "requirements_text", "min_degree",
        "min_work_years", "must_skills_json", "bonus_skills_json",
        "hard_rule_switches_json", "pass_threshold", "pending_threshold",
        "weights_json", "version", "is_active", "created_by", "updated_by",
        "created_at", "updated_at",
    },
    "screen_batches": {
        "id", "job_profile_id", "job_profile_version", "config_snapshot_json",
        "scope_json", "status", "progress", "total", "passed", "pending_count",
        "rejected", "started_at", "heartbeat_at", "error", "retry_count",
        "vector_degraded", "created_by", "created_at", "finished_at",
        "idempotency_key",  # T-A42 请求级幂等键（可空）
    },
    "screen_results": {
        "id", "screen_batch_id", "job_profile_id", "talent_profile_id", "hard_fail",
        "hard_fail_reason", "score", "verdict", "manual_verdict",
        "manual_verdict_by", "manual_verdict_at", "reason_summary", "hits_json",
        "misses_json", "model_version", "prompt_version", "attempt",
        "profile_input_snapshot_json", "created_at",
    },
    "match_batches": {
        "id", "job_profile_id", "job_profile_version", "config_snapshot_json",
        "talent_scope_json", "status", "progress", "total", "hard_failed",
        "started_at", "heartbeat_at", "error", "retry_count", "vector_degraded",
        "created_by", "created_at", "finished_at",
        "idempotency_key",  # T-A42 请求级幂等键（可空）
    },
    "match_results": {
        "id", "match_batch_id", "talent_profile_id", "score", "hard_fail",
        "hard_fail_reason", "dimension_scores_json", "hits_json", "misses_json",
        "reason", "model_version", "prompt_version", "attempt",
        "profile_input_snapshot_json", "manual_verdict", "manual_verdict_by",
        "manual_verdict_at", "created_at",
    },
    # ---- C 模块 32 表（测评 9 / 人工面试 3 / AI 面试间 3 / 培训 12 / 盘点 5，裁决 C-04）----
    "assessment_questions": {
        "id", "job_id", "job_version", "job_title", "dimension_name",
        "dimension_type", "difficulty", "question_type", "knowledge_point", "stem",
        "options_json", "correct_answer", "reference_points", "scoring_guideline",
        "status", "created_by", "updated_by", "created_at", "updated_at",
    },
    "assessment_plans": {
        "id", "purpose", "job_id", "job_title", "job_version", "dimensions_json",
        "total_question_count", "pass_score", "difficulty_structure_json",
        "status", "version", "published_at", "created_by", "updated_by",
        "mode", "strategy_json", "bank_snapshot_json",
        "created_at", "updated_at",
    },
    "assessment_papers": {
        "id", "plan_id", "plan_version", "purpose", "job_id", "job_title",
        "question_count", "total_score", "pass_score", "dimensions_json",
        "questions_json", "grading_rule_json", "created_by",
        "created_at", "updated_at",
    },
    "assessment_assignments": {
        "id", "plan_id", "plan_version", "paper_id", "purpose", "user_id",
        "talent_profile_id", "job_id", "job_title", "status",
        "published_result_version", "answers_open", "attempt_no",
        "idempotency_key", "started_at", "submitted_at", "completed_at",
        "cancelled_reason", "mode", "strategy_snapshot_json",
        "target_question_count", "created_by", "created_at", "updated_at",
    },
    "assessment_answers": {
        "id", "assignment_id", "question_id", "question_snapshot_json",
        "selected_option", "answer_text", "is_correct", "score",
        "grading_status", "failure_reason", "graded_by", "graded_at",
        "created_at", "updated_at",
    },
    "assessment_results": {
        "id", "assignment_id", "version", "dimension_scores_json", "total_score",
        "pass_score", "passed", "grading_snapshot_json", "ability_json",
        "created_by", "created_at", "updated_at",
    },
    "assessment_selections": {
        "id", "assignment_id", "sequence_no", "question_id", "dimension_name",
        "difficulty", "question_snapshot_json", "selection_reason_json",
        "strategy_snapshot_json", "created_at", "updated_at",
    },
    "assessment_reviews": {
        "id", "assignment_id", "result_id", "reviewer_id", "reason",
        "before_json", "after_json", "created_at", "updated_at",
    },
    "assessment_review_items": {
        "id", "assignment_id", "question_id", "user_id", "is_completed",
        "completed_at", "question_snapshot_json", "created_at", "updated_at",
    },
    "interview_rounds": {
        "id", "candidate_user_id", "talent_profile_id", "job_id", "application_id",
        "job_title", "round_no", "round_name", "scheduled_at", "mode",
        "location_or_link", "interviewer_user_ids_json", "status", "internal_note",
        "client_request_id", "version", "created_by", "updated_by",
        "created_at", "updated_at",
    },
    "interview_feedback": {
        "id", "round_id", "interviewer_user_id", "evaluation", "evidence",
        "strengths", "weaknesses", "recommendation", "version",
        "created_at", "updated_at",
    },
    "interview_round_events": {
        "id", "round_id", "event_type", "before_json", "after_json",
        "operator_id", "note", "created_at", "updated_at",
    },
    "ai_interview_sessions": {
        "id", "candidate_user_id", "talent_profile_id", "job_id",
        "application_id", "status", "started_at", "finished_at", "overall_score",
        "overall_feedback", "note", "created_at", "updated_at",
    },
    "ai_interview_questions": {
        "id", "session_id", "sequence_no", "question_text", "question_type",
        "answer_text", "ai_score", "ai_feedback_json", "status",
        "created_at", "updated_at",
    },
    "ai_interview_events": {
        "id", "session_id", "event_type", "operator_id", "note",
        "payload_json", "created_at", "updated_at",
    },
    # ---- 组织范围授权（功能 11，A_0007；跨部门授权记录依据+期限）----
    "user_scope_grants": {
        "id", "granter_user_id", "grantee_user_id", "scope_type", "scope_ref_id",
        "reason", "valid_from", "valid_until", "revoked_at", "revoked_by",
        "created_at", "updated_at",
    },
    # ---- 系统管理域 6 表（8b，design §8.3 冻结口径）----
    "sys_departments": {
        "id", "name", "parent_id", "leader", "phone", "sort", "status",
        "created_at", "updated_at",
    },
    "sys_roles": {
        "id", "code", "name", "description", "data_scope", "status",
        "created_at", "updated_at",
    },
    "sys_user_roles": {"id", "user_id", "role_id"},
    "sys_role_permissions": {
        "id", "role_id", "menu_id", "action", "data_scope",
    },
    "sys_menus": {
        "id", "parent_id", "name", "path", "icon", "permission_code",
        "menu_type", "hidden", "sort", "status", "created_at", "updated_at",
    },
    "sys_operation_logs": {
        "id", "user_id", "username", "module", "action", "method", "path",
        "params", "ip", "status", "duration_ms", "created_at",
    },
    # ---- 招聘域 5 表（M3-V2.6 功能 8a-1，冻结口径）----
    "project_groups": {
        "id", "name", "description", "status", "created_by",
        "created_at", "updated_at",
    },
    "projects": {
        "id", "project_group_id", "name", "description", "status", "created_by",
        "created_at", "updated_at",
    },
    "project_demands": {
        "id", "project_id", "project_group_id", "job_profile_id", "position_name",
        "headcount", "expected_start_date", "priority", "remark", "status",
        "created_by", "created_at", "updated_at",
    },
    "jd_publications": {
        "id", "job_profile_id", "job_profile_version", "title",
        "content_snapshot_json", "channel", "status", "published_by",
        "published_at", "withdrawn_by", "withdrawn_at", "created_at", "updated_at",
    },
    "applications": {
        "id", "talent_profile_id", "job_profile_id", "job_profile_version",
        "resume_file_id", "internal_stage", "external_stage", "result_category",
        "result_confirmed_by", "stage_reason", "stage_updated_by",
        "stage_updated_at", "closed_at", "input_snapshot_json", "input_snapshot_at",
        "created_by", "created_at", "updated_at",
    },
    "talent_profile_questions": {"id", "person_id", "profile_version", "asked_by", "status", "snapshot_json", "created_at"},
    "training_courses": {
        "id", "title", "description", "material_url", "dimension_names_json",
        "status", "version", "feedback_note", "feedback_note_at", "capacity",
        "created_by", "updated_by", "created_at", "updated_at",
        "capacity",  # C_0006_course_capacity（课程名额上限）
    },
    "training_sessions": {
        "id", "course_id", "instructor_name", "starts_at", "ends_at",
        "created_by", "created_at", "updated_at",
    },
    "training_attendances": {
        "id", "session_id", "employee_user_id", "attended_at",
        "created_at", "updated_at",
    },
    "training_feedbacks": {
        "id", "course_id", "employee_user_id", "session_id", "content_score",
        "instructor_score", "comment", "status", "submitted_at", "withdrawn_at",
        "created_at", "updated_at",
    },
    "training_feedback_themes": {
        "id", "course_id", "theme", "feedback_count", "prompt_version",
        "generated_at", "created_at", "updated_at",
    },
    "training_plans": {
        "id", "employee_user_id", "talent_profile_id", "job_id",
        "target_job_title", "source", "status", "input_snapshot_json",
        "gap_summary", "version", "created_by", "updated_by",
        "created_at", "updated_at",
    },
    "training_plan_items": {
        "id", "plan_id", "plan_version", "course_id", "course_snapshot_json",
        "dimension_name", "reason", "sort_order", "created_at", "updated_at",
        "due_date",  # C_0005_plan_item_due_date（F09 必修到期/逾期/延期）
    },
    "training_plan_versions": {
        "id", "plan_id", "version", "status", "snapshot_json", "note",
        "created_by", "created_at", "updated_at",
    },
    "training_learning_records": {
        "id", "plan_item_id", "employee_user_id", "status", "note",
        "reported_at", "is_self_reported", "formal_completed", "evidence_score",
        "created_at", "updated_at",
    },
    "training_learning_evidence": {
        "id", "learning_record_id", "employee_user_id", "evidence_type", "evidence_key",
        "payload_json", "score", "verified_by", "created_at", "updated_at",
        "evidence_key",  # C_0007_learning_evidence_key（可信来源键，防重放）
    },
    "training_enrollments": {
        "id", "course_id", "employee_user_id", "priority", "status",
        "expires_at", "decided_by", "decided_at", "note", "created_at", "updated_at",
    },
    "training_growth_events": {
        "id", "employee_user_id", "talent_profile_id", "source_type", "event_type",
        "ref_type", "ref_id", "summary", "payload_json", "created_at", "updated_at",
    },
    "inventory_reviews": {
        "id", "employee_user_id", "talent_profile_id", "cycle_name", "review_type",
        "status", "performance_score", "performance_source",
        "potential_manager_score", "potential_hr_score", "grid_cell",
        "reviewer_manager_id", "reviewer_hr_id", "confirmed_at", "summary",
        "payload_json", "created_at", "updated_at",
    },
    "inventory_talent_pools": {
        "id", "employee_user_id", "pool_type", "status", "nominated_by",
        "decided_by", "decided_at", "note", "created_at", "updated_at",
    },
    "inventory_disputes": {
        "id", "review_id", "employee_user_id", "status", "content", "response",
        "responded_by", "created_at", "updated_at",
    },
    "inventory_development_plans": {
        "id", "review_id", "employee_user_id", "content_json", "status",
        "created_at", "updated_at",
    },
    "inventory_events": {
        "id", "review_id", "event_type", "operator_id", "note",
        "payload_json", "created_at", "updated_at",
    },
    # ---- D 通知中心（T-D03，契约 docs/modules/D/通知契约.md §2）----
    "notifications": {
        "id", "event_type", "ref_type", "ref_id", "actor_user_id",
        "recipient_user_id", "payload_json", "idempotency_key", "status",
        "failure_reason", "created_at", "sent_at", "read_at",
    },
    # ---- D 工作流引擎（T-D04）----
    "workflow_engine_runs": {
        "id", "graph_name", "thread_id", "status", "error",
        "finished_at", "created_at", "updated_at",
    },
    # ---- D 问数切片（T-D01b）----
    "analytics_query_snapshots": {
        "id", "metric_id", "params_json", "data_time", "result_json",
        "source", "cache_hit", "created_by", "created_at",
    },
    # ---- D 问数服务（T-D08）----
    "metric_registry": {
        "id", "metric_id", "name", "domain", "status", "definition_ref",
        "allowed_roles", "params_json", "cache_ttl", "note",
        "created_at", "updated_at",
    },
    # ---- D 自由问数（T-D09）----
    "nl2sql_audit": {
        "id", "question", "generated_sql", "stage", "refused_reason",
        "row_count", "duration_ms", "created_by", "created_at",
    },
    # ---- D 问数反馈（T-D12）----
    "analytics_feedback": {
        "id", "snapshot_id", "comment", "created_by", "created_at",
    },
    # ---- D 报告导出（T-D11）----
    "snapshot_export_audit": {
        "id", "snapshot_id", "format", "created_by", "created_at",
    },
    # ---- D 工作流数据模型（T-D14，契约 docs/modules/D/工作流数据模型契约.md）----
    "workflow_definitions": {
        "id", "code", "name", "description", "created_at", "updated_at",
    },
    "workflow_definition_versions": {
        "id", "definition_id", "version", "status", "note", "created_by",
        "submitted_by", "submitted_at", "review_note",
        "published_by", "published_at", "created_at", "updated_at",
    },
    "workflow_nodes": {
        "id", "version_id", "node_key", "name", "node_type", "position_json",
        "config_json", "inputs_json", "outputs_json", "created_at", "updated_at",
    },
    "workflow_edges": {
        "id", "version_id", "source_node_key", "target_node_key", "branch",
        "condition_json", "created_at", "updated_at",
    },
    "workflow_runs": {
        "id", "definition_id", "version_id", "thread_id", "trigger_type",
        "trigger_ref", "params_json", "status", "error", "created_by",
        "finished_at", "created_at", "updated_at",
    },
    "workflow_run_steps": {
        "id", "run_id", "node_key", "node_type", "attempt", "status",
        "output_json", "error", "started_at", "finished_at",
        "created_at", "updated_at",
    },
    "workflow_skills": {
        "id", "code", "name", "description", "tool", "output_parser",
        "created_at", "updated_at",
    },
    "workflow_skill_versions": {
        "id", "skill_id", "version", "status", "prompt_template",
        "params_json_schema", "note", "created_by",
        "submitted_by", "submitted_at", "review_note",
        "published_by", "published_at", "created_at", "updated_at",
    },
    "ai_providers": {
        "id", "name", "provider_type", "base_url", "model", "api_key",
        "enabled", "is_default", "timeout_seconds", "note", "updated_by",
        "created_at", "updated_at",
    },
    "ai_prompt_versions": {
        "id", "name", "version", "status", "content", "note", "created_by",
        "published_by", "published_at", "created_at", "updated_at",
    },
    "ai_call_logs": {
        "id", "provider_name", "model", "purpose", "result", "latency_ms",
        "prompt_tokens", "completion_tokens", "error", "actor_user_id",
        "created_at", "updated_at",
    },
    "member_im_accounts": {
        "id", "user_id", "channel", "im_user_id", "source", "enabled", "note",
        "created_at", "updated_at",
    },
    "digital_tasks": {
        "id", "task_type", "status", "idempotency_key", "trigger", "triggered_by",
        "notify_admin_ids", "result_json", "summary", "error", "started_at",
        "finished_at", "created_at", "updated_at",
    },
    "digital_outbound_requests": {
        "id", "task_id", "channel", "target_im_user_id", "content", "status",
        "requested_by", "decided_by", "decided_at", "decision_note", "sent_at",
        "created_at", "updated_at",
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
    assert len(TABLE_NAMES) == 84
    assert len(MODEL_CLASSES) == 84
    assert len(Base.metadata.tables) == 84
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
        if model.__tablename__ in C_DOMAIN_TABLES:
            # C 域表：应用层维护 updated_at（CTimestampMixin），暂无 ON UPDATE
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
