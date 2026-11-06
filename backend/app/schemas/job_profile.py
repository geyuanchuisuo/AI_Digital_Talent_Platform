"""岗位条件 DTO（T-22，`docs/接口设计.md` v1.3 §6、数据库 §6.1）。

校验统一 400（接口 §6）：weights 总和=100、pass>pending、min_degree 逐字等于
六档字面值之一（**「高中中专」**，D-G）；技能归一化后存储（D-N：复用
`core/normalize.normalize_skills`，别名/大小写/修饰后缀与画像侧同一套）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WEIGHT_KEYS = ("skills", "experience", "education", "others")
HARD_RULE_KEYS = ("degree", "work_years", "must_skills")


class HardRuleSwitches(BaseModel):
    """硬门槛开关（数据库 §6.1：缺省视为开启）。"""

    model_config = ConfigDict(extra="forbid")

    degree: bool = True
    work_years: bool = True
    must_skills: bool = True


class Weights(BaseModel):
    """四维权重，总和必须 = 100（service 校验并 400）。"""

    model_config = ConfigDict(extra="forbid")

    skills: float
    experience: float
    education: float
    others: float

    def total(self) -> float:
        return round(self.skills + self.experience + self.education + self.others, 6)


class JobProfileWriteRequest(BaseModel):
    """POST / PUT 共用请求体（PUT 为全量更新，校验同 POST）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    jd_text: str = Field(min_length=1)
    requirements_text: str = Field(min_length=1)
    min_degree: str | None = Field(default=None, max_length=20)
    min_work_years: float | None = Field(default=None, ge=0, le=80)
    must_skills: list[str] = Field(default_factory=list, max_length=50)
    bonus_skills: list[str] = Field(default_factory=list, max_length=50)
    hard_rule_switches: HardRuleSwitches = Field(default_factory=HardRuleSwitches)
    pass_threshold: float = Field(default=75, ge=0, le=100)
    pending_threshold: float = Field(default=60, ge=0, le=100)
    weights: Weights

    def hard_rule_dict(self) -> dict[str, bool]:
        return {"degree": self.hard_rule_switches.degree, "work_years": self.hard_rule_switches.work_years,
                "must_skills": self.hard_rule_switches.must_skills}


class JobProfileOut(BaseModel):
    """`GET /job-profiles/{id}` / POST / PUT 响应：完整配置。"""

    id: int
    title: str
    department: str | None = None
    jd_text: str
    requirements_text: str
    min_degree: str | None = None
    min_work_years: float | None = None
    must_skills: list[str] = Field(default_factory=list)
    bonus_skills: list[str] = Field(default_factory=list)
    hard_rule_switches: dict[str, bool]
    pass_threshold: float
    pending_threshold: float
    weights: dict[str, Any]
    version: int
    is_active: bool
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


class JobProfileListItem(BaseModel):
    """`GET /job-profiles` 的 items 项（**不含 jd/requirements 全文**，接口 §6）。"""

    id: int
    title: str
    department: str | None = None
    min_degree: str | None = None
    min_work_years: float | None = None
    must_skills: list[str] = Field(default_factory=list)
    bonus_skills: list[str] = Field(default_factory=list)
    version: int
    is_active: bool
    updated_at: datetime | None = None


class JobProfileDeleteData(BaseModel):
    """`DELETE /job-profiles/{id}` 响应 data。"""

    deleted: bool
    soft_deleted: bool = Field(description="有批次引用 → 软删（is_active=0）；无引用 → 物理删除")
