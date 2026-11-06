"""C 模块接入清单：A 只需引用本文件即可注册 C 的模型与路由。

用法（由 A 在 `backend/app/models/__init__.py` 与 `backend/app/main.py` 中执行）：

```python
from app.modules.assessments.registry import ALL_MODELS, ALL_ROUTERS as C_ROUTERS
for router in C_ROUTERS:
    app.include_router(router)
```

不要在 C 的 `models.py` 中反向导入公共注册表，避免与 A 的初始化顺序耦合。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.assessments.models import (
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentPaper,
    AssessmentPlan,
    AssessmentQuestion,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
)
from app.modules.assessments.router import router as assessments_router
from app.modules.interviews.models import InterviewFeedback, InterviewRound, InterviewRoundEvent
from app.modules.interviews.router import router as interviews_router
from app.modules.training.models import (
    TrainingCourse,
    TrainingGrowthEvent,
    TrainingLearningRecord,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
)
from app.modules.training.router import router as training_router

ALL_MODELS: tuple[type, ...] = (
    AssessmentQuestion,
    AssessmentPlan,
    AssessmentPaper,
    AssessmentAssignment,
    AssessmentAnswer,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
    InterviewRound,
    InterviewFeedback,
    InterviewRoundEvent,
    TrainingCourse,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
    TrainingLearningRecord,
    TrainingGrowthEvent,
)

ALL_ROUTERS: tuple[APIRouter, ...] = (
    assessments_router,
    interviews_router,
    training_router,
)
