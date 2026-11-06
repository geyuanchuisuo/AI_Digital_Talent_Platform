"""Prompt A（简历结构化提取）的响应契约模型（T-13）。

对应 `docs/LLM提示词设计.md` v1.2 Prompt A 的 JSON Output Schema，也是
`docs/数据库设计.md` v1.2 §4.3 里 `parsed_resumes.structured_json` 的 `ResumeSchema`。
`core/prompts/schemas.py::PROMPT_A_SCHEMA` 是拼进 system prompt 的结构约束，
本文件的 Pydantic 模型是**响应校验**（`chat_json(schema=ResumeSchema)`）。

宽松点（故意为之，避免把模型的小偏差升级成整次失败）：
- `extra="ignore"`：多吐字段直接忽略（文档要求 additionalProperties=false，但多余字段不影响正确性）；
- 各数组字段缺失时按空数组处理：缺 `employments` 会被"经历为空"的语义重试兜住（见 `parse_service`）；
- `skills` / `certificates` 里的非字符串元素**尽量救回**（`{"name": "Python"}` → `"Python"`），
  救不回的丢弃——这两个字段只用于技能归一化与展示，不值得为一条脏数据重试整份简历；
- `birth_year` / `work_years` 接受 `"1996年"` 这类带单位文本，解析不出即 `None`
  （规则层本来就要按 employments 重算年限，见算法 §2）。

⚠️ 字段名与 Prompt A schema **逐字一致**，改名等于改提示词契约，需同步改
`docs/LLM提示词设计.md` 与 `core/prompts/schemas.py`。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 学历六档（数据库 §5.1 唯一口径）；Prompt A 已约束枚举，这里只做兜底放行
DEGREE_VALUES: tuple[str, ...] = ("博士", "硕士", "本科", "大专", "高中中专", "其他")

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_STRING_KEYS = ("name", "skill", "title", "value")


class _Base(BaseModel):
    """统一 `extra="ignore"`：模型多吐字段不致命。"""

    model_config = ConfigDict(extra="ignore")


class BasicInfo(_Base):
    name: str | None = None
    gender: str | None = None
    birth_year: int | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None

    @field_validator("birth_year", mode="before")
    @classmethod
    def _year(cls, value: Any) -> int | None:
        """`1996` / `"1996"` / `"1996年"` 都能收；其余按 None（不编造）。"""
        return _to_int(value)


class EducationItem(_Base):
    school: str | None = None
    major: str | None = None
    degree: str | None = None
    start: str | None = None
    end: str | None = None


class EmploymentItem(_Base):
    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None


class ProjectItem(_Base):
    name: str | None = None
    role: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None


class ResumeSchema(_Base):
    """Prompt A 输出；`structured_json` 直接落本模型的 `model_dump()`。"""

    basic: BasicInfo = Field(default_factory=BasicInfo)
    educations: list[EducationItem] = Field(default_factory=list)
    employments: list[EmploymentItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    expected_title: str | None = None
    work_years: float | None = None

    @field_validator("skills", "certificates", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        return _to_string_list(value)

    @field_validator("work_years", mode="before")
    @classmethod
    def _years(cls, value: Any) -> float | None:
        """`6` / `"6"` / `"6.5 年"` 都能收；解析不出按 None（规则层会用经历区间重算）。"""
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            match = _NUMBER_RE.search(str(value))
            return float(match.group()) if match else None


def _to_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        match = _NUMBER_RE.search(str(value))
        return int(float(match.group())) if match else None


def _to_string_list(value: Any) -> list[str]:
    """把"应该是字符串数组"的字段尽量救回；非数组一律按空。"""
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, dict):
        value = [value]  # 单个对象：交给下面的 `name/skill/title/value` 取文本
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            text = str(item)
        elif isinstance(item, dict):
            text = next(
                (str(item[key]).strip() for key in _STRING_KEYS if item.get(key)), ""
            )
        else:
            text = ""
        if text:
            result.append(text)
    return result


__all__ = [
    "DEGREE_VALUES",
    "BasicInfo",
    "EducationItem",
    "EmploymentItem",
    "ProjectItem",
    "ResumeSchema",
]
