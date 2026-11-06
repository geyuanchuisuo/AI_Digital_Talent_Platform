"""L1 单元测试 · 13 张表结构逐字段对照 `docs/数据库设计.md` v1.2（T-03 验收点）。

不触真实 MySQL：用 MySQL dialect 编译 DDL + 检查 metadata，纯内存执行。
期望列名/约束名在下方硬编码，来源为数据库设计 v1.2 各表小节，不反向从模型推导。
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

# 逐表期望列名（含 id / created_at / updated_at），来源：数据库设计 v1.2 §3~§6
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "users": {
        "id", "username", "password_hash", "display_name", "role", "is_active",
        "last_login_at", "created_at", "updated_at",
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
        "deleted_by", "updated_by", "created_at", "updated_at",
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
    },
    "screen_results": {
        "id", "screen_batch_id", "job_profile_id", "talent_profile_id", "hard_fail",
        "hard_fail_reason", "score", "verdict", "manual_verdict",
        "manual_verdict_by", "manual_verdict_at", "reason_summary", "hits_json",
        "misses_json", "created_at",
    },
    "match_batches": {
        "id", "job_profile_id", "job_profile_version", "config_snapshot_json",
        "talent_scope_json", "status", "progress", "total", "hard_failed",
        "started_at", "heartbeat_at", "error", "retry_count", "vector_degraded",
        "created_by", "created_at", "finished_at",
    },
    "match_results": {
        "id", "match_batch_id", "talent_profile_id", "score", "hard_fail",
        "hard_fail_reason", "dimension_scores_json", "hits_json", "misses_json",
        "reason", "created_at",
    },
}

EXPECTED_UNIQUE: dict[str, set[str]] = {
    "users": {"uk_username"},
    "sys_settings": {"uk_setting_key"},
    "resume_files": {"uk_file_hash"},
    "parse_tasks": {"uk_resume_file"},
    "parsed_resumes": {"uk_parsed_resume_file"},
    "talent_tags": {"uk_profile_tag"},
    "talent_duplicates": {"uk_parsed_resume"},
    "screen_results": {"uk_screen_batch_talent"},
    "match_results": {"uk_match_batch_talent"},
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
}


def ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def test_exactly_13_tables_and_no_phase2_table():
    assert len(TABLE_NAMES) == 13
    assert len(MODEL_CLASSES) == 13
    assert len(Base.metadata.tables) == 13
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
    """§1 总体约定：BIGINT UNSIGNED AI 主键 + created_at DEFAULT CURRENT_TIMESTAMP。"""
    table = model.__table__
    pk = list(table.primary_key.columns)
    assert len(pk) == 1 and pk[0].name == "id"
    assert pk[0].autoincrement is True
    text = ddl(model)
    assert "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in text, model.__tablename__
    assert "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in text
    if "updated_at" in EXPECTED_COLUMNS[model.__tablename__]:
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
    assert unique_cols("sys_settings", "uk_setting_key") == ("setting_key",)
    assert unique_cols("resume_files", "uk_file_hash") == ("file_hash",)
    assert unique_cols("parse_tasks", "uk_resume_file") == ("resume_file_id",)
    assert unique_cols("parsed_resumes", "uk_parsed_resume_file") == ("resume_file_id",)
    assert unique_cols("talent_tags", "uk_profile_tag") == ("talent_profile_id", "tag")
    assert unique_cols("talent_duplicates", "uk_parsed_resume") == ("parsed_resume_id",)
    assert unique_cols("screen_results", "uk_screen_batch_talent") == (
        "screen_batch_id", "talent_profile_id",
    )
    assert unique_cols("match_results", "uk_match_batch_talent") == (
        "match_batch_id", "talent_profile_id",
    )


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
    """§1：不建物理外键，一致性由 service 层同事务保证。"""
    for table in Base.metadata.tables.values():
        assert not table.foreign_keys, f"{table.name} 不应有物理外键"
