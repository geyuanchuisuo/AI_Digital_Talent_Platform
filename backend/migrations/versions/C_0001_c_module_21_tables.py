"""c_module_21_tables：C 域 21 表（测评 8 / 人工面试 3 / 培训 10，裁决 C-04）

Revision ID: C_0001_c_module
Revises: A_0001_baseline
Create Date: 2026-11-07

把 C 已合入 master 的 21 个模型落成表（此前 13 表断言未注册、一张不建）。
表结构以 backend/app/modules/*/models.py 为准；C 后续改表用 `C_*.py` 增量迁移。
"""
from __future__ import annotations

from alembic import op

revision = "C_0001_c_module"
down_revision = "A_0001_baseline"
branch_labels = None
depends_on = None

C_TABLES = (
    # 测评（8）
    "assessment_questions",
    "assessment_plans",
    "assessment_papers",
    "assessment_assignments",
    "assessment_answers",
    "assessment_results",
    "assessment_reviews",
    "assessment_review_items",
    # 人工面试（3）
    "interview_rounds",
    "interview_feedback",
    "interview_round_events",
    # 培训（10）
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
    from app.models import Base  # 导入即注册全部 34 表（公共 13 + C 21）

    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[t] for t in C_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind,
        tables=[Base.metadata.tables[t] for t in reversed(C_TABLES)],
    )
