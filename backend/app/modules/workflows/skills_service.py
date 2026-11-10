"""Skills 组件库 service（T-D19，PRD §2.7 / 需求 D-35）。

组件 schema 四件套：提示词模板 + 参数 JSON Schema（随版本快照落库）+
工具绑定 + 输出解析器（随组件定义登记，白名单拒绝）。

生命周期与流程定义版本（T-D15）同构：
draft →(submit)→ pending_review →(审核通过)→ published →(停用)→ deprecated；
驳回退回 draft（留 review_note）。四眼原则：提交人不可自行审核（403）。
发布版本内容不可变（版本锁定）；编排节点引用 published 版本（引用校验见
resolve_published_skill），运行实例不受后续新版本/停用影响（引用钉死 version_id）。
组件执行经 run_service 的 skill 节点包装进运行审计（record_step）。

一期仅内部库（市场化按 PRD §8 后置）；创建/修改/提交/停用 = 管理侧，
审核 = 管理侧 + 四眼。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.workflows.models import (
    SKILL_OUTPUT_PARSERS,
    SKILL_TOOLS,
    SKILL_VERSION_STATUSES,
    WorkflowSkill,
    WorkflowSkillVersion,
)
from app.modules.workflows.service import _require_management

_MUTATE_PUBLISHED = "发布版本内容不可变（版本锁定），改版请新建草稿版本"

_PROMPT_MAX = 8000
_JSON_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties"}


def list_skills(db: Session) -> list[WorkflowSkill]:
    """全部组件（id 升序）。"""
    return list(
        db.execute(select(WorkflowSkill).order_by(WorkflowSkill.id.asc())).scalars().all()
    )


def create_skill(
    db: Session,
    *,
    code: str,
    name: str,
    tool: str,
    output_parser: str,
    description: str | None,
    user,
) -> WorkflowSkill:
    """新建组件定义（管理侧）：code 唯一；工具/解析器白名单外 422。"""
    _require_management(user)
    if not code or len(code) > 64:
        raise BizError(422, "code 必填且 ≤64 字符")
    if not name or len(name) > 128:
        raise BizError(422, "name 必填且 ≤128 字符")
    if tool not in SKILL_TOOLS:
        raise BizError(422, f"工具绑定 {tool!r} 不在白名单（{', '.join(SKILL_TOOLS)}）")
    if output_parser not in SKILL_OUTPUT_PARSERS:
        raise BizError(
            422, f"输出解析器 {output_parser!r} 不在白名单（{', '.join(SKILL_OUTPUT_PARSERS)}）"
        )
    skill = WorkflowSkill(
        code=code, name=name, description=description,
        tool=tool, output_parser=output_parser,
    )
    db.add(skill)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - code 唯一约束冲突
        db.rollback()
        raise BizError(409, f"组件 code 已存在：{code}") from exc
    db.refresh(skill)
    return skill


def _validate_params_schema(schema: Any) -> None:
    """参数 JSON Schema 形状校验（受控子集：object + properties/required）。"""
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise BizError(422, "params_json_schema 必须为对象")
    if schema.get("type") not in (None, "object"):
        raise BizError(422, "params_json_schema.type 仅允许 object")
    unknown = set(schema) - _JSON_SCHEMA_KEYS
    if unknown:
        raise BizError(422, f"params_json_schema 含不支持的字段：{sorted(unknown)}")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise BizError(422, "properties 必须为对象")
        for key, spec in properties.items():
            if not isinstance(key, str) or not key:
                raise BizError(422, "properties 键必须为非空字符串")
            if not isinstance(spec, dict):
                raise BizError(422, f"参数 {key} 的 schema 必须为对象")
            if "type" not in spec:
                raise BizError(422, f"参数 {key} 缺少 type")
    required = schema.get("required")
    if required is not None:
        if (
            not isinstance(required, list)
            or any(not isinstance(r, str) for r in required)
            or len(required) != len(set(required))
        ):
            raise BizError(422, "required 必须为不重复的字符串数组")
        declared = set(properties) if isinstance(properties, dict) else set()
        missing = [r for r in required if r not in declared]
        if missing:
            raise BizError(422, f"required 引用未声明的参数：{missing}")


def create_skill_version(
    db: Session,
    skill_id: int,
    *,
    prompt_template: str,
    params_json_schema: dict[str, Any] | None,
    user,
    created_by: int | None = None,
    note: str | None = None,
) -> WorkflowSkillVersion:
    """新建草稿版本（管理侧）：模板/schema 校验通过后版本号 = max+1 落库。"""
    _require_management(user)
    skill = db.get(WorkflowSkill, skill_id)
    if skill is None:
        raise BizError(404, "Skills 组件不存在")
    if not prompt_template or len(prompt_template) > _PROMPT_MAX:
        raise BizError(422, f"prompt_template 必填且 ≤{_PROMPT_MAX} 字符")
    _validate_params_schema(params_json_schema)
    max_version = db.execute(
        select(func.max(WorkflowSkillVersion.version)).where(
            WorkflowSkillVersion.skill_id == skill_id
        )
    ).scalar()
    version = WorkflowSkillVersion(
        skill_id=skill_id,
        version=(max_version or 0) + 1,
        status="draft",
        prompt_template=prompt_template,
        params_json_schema=params_json_schema,
        note=note,
        created_by=created_by,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def update_draft_skill_version(
    db: Session,
    version_id: int,
    *,
    prompt_template: str,
    params_json_schema: dict[str, Any] | None,
    user,
) -> WorkflowSkillVersion:
    """替换草稿版本内容（管理侧；发布版本拒绝——版本锁定）。"""
    _require_management(user)
    version = _get_skill_version(db, version_id)
    if version.status != "draft":
        raise BizError(409, _MUTATE_PUBLISHED)
    if not prompt_template or len(prompt_template) > _PROMPT_MAX:
        raise BizError(422, f"prompt_template 必填且 ≤{_PROMPT_MAX} 字符")
    _validate_params_schema(params_json_schema)
    version.prompt_template = prompt_template
    version.params_json_schema = params_json_schema
    db.commit()
    db.refresh(version)
    return version


def submit_skill_version(db: Session, version_id: int, *, user) -> WorkflowSkillVersion:
    """草稿 → 待审核（管理侧）。记录提交人（四眼依据）。"""
    _require_management(user)
    version = _get_skill_version(db, version_id)
    if version.status != "draft":
        raise BizError(409, f"仅草稿可提交审核，当前 {version.status}")
    version.status = "pending_review"
    version.submitted_by = getattr(user, "id", None)
    version.submitted_at = datetime.now()
    db.commit()
    db.refresh(version)
    return version


def review_skill_version(
    db: Session, version_id: int, *, user, approve: bool, note: str | None = None
) -> WorkflowSkillVersion:
    """审核待审版本：通过 → published；驳回 → draft（留 review_note）。

    四眼原则：提交人不可自行审核——无论通过或驳回，403（T-D19 验收①：
    未审核组件不可发布由 pending_review 前置 + 四眼 + 此审核口共同保证）。
    """
    _require_management(user)
    version = _get_skill_version(db, version_id)
    if version.status != "pending_review":
        raise BizError(409, f"仅待审核版本可审核，当前 {version.status}")
    submitter = version.submitted_by
    if submitter is not None and getattr(user, "id", None) == submitter:
        raise BizError(403, "提交与审核须为不同账号（四眼原则），不可自行发布")
    version.review_note = note
    if approve:
        version.status = "published"
        version.published_by = getattr(user, "id", None)
        version.published_at = datetime.now()
    else:
        version.status = "draft"
    db.commit()
    db.refresh(version)
    return version


def deactivate_skill_version(db: Session, version_id: int, *, user) -> WorkflowSkillVersion:
    """管理侧停用发布版本（已引用运行不受影响——运行钉死 version_id）。"""
    _require_management(user)
    version = _get_skill_version(db, version_id)
    if version.status != "published":
        raise BizError(409, f"仅发布版本可停用，当前 {version.status}")
    version.status = "deprecated"
    db.commit()
    db.refresh(version)
    return version


def resolve_published_skill(
    db: Session, *, skill_version_id: int
) -> dict[str, Any]:
    """编排引用解析：节点 config 引用 skill_version_id → 组件+版本快照。

    仅 published 可被新引用（draft/pending_review/deprecated 拒绝 422——
    验收①「未审核组件不可发布」的引用面）；返回四件套供执行面渲染。
    组件定义（tool/output_parser）随引用一并返回；运行实例钉死 version_id，
    后续停用/新版本不影响已引用实例（验收②）。
    """
    version = _get_skill_version(db, skill_version_id)
    if version.status != "published":
        raise BizError(422, f"仅发布版本可被编排引用，当前 {version.status}")
    skill = db.get(WorkflowSkill, version.skill_id)
    if skill is None:
        raise BizError(404, "Skills 组件不存在")
    return {
        "skill_id": skill.id,
        "skill_code": skill.code,
        "skill_name": skill.name,
        "tool": skill.tool,
        "output_parser": skill.output_parser,
        "skill_version_id": version.id,
        "version": version.version,
        "prompt_template": version.prompt_template,
        "params_json_schema": version.params_json_schema,
    }


def list_skill_versions(db: Session, skill_id: int) -> list[WorkflowSkillVersion]:
    """组件版本列表（版本号倒序）。"""
    if db.get(WorkflowSkill, skill_id) is None:
        raise BizError(404, "Skills 组件不存在")
    return list(
        db.execute(
            select(WorkflowSkillVersion)
            .where(WorkflowSkillVersion.skill_id == skill_id)
            .order_by(WorkflowSkillVersion.version.desc())
        ).scalars().all()
    )


def to_skill_dict(skill: WorkflowSkill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "code": skill.code,
        "name": skill.name,
        "description": skill.description,
        "tool": skill.tool,
        "output_parser": skill.output_parser,
    }


def to_skill_version_dict(version: WorkflowSkillVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "skill_id": version.skill_id,
        "version": version.version,
        "status": version.status,
        "prompt_template": version.prompt_template,
        "params_json_schema": version.params_json_schema,
        "note": version.note,
        "created_by": version.created_by,
        "submitted_by": version.submitted_by,
        "submitted_at": version.submitted_at.isoformat() if version.submitted_at else None,
        "review_note": version.review_note,
        "published_by": version.published_by,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


def _get_skill_version(db: Session, version_id: int) -> WorkflowSkillVersion:
    version = db.get(WorkflowSkillVersion, version_id)
    if version is None:
        raise BizError(404, "Skills 版本不存在")
    return version


# 状态白名单再确认（防 models 改动静默漂移）
assert set(SKILL_VERSION_STATUSES) == {"draft", "pending_review", "published", "deprecated"}
