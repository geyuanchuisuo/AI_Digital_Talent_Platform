"""人才画像与查重 DTO（T-17，`docs/接口设计.md` v1.3 §5）。

字段名与接口文档逐字一致，前端可直接据此定义 TS 类型。
`matched_value` / `parsed_resume.phone` **均为脱敏值**——库里存的就是脱敏后的
`138****0000`，不是前端再处理一遍（数据库 §5.3）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfileSummary(BaseModel):
    """查重列表里的画像摘要（接口 §5 items.profile_a / profile_b）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    current_title: str | None = None
    created_at: datetime


class ParsedResumeSummary(BaseModel):
    """触发查重的那份解析结果摘要；`phone` 已脱敏。"""

    id: int
    filename: str | None = Field(default=None, description="原始文件名；文件行缺失时为 null")
    name: str | None = None
    phone: str | None = Field(default=None, description="脱敏手机号，如 138****0000")


class DuplicateDetection(BaseModel):
    policy_version: str
    confidence: Literal['high', 'medium']
    score: int = Field(ge=0, le=100, description='规则分，非同人概率')
    matched_fields: list[str]
    conflict_fields: list[str]
    block_reasons: list[str]
    candidate_count: int
    automatic: bool


class DuplicateItem(BaseModel):
    """`GET /talents/duplicates` 的 items 项。"""

    id: int
    reason: str = Field(description="phone_match / email_match / name_phone_match")
    matched_value: str | None = Field(default=None, description="命中的规范化值（已脱敏）")
    status: str = Field(description="pending / merged / kept")
    profile_a: ProfileSummary | None = Field(default=None, description="既有画像（merge 的 master）")
    profile_b: ProfileSummary | None = Field(default=None, description="仅 keep 后才有值")
    parsed_resume: ParsedResumeSummary | None = None
    created_at: datetime
    detection: DuplicateDetection | None = None


class DuplicateResolveRequest(BaseModel):
    """`POST /talents/duplicates/{id}/resolve` 请求体。"""

    action: Literal["merge", "keep"]
    master_profile_id: int | None = Field(
        default=None, description="可省略；一期只接受等于 profile_a_id（传其他值 400）"
    )
    resolution_note: str | None = Field(default=None, max_length=255)


class MergedProfile(BaseModel):
    """merge 响应里的合并后摘要。"""

    id: int
    name: str
    current_title: str | None = None
    degree: str | None = None
    work_years: float | None = None
    city: str | None = None
    skills: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    vector_status: str
    updated_at: datetime | None = None


class MergeResolveData(BaseModel):
    """merge（HTTP 200）。向量失败时 `vector_status="failed"`，其余字段照常。"""

    duplicate_id: int
    status: str = Field(description="固定 merged")
    master_profile_id: int
    profile: MergedProfile
    vector_status: str = Field(description="synced / failed")
    task_id: int | None = None
    task_status: str | None = Field(default=None, description="固定 success")


class KeepResolveData(BaseModel):
    """keep（HTTP 202）：同步只建空画像，标签/能力/向量由 runner 补齐。"""

    duplicate_id: int
    status: str = Field(description="固定 kept")
    talent_profile_id: int
    task_id: int | None = None
    task_status: str | None = Field(default=None, description="固定 pending")
    stage: str | None = Field(default="profile")


# =========================================================================== #
# T-18 画像 CRUD（接口 §5：GET/PATCH/DELETE/restore/revector/resume-file）
# =========================================================================== #

#: 画像标签取值白名单（数据库 §5.2）；tag_type 非法按「类型非法 400」处理
TAG_TYPE_VALUES: tuple[str, ...] = ("skill", "industry", "level", "general")


class TagEntry(BaseModel):
    """PATCH `tags` 的对象形态：`{"tag": "python", "tag_type": "skill"}`。

    `tags` 同时接受纯字符串（等价于只给 tag 名，type 沿用既有行/回落 general），
    前端编辑器两种形态都能发。
    """

    tag: str = Field(min_length=1, max_length=100)
    tag_type: str | None = Field(default=None, description="缺省沿用既有行，否则 general")


class AbilityDimIn(BaseModel):
    """PATCH `ability_scores` 的单维入参；score 0–100 之外由 service 归一（与 LLM 路径一致）。"""

    score: int
    basis: str = Field(default="", max_length=200)


class TagProof(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=150)
    candidate_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    catalog_version: str = Field(min_length=1, max_length=64)
    profile_version: int = Field(ge=1)
    reason: str = Field(default='', max_length=500)


class TalentPatchRequest(BaseModel):
    """`PATCH /talents/{id}` 请求体（接口 §5：只接受画像自有字段）。

    - `educations/employments/projects/certificates` 一期只读（D-J）：**声明为 Any 字段**
      是为了让「出现即 400」能在 controller 里精确判定（若靠 `extra="forbid"` 会归一成
      422，不符合接口「按 400 处理」的要求）；`exclude=True` 保证不会泄进 model_dump。
    - 其余未声明字段（拼写错误等）→ `extra="forbid"` → 422 归一错误体。
    - 「未传」与「传 null」用 `model_fields_set` 区分：null = 清空（name 除外，NOT NULL）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=50)
    degree: str | None = Field(default=None, max_length=20)
    work_years: float | None = Field(default=None, ge=0, le=80)
    current_title: str | None = Field(default=None, max_length=100)
    expected_title: str | None = Field(default=None, max_length=100)
    skills: list[str] | None = Field(default=None, max_length=100)
    tags: list[TagEntry | str] | None = Field(default=None, max_length=100)
    tag_proofs: list[TagProof] = Field(default_factory=list, max_length=50)
    ability_scores: dict[str, AbilityDimIn] | None = None

    # —— 一期只读（D-J）：出现即 400，值不参与任何处理 ——
    educations: Any = Field(default=None, exclude=True)
    employments: Any = Field(default=None, exclude=True)
    projects: Any = Field(default=None, exclude=True)
    certificates: Any = Field(default=None, exclude=True)


class TagRowOut(BaseModel):
    """详情/PATCH 里的标签行（带 type 与 source，前端据此渲染标签编辑器）。"""

    model_config = ConfigDict(from_attributes=True)

    tag: str
    tag_type: str
    source: str


class TalentProfileOut(BaseModel):
    """`GET /talents/{id}` 的 profile 对象（接口 §5 字段集合，手机号/邮箱完整返回）。"""

    id: int
    name: str
    gender: str | None = None
    birth_year: int | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    degree: str | None = None
    work_years: float | None = None
    current_title: str | None = None
    expected_title: str | None = None
    skills: list[str] = Field(default_factory=list)
    tags: list[TagRowOut] = Field(default_factory=list)
    ability_scores: dict[str, dict[str, Any]] | None = None
    manual_edited_fields: list[str] = Field(default_factory=list)
    vector_status: str
    created_at: datetime
    updated_at: datetime | None = None


class TalentListItem(BaseModel):
    """`GET /talents` 的 items 元素（接口 §5；tags 仅前 8 个，tag_total 为全量计数）。"""

    id: int
    name: str
    current_title: str | None = None
    work_years: float | None = None
    degree: str | None = None
    city: str | None = None
    tags: list[str] = Field(default_factory=list)
    tag_total: int = 0
    source: str
    manual_edited_fields: list[str] = Field(default_factory=list)
    vector_status: str
    deleted_at: datetime | None = None
    updated_at: datetime | None = None


class ResumeFileRef(BaseModel):
    """详情页可下载的原始简历引用（blob 下载用 resume_file_id）。"""

    resume_file_id: int
    filename: str
    upload_batch_id: str


class ScreenHistoryItem(BaseModel):
    """详情页筛选历史（接口 §5：effective_verdict = manual_verdict ?? verdict）。"""

    batch_id: int
    job_title: str | None = None
    effective_verdict: str
    score: float | None = None
    created_at: datetime


class MatchHistoryItem(BaseModel):
    """详情页匹配历史。"""

    batch_id: int
    job_title: str | None = None
    score: float
    hard_fail: bool
    created_at: datetime


class TalentDetailData(BaseModel):
    """`GET /talents/{id}` 响应 data；经历类为聚合只读视图（D-J）。"""

    profile: TalentProfileOut
    educations: list[dict[str, Any]] = Field(default_factory=list)
    employments: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    certificates: list[dict[str, Any]] = Field(default_factory=list)
    resume_files: list[ResumeFileRef] = Field(default_factory=list)
    screen_history: list[ScreenHistoryItem] = Field(default_factory=list)
    match_history: list[MatchHistoryItem] = Field(default_factory=list)


class PatchTalentData(BaseModel):
    """`PATCH /talents/{id}` 响应 data（向量失败时 `vector_status="failed"` 仍 200）。"""

    profile: TalentProfileOut
    manual_edited_fields: list[str]
    vector_status: str
    profile_text_changed: bool


class RestoreTalentData(BaseModel):
    """`POST /talents/{id}/restore` 响应 data。"""

    restored: bool
    vector_status: str


class RevectorTalentData(BaseModel):
    """`POST /talents/{id}/revector` 响应 data。"""

    vector_status: str
    vector_synced_at: datetime | None = None


class DeleteTalentData(BaseModel):
    """`DELETE /talents/{id}` 响应 data（向量删除失败不阻塞，`vector_deleted: false`）。"""

    deleted: bool
    vector_deleted: bool


class TalentSearchItem(BaseModel):
    """`GET /talents/search` 的 items 元素（接口 §5：画像摘要 + `similarity`）。

    `similarity` 为 COSINE 相似度（0–1，越大越相似），由 Milvus distance 换算，
    服务端已 clamp 到 [0,1] 并保留 4 位小数。
    """

    id: int
    name: str
    current_title: str | None = None
    work_years: float | None = None
    degree: str | None = None
    city: str | None = None
    tags: list[str] = Field(default_factory=list)
    tag_total: int = 0
    vector_status: str
    similarity: float
