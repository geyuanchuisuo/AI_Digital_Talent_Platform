"""个人工作台汇总（T-D23）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.assessments.models import (
    AssessmentAssignment,
    AssessmentResult,
)
from app.modules.training.models import TrainingPlan

def _require_personal(user) -> None:
    """个人工作台门禁：仅 personal 账号（deny-by-default，与 overview._require_management 对称）。"""
    if getattr(user, "role", "") != "personal":
        raise BizError(403, "仅个人账号可访问个人工作台")


def me_overview(db: Session, user) -> dict[str, Any]:
    """个人工作台三要素：本人待测评 / 培训 / 已公布近期结果。

    权限：仅 personal 账号（_require_personal 服务内门禁）；只读本人行（user_id 绑定）。
    口径：
    - 待测评 = assignment.status in (assigned/in_progress/grading)（未交卷即待测；
      completed/cancelled 不计）；
    - 培训 = training_plans 已正式指派给本人（assigned；draft 是管理侧确认前
      中间态，未确认不向员工展示）——与 T-D22 管理端 draft+assigned 全流程口径
      有意区分，不混用；
    - 近期结果 = assignment.published_result_version 非空的结果行数（成绩未公布
      不展示——PRD 红线）。
    """
    _require_personal(user)
    user_id = user.id

    # 个人工作台仅看本人：待测评=未交卷（assigned/in_progress/grading）
    pending_assignments = int(
        db.execute(
            select(func.count(AssessmentAssignment.id)).where(
                AssessmentAssignment.user_id == user_id,
                AssessmentAssignment.status.in_(("assigned", "in_progress", "grading")),
            )
        ).scalar_one()
    )

    # 培训：仅本人已被正式指派（assigned）；draft 是管理侧确认前中间态，未确认前不向员工呈现
    pending_training = int(
        db.execute(
            select(func.count(TrainingPlan.id)).where(
                TrainingPlan.employee_user_id == user_id,
                TrainingPlan.status == "assigned",
            )
        ).scalar_one()
    )

    # 近期结果：本人 assignment 已公布结果版本（published_result_version 非空）
    recent_results = int(
        db.execute(
            select(func.count(AssessmentResult.id)).where(
                AssessmentResult.assignment_id.in_(
                    select(AssessmentAssignment.id).where(
                        AssessmentAssignment.user_id == user_id,
                        AssessmentAssignment.published_result_version.isnot(None),
                    )
                )
            )
        ).scalar_one()
    )

    return {
        "user_id": user_id,
        "pending_assignments": pending_assignments,
        "pending_training": pending_training,
        "recent_results": recent_results,
        "caliber": {
            "pending_assignments": "本人未交卷测评（assigned/in_progress/grading）",
            "pending_training": "本人培训已正式指派（assigned）",
            "recent_results": "本人已公布结果（成绩公布后才计入）",
        },
    }
