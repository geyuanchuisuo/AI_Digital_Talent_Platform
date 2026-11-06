"""ORM 实体统一入口（T-03，13 张表）。

`import app.models` 即把全部表注册到 `app.database.Base.metadata`，
`scripts/init_db.py` 与备份脚本据此建表/导出，不需要各任务自行 import 单表。

⚠️ 表结构已按 `docs/数据库设计.md` v1.2 固定，属公共部分；
   改动前按开发日志 §1.3 需先确认，并同步 Layer 4「MySQL 改表结构」登记。

⚠️ **批次（upload_batch）不是实体表（决策 D-M）**：一次 `POST /resumes/batch`
   只生成一个 ULID 写进 `resume_files.upload_batch_id`，批次汇总靠该字段 GROUP BY
   聚合。**不要新建 `upload_batches` 表或外键**——那会同时打破"一期 13 张表"的
   冻结口径、`init_db.py`/`backup_db.py` 的建表与备份清单，以及下面的 13 断言。
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
)

TABLE_NAMES: tuple[str, ...] = tuple(model.__tablename__ for model in MODEL_CLASSES)  # type: ignore[attr-defined]

assert len(TABLE_NAMES) == 13, f"一期固定 13 张表，实际 {len(TABLE_NAMES)}"
assert len(set(TABLE_NAMES)) == 13, "表名重复"
assert "llm_score_cache" not in TABLE_NAMES, "llm_score_cache 是 D-7 缓办项，一期不建"
assert "upload_batches" not in TABLE_NAMES, (
    "批次不是实体表（决策 D-M）：靠 resume_files.upload_batch_id 聚合，"
    "加表会打破一期 13 张表冻结口径"
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
