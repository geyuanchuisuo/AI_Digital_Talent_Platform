"""C 培训成长域 ORM（契约 §2）：课程、计划、计划项、计划版本、学习记录、成长事件。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import CrossDbUpdatedAtMixin, JSONDict, JSONList, MYSQL_TABLE_OPTIONS, PKMixin, str_default


class TrainingCourse(PKMixin, CrossDbUpdatedAtMixin, Base):
    """真实课程目录；材料仅接受 http/https 链接，不伪造资源。"""

    __tablename__ = "training_courses"
    __table_args__ = MYSQL_TABLE_OPTIONS

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_url: Mapped[str] = mapped_column(String(500), nullable=False)
    dimension_names_json: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    # 名额上限：NULL 表示不限；报名 pending 占位、approved 占用，超出即转候补
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_note_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # C06 报名审批链配置：none=免审批 / manager=直属经理审批 / admin=培训管理员审批
    approval_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="admin", server_default=str_default("admin")
    )
    # 涉及费用或占用工作时间：经理审批后仍须培训管理员复核
    has_cost: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    occupies_work_time: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    # C08 正式完成条件（按课程配置）：{"min_evidence_score": float, "required_evidence_types": [str]}
    # 未配置时回退平台默认阈值；正式完成只按本课程条件判定，不用全局固定值
    completion_rules_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingSession(PKMixin, CrossDbUpdatedAtMixin, Base):
    """课程期次：反馈窗口与出勤资格的唯一起点。"""

    __tablename__ = "training_sessions"
    __table_args__ = MYSQL_TABLE_OPTIONS

    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instructor_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingAttendance(PKMixin, CrossDbUpdatedAtMixin, Base):
    """实际出勤记录；只有在此登记过的员工才具备评价资格。"""

    __tablename__ = "training_attendances"
    __table_args__ = (
        UniqueConstraint("session_id", "employee_user_id", name="uk_attendance_session_employee"),
        MYSQL_TABLE_OPTIONS,
    )

    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class TrainingFeedback(PKMixin, CrossDbUpdatedAtMixin, Base):
    """员工对同一课程跨期最多一份有效反馈；文字意见不再对外返回原文。"""

    __tablename__ = "training_feedbacks"
    __table_args__ = (
        UniqueConstraint("course_id", "employee_user_id", name="uk_feedback_course_employee"),
        MYSQL_TABLE_OPTIONS,
    )

    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    instructor_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="submitted", server_default=str_default("submitted")
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TrainingFeedbackTheme(PKMixin, CrossDbUpdatedAtMixin, Base):
    """模型从匿名文字意见归纳的主题；只保存达到最小样本门槛的主题。"""

    __tablename__ = "training_feedback_themes"
    __table_args__ = (
        UniqueConstraint("course_id", "theme", name="uk_feedback_theme_course"),
        MYSQL_TABLE_OPTIONS,
    )

    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    theme: Mapped[str] = mapped_column(String(100), nullable=False)
    feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class TrainingPlan(PKMixin, CrossDbUpdatedAtMixin, Base):
    __tablename__ = "training_plans"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default=str_default("manual")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    input_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    gap_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingPlanItem(PKMixin, CrossDbUpdatedAtMixin, Base):
    """计划项绑定当时的课程快照，后续改课程不回写历史版本。"""

    __tablename__ = "training_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "plan_version", "course_id", name="uk_plan_version_course"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    dimension_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 必修课程到期日：用于到期提醒 / 逾期判定 / 延长期限（F09）
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class TrainingPlanVersion(PKMixin, CrossDbUpdatedAtMixin, Base):
    __tablename__ = "training_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uk_plan_version"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingLearningRecord(PKMixin, CrossDbUpdatedAtMixin, Base):
    """员工自报学习状态；完成不代表验证学时。"""

    __tablename__ = "training_learning_records"
    __table_args__ = (
        UniqueConstraint("plan_item_id", "employee_user_id", name="uk_learning_item_employee"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default=str_default("not_started")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_self_reported: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    formal_completed: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    evidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))


class TrainingGrowthEvent(PKMixin, CrossDbUpdatedAtMixin, Base):
    """追加式成长事件：来源 assessment/interview/training/hr_ability。"""

    __tablename__ = "training_growth_events"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="training", server_default=str_default("training")
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)


class TrainingEnrollment(PKMixin, CrossDbUpdatedAtMixin, Base):
    """员工课程报名：必修/选修，审批占位与候补队列，24h 超时释放。"""

    __tablename__ = "training_enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "employee_user_id", name="uk_enrollment_course_employee"),
        MYSQL_TABLE_OPTIONS,
    )

    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="elective", server_default=str_default("elective")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # C06 直属经理审批留痕（manager 模式下先于管理员复核）；最终决定仍记 decided_by/at
    manager_decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manager_decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainingLearningEvidence(PKMixin, CrossDbUpdatedAtMixin, Base):
    """有效学习证据（防挂机）：分数与验证人只能由服务端可信校验产生。

    `evidence_key` 指向服务端可信来源（出勤记录/测评结果/学习会话 nonce），
    同一条记录内唯一，用于阻止同一来源重复计入或凭证重放。
    """

    __tablename__ = "training_learning_evidence"
    __table_args__ = (
        UniqueConstraint("learning_record_id", "evidence_key", name="uk_evidence_record_key"),
        MYSQL_TABLE_OPTIONS,
    )

    learning_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    verified_by: Mapped[str | None] = mapped_column(String(30), nullable=True)


class TrainingStudySession(PKMixin, CrossDbUpdatedAtMixin, Base):
    """服务端学习会话（C08 防挂机）：有效学时只累计「有交互心跳」的区间。

    签发时建会话；员工学习期间须周期性发送心跳，服务端按相邻心跳间隔累计
    `active_minutes`。仅签发后静默等待（无心跳）不产生有效学时，不能兑换学习证据。
    `nonce` 与签名凭证一一对应，结算后置 `consumed` 防重放。
    """

    __tablename__ = "training_study_sessions"
    __table_args__ = (
        UniqueConstraint("nonce", name="uk_study_session_nonce"),
        MYSQL_TABLE_OPTIONS,
    )

    learning_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    active_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    consumed: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )


class PrejobPlan(PKMixin, CrossDbUpdatedAtMixin, Base):
    """岗前培养计划：面向新入职/待上岗人员的分阶段（目标、阶段、课程、导师）可编辑计划。

    与在职培训课程体系分离；正式培训课程分配仍限在职员工，本计划不自动报名。
    """

    __tablename__ = "prejob_plans"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    input_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PrejobPhase(PKMixin, CrossDbUpdatedAtMixin, Base):
    """岗前计划的阶段快照：绑定当时课程，含导师与阶段目标；改课程不回写历史版本。"""

    __tablename__ = "prejob_phases"
    __table_args__ = (
        UniqueConstraint("plan_id", "plan_version", "seq", name="uk_prejob_phase_order"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    mentor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    courses_json: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )


class PrejobPlanVersion(PKMixin, CrossDbUpdatedAtMixin, Base):
    """岗前计划版本快照：修订时保留历史，供追溯。"""

    __tablename__ = "prejob_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uk_prejob_plan_version"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CapabilityRating(PKMixin, CrossDbUpdatedAtMixin, Base):
    """正式能力评级（按维度，追加版本，历史不覆盖）。

    仅由经 HR 审核通过的能力评级变更生成新版本；课程完成、AI 结果不得自动改写本表。
    """

    __tablename__ = "capability_ratings"
    __table_args__ = (
        UniqueConstraint(
            "employee_user_id", "talent_profile_id", "dimension_name", "version",
            name="uk_capability_rating_dim_version",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dimension_name: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    effective: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text("1"))
    source_change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    basis_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CapabilityRatingChange(PKMixin, CrossDbUpdatedAtMixin, Base):
    """能力评级变更申请链：实践成果证据 + 经理评价 → HR 审核；通过后生成新评级版本。"""

    __tablename__ = "capability_rating_changes"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dimension_name: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    manager_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manager_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending_hr", server_default=str_default("pending_hr")
    )
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

