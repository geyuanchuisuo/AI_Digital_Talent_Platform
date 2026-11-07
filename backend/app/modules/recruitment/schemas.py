"""招聘域接口模型（M3-V2.6，T-A60b）。

字段口径：V2.6 F01（人才需求：岗位/需求人数/说明/状态 + 项目/项目群归属 +
优先级 + 期望到岗）；状态与优先级枚举见 models.py，非法流转返回 400。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEMAND_STATUS_LITERAL = Literal["open", "in_progress", "fulfilled", "closed"]
PRIORITY_LITERAL = Literal["high", "medium", "low"]


class ProjectGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class ProjectGroupItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    status: str
    created_at: datetime


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    project_group_id: int | None = None
    description: str | None = None


class ProjectItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_group_id: int | None = None
    name: str
    description: str | None = None
    status: str
    created_at: datetime


class ProjectDemandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_name: str = Field(min_length=1, max_length=120, description="岗位名称")
    headcount: int = Field(ge=1, le=999, description="需求人数")
    project_id: int | None = Field(default=None, description="归属项目")
    project_group_id: int | None = Field(default=None, description="归属项目群")
    job_profile_id: int | None = Field(default=None, description="关联岗位条件")
    expected_start_date: date | None = None
    priority: PRIORITY_LITERAL = "medium"
    remark: str | None = None


class ProjectDemandUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_name: str | None = Field(default=None, min_length=1, max_length=120)
    headcount: int | None = Field(default=None, ge=1, le=999)
    expected_start_date: date | None = None
    priority: PRIORITY_LITERAL | None = None
    remark: str | None = None
    status: DEMAND_STATUS_LITERAL | None = None


class ProjectDemandItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None = None
    project_group_id: int | None = None
    job_profile_id: int | None = None
    position_name: str
    headcount: int
    expected_start_date: date | None = None
    priority: str
    remark: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class PageData(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
