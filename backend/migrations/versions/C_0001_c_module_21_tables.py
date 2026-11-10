"""c_module_21_tables: 21 C-domain tables (assessments 8 / interviews 3 / training 10; ruling C-04).

Revision ID: C_0001_c_module
Revises: A_0001_baseline
Create Date: 2026-11-07

Creates the 21 models C already merged into master (previously blocked by the
13-table assertion so none existed). Schema source of truth:
backend/app/modules/*/models.py; C submits future `C_*.py` migrations on top.

修复（A, 2026-11-10, REQ-C-20261109-121506）：空库升级时 `interview_rounds` /
`ai_interview_sessions` 的当前模型元数据已带 `ForeignKey("applications.id")`
（由 C_0004 引入），但 `applications` 要到 `A_0005_m3_recruitment` 才创建，
MySQL 报 1824 Failed to open the referenced table。建表期间临时摘除「目标表此刻
尚不存在」的外键，用完恢复，最终外键仍由 `C_0004` 在 applications 建好后补加。
不改表名/列/类型、不改 down_revision（不产生分支或双头）、不影响已升级的库。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import ForeignKeyConstraint

revision = "C_0001_c_module"
down_revision = "A_0001_baseline"
branch_labels = None
depends_on = None

C_TABLES = (
    # assessments (8)
    "assessment_questions",
    "assessment_plans",
    "assessment_papers",
    "assessment_assignments",
    "assessment_answers",
    "assessment_results",
    "assessment_reviews",
    "assessment_review_items",
    # interviews (3)
    "interview_rounds",
    "interview_feedback",
    "interview_round_events",
    # training (10)
    "training_courses",
    "training_sessions",
    "training_attendances",
    "training_feedbacks",
    "training_feedback_themes",
    "training_plans",
    "training_plan_items",
    "training_plan_versions",
    "training_learning_records",
    "training_growth_events",
)


def upgrade() -> None:
    from app.models import Base  # importing registers all tables

    bind = op.get_bind()
    tables = [Base.metadata.tables[t] for t in C_TABLES]
    existing = set(sa.inspect(bind).get_table_names())
    known = {table.name for table in tables}

    # 模型元数据是「当前」结构，含后来 C_0004 才补的 application_id 外键；
    # 而 applications 表在本迁移执行点尚未创建。仅剥离目标表此刻不存在的外键，
    # 不改动 Column 定义，且在 create_all 后立即恢复，避免污染进程内共享的 metadata
    # （后续迁移、测试仍应看到完整模型定义）。
    deferred: list[tuple[sa.Table, ForeignKeyConstraint]] = []
    for table in tables:
        for constraint in list(table.constraints):
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {
                element.target_fullname.split(".")[0]
                for element in constraint.elements
                if element.target_fullname
            }
            if targets - existing - known:
                table.constraints.discard(constraint)
                deferred.append((table, constraint))
    try:
        Base.metadata.create_all(bind, tables=tables, checkfirst=True)
    finally:
        for table, constraint in deferred:
            table.constraints.add(constraint)


def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(C_TABLES)],
    )
