"""c_module_21_tables: 21 C-domain tables (assessments 8 / interviews 3 / training 10; ruling C-04).

Revision ID: C_0001_c_module
Revises: A_0001_baseline
Create Date: 2026-11-07

Creates the 21 models C already merged into master (previously blocked by the
13-table assertion so none existed). Schema source of truth:
backend/app/modules/*/models.py; C submits future `C_*.py` migrations on top.
"""
from __future__ import annotations

from alembic import op

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
    from app.models import Base  # importing registers all 34 tables (13 public + 21 C)

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
