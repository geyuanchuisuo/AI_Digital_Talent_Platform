"""ORM 实体统一入口（T-03 + 裁决 C-04，35 张表 = 13 公共 + 22 C 模块）。

`import app.models` 即把全部表注册到 `app.database.Base.metadata`，
`scripts/init_db.py` 与备份脚本据此建表/导出，不需要各任务自行 import 单表。

⚠️ 表结构属公共部分；改动前需确认，并同步迁移链
   `backend/migrations/versions/`（`A_*.py` 公共/招聘 + `C_*.py` 测评/面试/培训，
   命名约定见 U-05 裁决）与 `backend/tests/test_models_structure.py` 的对照清单。

⚠️ **批次（upload_batch）不是实体表（决策 D-M）**：一次 `POST /resumes/batch`
   只生成一个 ULID 写进 `resume_files.upload_batch_id`，批次汇总靠该字段 GROUP BY
   聚合。**不要新建 `upload_batches` 表或外键**——那会同时打破
   `init_db.py`/`backup_db.py` 的建表与备份清单，以及下面的排除断言。

C 模块的 21 个模型直接从三域 `models.py` 导入（C 请求 20261106-A §5.1）。
⚠️ 不经 `registry.py` 的 `ALL_MODELS`：registry 同时导入 router（router → api.deps
→ app.models），与 `app.api.deps` → `app.models` 的加载链在部分入口（如 C 的
c_harness 先 import deps）会成循环导入；模型注册只取 models.py，路由仍由
`main.py` 用 registry 的 `ALL_ROUTERS` 注册（该入口 deps 先完成，无环）。
新增跨角色模型时：模型直接 import 各域 models.py；若 registry 仍捆绑 router，
建议 C 拆分为模型/路由两个注册表（已登记请求）。
"""

from __future__ import annotations

from app.database import Base
from app.models.job import (
    JobProfile,
    MatchBatch,
    MatchResult,
    ScreenBatch,
    ScreenResult,
)
from app.models.resume import ParseTask, ParsedResume, ResumeFile
from app.models.talent import TalentDuplicate, TalentProfile, TalentTag
from app.models.user import SysSetting, User
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
from app.modules.interviews.models import (
    InterviewFeedback,
    InterviewRound,
    InterviewRoundEvent,
)
from app.modules.training.models import (
    TrainingAttendance,
    TrainingCourse,
    TrainingFeedback,
    TrainingFeedbackTheme,
    TrainingGrowthEvent,
    TrainingLearningRecord,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
    TrainingSession,
)

MODEL_CLASSES: tuple[type[Base], ...] = (
    User,
    SysSetting,
    ResumeFile,
    ParseTask,
    ParsedResume,
    TalentProfile,
    TalentTag,
    TalentDuplicate,
    JobProfile,
    ScreenBatch,
    ScreenResult,
    MatchBatch,
    MatchResult,
    AssessmentQuestion,
    AssessmentPlan,
    AssessmentPaper,
    AssessmentAssignment,
    AssessmentAnswer,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
    AssessmentSelection,
    InterviewRound,
    InterviewFeedback,
    InterviewRoundEvent,
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
)

TABLE_NAMES: tuple[str, ...] = tuple(model.__tablename__ for model in MODEL_CLASSES)  # type: ignore[attr-defined]

assert len(TABLE_NAMES) == 35, f"一期固定 35 张表（13 公共 + 22 C），实际 {len(TABLE_NAMES)}"
assert len(set(TABLE_NAMES)) == 35, "表名重复"
assert "llm_score_cache" not in TABLE_NAMES, "llm_score_cache 是 D-7 缓办项，一期不建"
assert "upload_batches" not in TABLE_NAMES, (
    "批次不是实体表（决策 D-M）：靠 resume_files.upload_batch_id 聚合，"
    "加表会打破建表/备份清单口径"
)

__all__ = [
    "Base",
    "MODEL_CLASSES",
    "TABLE_NAMES",
    "User",
    "SysSetting",
    "ResumeFile",
    "ParseTask",
    "ParsedResume",
    "TalentProfile",
    "TalentTag",
    "TalentDuplicate",
    "JobProfile",
    "ScreenBatch",
    "ScreenResult",
    "MatchBatch",
    "MatchResult",
]
