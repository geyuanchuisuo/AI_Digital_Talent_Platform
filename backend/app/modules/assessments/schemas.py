"""C 测评域请求 DTO 与字面值校验（一期契约 §3/§4）。

业务级校验（题库充足、权重合计、维度和题量）在 `scoring`/`service` 中抛统一 400；
这里只做结构级约束，避免把业务规则散落到 Pydantic。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DimensionType = Literal["professional", "general"]
Difficulty = Literal["easy", "medium", "hard"]
QuestionType = Literal["single_choice", "short_answer"]
PlanPurpose = Literal["recruitment", "employee"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DimensionIn(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    type: DimensionType = "professional"
    weight: float = Field(gt=0, le=100)


class QuestionCreateRequest(_StrictModel):
    job_id: int | None = None
    job_version: int | None = None
    job_title: str | None = Field(default=None, max_length=100)
    dimension_name: str = Field(min_length=1, max_length=100)
    dimension_type: DimensionType = "professional"
    difficulty: Difficulty = "medium"
    question_type: QuestionType = "single_choice"
    stem: str = Field(min_length=1)
    options: list[str] | None = Field(default=None, max_length=8)
    correct_answer: str | None = None
    reference_points: str | None = None
    scoring_guideline: str | None = None


class QuestionUpdateRequest(_StrictModel):
    job_id: int | None = None
    job_version: int | None = None
    job_title: str | None = Field(default=None, max_length=100)
    dimension_name: str | None = Field(default=None, min_length=1, max_length=100)
    dimension_type: DimensionType | None = None
    difficulty: Difficulty | None = None
    question_type: QuestionType | None = None
    stem: str | None = Field(default=None, min_length=1)
    options: list[str] | None = Field(default=None, max_length=8)
    correct_answer: str | None = None
    reference_points: str | None = None
    scoring_guideline: str | None = None


class PlanCreateRequest(_StrictModel):
    purpose: PlanPurpose = "recruitment"
    job_id: int
    job_title: str = Field(min_length=1, max_length=100)
    job_version: int = Field(default=1, ge=1)
    dimensions: list[DimensionIn] = Field(min_length=1, max_length=20)
    total_question_count: int = Field(ge=1, le=200)
    pass_score: float = Field(ge=0, le=100)
    difficulty_structure: dict[str, float] | None = None


class PlanUpdateRequest(_StrictModel):
    job_title: str | None = Field(default=None, min_length=1, max_length=100)
    job_version: int | None = Field(default=None, ge=1)
    dimensions: list[DimensionIn] | None = Field(default=None, min_length=1, max_length=20)
    total_question_count: int | None = Field(default=None, ge=1, le=200)
    pass_score: float | None = Field(default=None, ge=0, le=100)
    difficulty_structure: dict[str, float] | None = None


class AssignmentCreateRequest(_StrictModel):
    plan_id: int
    user_id: int
    talent_profile_id: int | None = None
    job_id: int | None = None
    job_title: str | None = Field(default=None, max_length=100)
    idempotency_key: str | None = Field(default=None, max_length=64)
    attempt_no: int = Field(default=1, ge=1, le=20)


class AnswerSaveRequest(_StrictModel):
    selected_option: str | None = None
    answer_text: str | None = None


class ManualScoreRequest(_StrictModel):
    score: float = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=500)
