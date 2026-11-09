"""C 测评域 ORM（`docs/modules/C/C模块接口与数据契约.md` §2/§3）。

9 张表：题库、方案、试卷（固定模式）、分配、逐题选题留痕、答案、结果、复核、复习事项。
- 时间列统一使用公共 `CrossDbUpdatedAtMixin`（`CURRENT_TIMESTAMP` 默认 + 应用层 onupdate），
  不使用公共 `UpdatedAtMixin` 的 `ON UPDATE` DDL，保证 SQLite 测试可直接建表。
- 表间不建外键，沿用一期 BIGINT 列风格；一致性由 service 层保证。
- JSON 一律 `JSONDict`/`JSONList`，可整体赋值与原地修改。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DECIMAL, DateTime, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    CrossDbUpdatedAtMixin,
    JSONDict,
    JSONList,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    str_default,
)


class AssessmentQuestion(PKMixin, CrossDbUpdatedAtMixin, Base):
    """题库题目；`approved` 才可被组卷。"""

    __tablename__ = "assessment_questions"
    __table_args__ = MYSQL_TABLE_OPTIONS

    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dimension_name: Mapped[str] = mapped_column(String(100), nullable=False)
    dimension_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="professional", server_default=str_default("professional")
    )
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default=str_default("medium")
    )
    question_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="single_choice", server_default=str_default("single_choice")
    )
    knowledge_point: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    correct_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_points: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_guideline: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssessmentPlan(PKMixin, CrossDbUpdatedAtMixin, Base):
    """测评方案（按用途 + 岗位），发布时冻结试卷。"""

    __tablename__ = "assessment_plans"
    __table_args__ = MYSQL_TABLE_OPTIONS

    purpose: Mapped[str] = mapped_column(
        String(20), nullable=False, default="recruitment", server_default=str_default("recruitment")
    )
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    job_title: Mapped[str] = mapped_column(String(100), nullable=False)
    job_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dimensions_json: Mapped[list] = mapped_column(JSONList, nullable=False)
    total_question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_score: Mapped[float] = mapped_column(DECIMAL(6, 2), nullable=False)
    difficulty_structure_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="adaptive", server_default=str_default("adaptive")
    )
    strategy_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    bank_snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssessmentPaper(PKMixin, CrossDbUpdatedAtMixin, Base):
    """发布时的固定试卷快照；同一方案版本只有一套。"""

    __tablename__ = "assessment_papers"
    __table_args__ = (
        UniqueConstraint("plan_id", "plan_version", name="uk_paper_plan_version"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    job_title: Mapped[str] = mapped_column(String(100), nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[float] = mapped_column(DECIMAL(6, 2), nullable=False, default=100)
    pass_score: Mapped[float] = mapped_column(DECIMAL(6, 2), nullable=False)
    dimensions_json: Mapped[list] = mapped_column(JSONList, nullable=False)
    questions_json: Mapped[list] = mapped_column(JSONList, nullable=False)
    grading_rule_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssessmentAssignment(PKMixin, CrossDbUpdatedAtMixin, Base):
    """答题任务；`published_result_version` 绑定具体结果版本。"""

    __tablename__ = "assessment_assignments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_assignment_idempotency"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 固定模式绑定试卷快照；自适应模式按需出题，无纸卷
    paper_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="adaptive", server_default=str_default("adaptive")
    )
    strategy_snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    target_question_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="assigned", server_default=str_default("assigned")
    )
    published_result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answers_open: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssessmentSelection(PKMixin, CrossDbUpdatedAtMixin, Base):
    """自适应逐题选题留痕；按出题顺序记录题目快照、选题理由与策略上下文。"""

    __tablename__ = "assessment_selections"
    __table_args__ = (
        UniqueConstraint("assignment_id", "sequence_no", name="uk_selection_sequence"),
        UniqueConstraint("assignment_id", "question_id", name="uk_selection_question"),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_name: Mapped[str] = mapped_column(String(100), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False)
    question_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    selection_reason_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    strategy_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)


class AssessmentAnswer(PKMixin, CrossDbUpdatedAtMixin, Base):
    """逐题答案与评分状态；交卷幂等以 (assignment_id, question_id) 为单位。"""

    __tablename__ = "assessment_answers"
    __table_args__ = (
        UniqueConstraint("assignment_id", "question_id", name="uk_answer_assignment_question"),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    selected_option: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score: Mapped[float | None] = mapped_column(DECIMAL(6, 2), nullable=True)
    grading_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssessmentResult(PKMixin, CrossDbUpdatedAtMixin, Base):
    """版本化结果；修分后新增版本，旧版本保留。"""

    __tablename__ = "assessment_results"
    __table_args__ = (
        UniqueConstraint("assignment_id", "version", name="uk_result_assignment_version"),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_scores_json: Mapped[list] = mapped_column(JSONList, nullable=False)
    total_score: Mapped[float] = mapped_column(DECIMAL(6, 2), nullable=False)
    pass_score: Mapped[float] = mapped_column(DECIMAL(5, 2), nullable=False)
    passed: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    grading_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    # 能力结构分析：雷达数据、维度档位、强项/短板与评级
    ability_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssessmentReview(PKMixin, CrossDbUpdatedAtMixin, Base):
    """人工修分审计：保留原分、理由、修改人与前后快照。"""

    __tablename__ = "assessment_reviews"
    __table_args__ = MYSQL_TABLE_OPTIONS

    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    before_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    after_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)


class AssessmentReviewItem(PKMixin, CrossDbUpdatedAtMixin, Base):
    """复习事项：关联原答题/题目快照，可标记完成。"""

    __tablename__ = "assessment_review_items"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "question_id", "user_id", name="uk_review_assignment_question_user"
        ),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_completed: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    question_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
