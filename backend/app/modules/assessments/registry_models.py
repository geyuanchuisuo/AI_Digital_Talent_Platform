"""C 模块模型注册清单（只依赖模型层，不导入路由）。

为什么与路由注册分开：`router` → `app.api.deps` → `app.models` 的依赖链，与
「先导入模型清单、后导入 deps」的入口（如 `tests/C/c_harness.py`、init 脚本）
会形成循环导入。模型注册只供建表/结构断言使用，不在此导入任何 router。

路由注册见 `app.modules.assessments.registry.ALL_ROUTERS`。
"""

from __future__ import annotations

from app.modules.assessments.models import (
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentPaper,
    AssessmentPlan,
    AssessmentQuestion,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
    AssessmentSelection,
)
from app.modules.interviews.ai_models import (
    AiInterviewEvent,
    AiInterviewQuestion,
    AiInterviewSession,
)
from app.modules.interviews.models import InterviewFeedback, InterviewRound, InterviewRoundEvent
from app.modules.inventory.models import (
    InventoryDevelopmentPlan,
    InventoryDispute,
    InventoryEvent,
    InventoryGridConfig,
    InventoryReview,
    InventoryTalentPool,
)
from app.modules.training.models import (
    PrejobPhase,
    PrejobPlan,
    PrejobPlanVersion,
    TrainingAttendance,
    TrainingCourse,
    TrainingEnrollment,
    TrainingFeedback,
    TrainingFeedbackTheme,
    TrainingGrowthEvent,
    TrainingLearningEvidence,
    TrainingLearningRecord,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
    TrainingSession,
)

ALL_MODELS: tuple[type, ...] = (
    AssessmentQuestion,
    AssessmentPlan,
    AssessmentPaper,
    AssessmentAssignment,
    AssessmentSelection,
    AssessmentAnswer,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
    InterviewRound,
    InterviewFeedback,
    InterviewRoundEvent,
    AiInterviewSession,
    AiInterviewQuestion,
    AiInterviewEvent,
    TrainingCourse,
    TrainingSession,
    TrainingAttendance,
    TrainingFeedback,
    TrainingFeedbackTheme,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
    TrainingLearningRecord,
    TrainingGrowthEvent,
    TrainingEnrollment,
    TrainingLearningEvidence,
    PrejobPlan,
    PrejobPhase,
    PrejobPlanVersion,
    InventoryReview,
    InventoryTalentPool,
    InventoryDispute,
    InventoryDevelopmentPlan,
    InventoryEvent,
    InventoryGridConfig,
)
