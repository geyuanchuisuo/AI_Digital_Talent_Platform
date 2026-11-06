"""画像生成的 LLM 契约模型（T-16）。

对应 `docs/LLM提示词设计.md` v1.2 Prompt B 的 JSON Output Schema。
`core/prompts/schemas.py::PROMPT_B_SCHEMA` 是拼进 system prompt 的**结构约束**，
本文件的 Pydantic 模型是**响应校验**（`chat_json(schema=...)`）。

两个宽松点（故意为之，避免把模型的小偏差升级成失败）：
- `extra="ignore"`：模型多吐字段直接忽略（文档要求 additionalProperties=false，但多字段不影响正确性）；
- 五维 `score` 不设 0–100 边界：越界值由 `profile_service` clamp（算法 §6.2「0–100（clamp）」），
  而不是让整次调用失败重试。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TagType = Literal["skill", "industry", "level", "general"]

#: 五维键名固定，不可改名（算法 §6.2 / Prompt B）
ABILITY_KEYS: tuple[str, ...] = (
    "professional",
    "project_experience",
    "education",
    "stability",
    "growth_potential",
)

BASIS_MAX_CHARS = 60


class TagItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    type: TagType = "general"


class AbilityDim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int
    basis: str = ""


class AbilityScores(BaseModel):
    """五维能力；**缺任何一维都算输出不合格**（交由 chat_json 重试）。"""

    model_config = ConfigDict(extra="ignore")

    professional: AbilityDim
    project_experience: AbilityDim
    education: AbilityDim
    stability: AbilityDim
    growth_potential: AbilityDim


class ProfileLLMOutput(BaseModel):
    """Prompt B 响应；`tags` 不设 min_length——不足 10 个由服务层重试 1 次后接受（算法 §6.1）。"""

    model_config = ConfigDict(extra="ignore")

    tags: list[TagItem] = Field(default_factory=list)
    ability_scores: AbilityScores
