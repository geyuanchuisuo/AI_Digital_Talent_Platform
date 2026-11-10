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
    # A11：退回补充时须能补齐组织/岗位归属，否则「必填项完整」永远无法达成。
    project_id: int | None = None
    project_group_id: int | None = None
    job_profile_id: int | None = None
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
    # A11：审批状态与版本（迁移前历史行由 server_default 补为 draft/1）。
    approval_status: str = "draft"
    approval_version: int = 1
    approved_version: int | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None
    #: 计算字段，由 router 用 `demand_is_matchable` 覆写，不从 ORM 直接取。
    matchable: bool = False
    created_at: datetime
    updated_at: datetime


class DemandSubmitRequest(BaseModel):
    """提交审批：可选补充说明（不写入需求业务字段）。"""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=255)


class DemandDecisionRequest(BaseModel):
    """审批决定：approve / reject / return（退回补充）。拒绝与退回必填原因。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "return"]
    reason: str | None = Field(default=None, max_length=255)


class DemandWithdrawRequest(BaseModel):
    """撤回需求：必填原因，留痕可追溯。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=255)


class DemandApprovalRecordItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    demand_id: int
    demand_version: int
    action: str
    from_status: str
    to_status: str
    actor_user_id: int
    actor_roles: str
    reason: str | None = None
    snapshot_json: dict | None = None
    created_at: datetime


class PageData(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
