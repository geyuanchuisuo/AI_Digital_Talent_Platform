"""C 一期扩表：知识点、面试申请关联、成长来源、培训报名/学习证据、F10 盘点、AI 面试间。

Revision ID: C_0003_phase1_expansion
Revises: C_0002_adaptive_assessment
Create Date: 2026-11-07

依赖 A 维护的迁移链；本地无 MySQL 环境，迁移链未实跑（见 docs/updates/C）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "C_0003_phase1_expansion"
down_revision: Union[str, None] = "C_0002_adaptive_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    # 测评题目知识点
    op.add_column("assessment_questions", sa.Column("knowledge_point", sa.String(length=200), nullable=True))

    # 面试轮次关联招聘申请
    op.add_column("interview_rounds", sa.Column("application_id", sa.Integer(), nullable=True))

    # 成长事件来源类型
    op.add_column(
        "training_growth_events",
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="training"),
    )

    # 学习记录：正式完成与证据分
    op.add_column(
        "training_learning_records",
        sa.Column("formal_completed", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "training_learning_records",
        sa.Column("evidence_score", sa.Float(), nullable=False, server_default="0"),
    )

    # 培训报名
    op.create_table(
        "training_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="elective"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("course_id", "employee_user_id", name="uk_enrollment_course_employee"),
        **TABLE_OPTIONS,
    )

    # 学习证据
    op.create_table(
        "training_learning_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learning_record_id", sa.Integer(), nullable=False),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("verified_by", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )

    # F10 盘点
    op.create_table(
        "inventory_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("cycle_name", sa.String(length=50), nullable=False),
        sa.Column("review_type", sa.String(length=20), nullable=False, server_default="annual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("performance_score", sa.Integer(), nullable=True),
        sa.Column("performance_source", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("potential_manager_score", sa.Integer(), nullable=True),
        sa.Column("potential_hr_score", sa.Integer(), nullable=True),
        sa.Column("grid_cell", sa.String(length=10), nullable=True),
        sa.Column("reviewer_manager_id", sa.Integer(), nullable=True),
        sa.Column("reviewer_hr_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "inventory_talent_pools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("pool_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="nominated"),
        sa.Column("nominated_by", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "inventory_disputes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "inventory_development_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("review_id", name="uk_idp_review"),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "inventory_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )

    # AI 面试间
    op.create_table(
        "ai_interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_user_id", sa.Integer(), nullable=True),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("overall_feedback", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "ai_interview_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(length=30), nullable=False, server_default="structured"),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("ai_feedback_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("session_id", "sequence_no", name="uk_ai_q_session_seq"),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "ai_interview_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("ai_interview_events")
    op.drop_table("ai_interview_questions")
    op.drop_table("ai_interview_sessions")
    op.drop_table("inventory_events")
    op.drop_table("inventory_development_plans")
    op.drop_table("inventory_disputes")
    op.drop_table("inventory_talent_pools")
    op.drop_table("inventory_reviews")
    op.drop_table("training_learning_evidence")
    op.drop_table("training_enrollments")
    op.drop_column("training_learning_records", "evidence_score")
    op.drop_column("training_learning_records", "formal_completed")
    op.drop_column("training_growth_events", "source_type")
    op.drop_column("interview_rounds", "application_id")
    op.drop_column("assessment_questions", "knowledge_point")
