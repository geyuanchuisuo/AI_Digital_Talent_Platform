"""招聘域业务服务（M3-V2.6，T-A60b/c/d，A08）：人才需求/项目、JD 投放、申请状态机。

约定：无物理外键，关联一致性在本层同事务校验；重复提交返回 409；非法状态
流转返回 400（枚举见 models.py）。所有写接口由调用方传入 user_id 记录操作人。

A08 申请输入快照（需求 §73 / §201 / §280）：`capture_application_input_snapshot`
在**创建申请时**把岗位/JD 条件、人才档案、简历原件与解析结果做一次值拷贝冻结。
它是纯读函数，不改任何源行；后续岗位/档案/JD 变更不得回写，历史申请据此复现
当时的评分依据。快照只经管理侧只读接口暴露，个人端不返回任何快照内容。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth.scope import get_role_codes
from app.core.errors import BizError
from app.models import (
    JobProfile,
    ParsedResume,
    Project,
    ProjectDemand,
    ProjectGroup,
    ResumeFile,
    TalentProfile,
)
from app.modules.recruitment.models import (
    DEMAND_ANCHOR_FIELDS,
    DEMAND_DECISIONS,
    DEMAND_PRIORITIES,
    DEMAND_REQUIRED_FIELDS,
    DEMAND_STATUSES,
    Application,
    DemandApprovalRecord,
    JdPublication,
    ProjectDemand,
)

RESULT_NOT_FOUND = "资源不存在"
DEMAND_CONFLICT = "同名同项目的人才需求已存在"

APPLICATION_SNAPSHOT_SCHEMA = "A08-v1"
DEMAND_APPROVAL_SNAPSHOT_SCHEMA = "A11-v1"

# A11：审批决策对象的字段值拷贝；status（业务生命周期）与 approval_version 只作
# 上下文记录，不参与「内容是否变更」的判定。
_DEMAND_SNAPSHOT_FIELDS = (
    "id", "project_id", "project_group_id", "job_profile_id", "position_name",
    "headcount", "expected_start_date", "priority", "remark", "status",
    "approval_version",
)
_DEMAND_CONTENT_FIELDS = (
    "project_id", "project_group_id", "job_profile_id", "position_name",
    "headcount", "expected_start_date", "priority", "remark",
)

# 快照字段白名单：只冻结「评分依据与档案输入」，不把账号/审计列带进历史快照。
_JOB_SNAPSHOT_FIELDS = (
    "id", "title", "department", "jd_text", "requirements_text", "min_degree",
    "min_work_years", "must_skills_json", "bonus_skills_json",
    "hard_rule_switches_json", "pass_threshold", "pending_threshold",
    "weights_json", "version", "is_active",
)
_PROFILE_SNAPSHOT_FIELDS = (
    "id", "name", "gender", "birth_year", "phone", "email", "city", "degree",
    "work_years", "current_title", "expected_title", "skills_json", "tags_json",
    "ability_scores_json", "profile_text_hash", "vector_status", "source",
    "manual_edited_fields", "deleted_at",
)
_RESUME_FILE_SNAPSHOT_FIELDS = (
    "id", "filename", "file_hash", "file_ext", "file_size", "upload_batch_id",
)
_JD_SNAPSHOT_FIELDS = (
    "id", "job_profile_id", "job_profile_version", "title", "channel",
    "content_snapshot_json", "status", "published_at",
)


def _plain(value: Any) -> Any:
    """把 ORM 值转成 JSON 可序列化值（Decimal→float、日期→ISO、容器递归）。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _snapshot_of(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: _plain(getattr(row, name, None)) for name in fields}



def _demand_conflict(db: Session, position_name: str, project_id: int | None, exclude_id: int | None = None) -> bool:
    stmt = select(func.count()).select_from(ProjectDemand).where(
        ProjectDemand.position_name == position_name,
        # 同岗位同项目防重；无项目归属时按 NULL 匹配（is_ 仅用于 NULL 语义）
        (
            ProjectDemand.project_id == project_id
            if project_id is not None
            else ProjectDemand.project_id.is_(None)
        ),
        ProjectDemand.status.in_(("open", "in_progress")),  # 在途需求防重；历史 fulfilled/closed 允许重开
    )
    if exclude_id is not None:
        stmt = stmt.where(ProjectDemand.id != exclude_id)
    return (db.execute(stmt).scalar_one() or 0) > 0


def create_project_group(db: Session, *, name: str, description: str | None, user_id: int) -> ProjectGroup:
    row = ProjectGroup(name=name, description=description, created_by=user_id)
    db.add(row)
    db.commit()
    return row


def list_project_groups(db: Session, *, offset: int, limit: int) -> tuple[list[ProjectGroup], int]:
    total = db.execute(select(func.count()).select_from(ProjectGroup)).scalar_one()
    rows = db.execute(select(ProjectGroup).order_by(ProjectGroup.id.desc()).offset(offset).limit(limit)).scalars().all()
    return list(rows), int(total)


def create_project(db: Session, *, name: str, project_group_id: int | None, description: str | None, user_id: int) -> Project:
    if project_group_id is not None and db.get(ProjectGroup, project_group_id) is None:
        raise BizError(404, "项目群不存在")
    row = Project(name=name, project_group_id=project_group_id, description=description, created_by=user_id)
    db.add(row)
    db.commit()
    return row


def list_projects(db: Session, *, project_group_id: int | None, offset: int, limit: int) -> tuple[list[Project], int]:
    stmt = select(func.count()).select_from(Project)
    rows_stmt = select(Project).order_by(Project.id.desc())
    if project_group_id is not None:
        stmt = stmt.where(Project.project_group_id == project_group_id)
        rows_stmt = rows_stmt.where(Project.project_group_id == project_group_id)
    total = db.execute(stmt).scalar_one()
    rows = db.execute(rows_stmt.offset(offset).limit(limit)).scalars().all()
    return list(rows), int(total)


def create_demand(db: Session, fields: dict[str, Any], *, user_id: int) -> ProjectDemand:
    if _demand_conflict(db, fields["position_name"], fields.get("project_id")):
        raise BizError(409, DEMAND_CONFLICT)
    if fields.get("project_id") is not None and db.get(Project, fields["project_id"]) is None:
        raise BizError(404, "项目不存在")
    row = ProjectDemand(**fields, created_by=user_id)
    db.add(row)
    db.commit()
    return row


def list_demands(db: Session, *, status: str | None, project_id: int | None, offset: int, limit: int) -> tuple[list[ProjectDemand], int]:
    stmt = select(func.count()).select_from(ProjectDemand)
    rows_stmt = select(ProjectDemand).order_by(ProjectDemand.id.desc())
    if status is not None:
        if status not in DEMAND_STATUSES:
            raise BizError(400, f"status 仅支持 {' / '.join(DEMAND_STATUSES)}")
        stmt = stmt.where(ProjectDemand.status == status)
        rows_stmt = rows_stmt.where(ProjectDemand.status == status)
    if project_id is not None:
        stmt = stmt.where(ProjectDemand.project_id == project_id)
        rows_stmt = rows_stmt.where(ProjectDemand.project_id == project_id)
    total = db.execute(stmt).scalar_one()
    rows = db.execute(rows_stmt.offset(offset).limit(limit)).scalars().all()
    return list(rows), int(total)


def get_demand(db: Session, demand_id: int) -> ProjectDemand:
    row = db.get(ProjectDemand, demand_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    return row


def update_demand(db: Session, demand_id: int, changes: dict[str, Any], *, user_id: int) -> ProjectDemand:
    row = get_demand(db, demand_id)
    new_status = changes.get("status")
    if new_status is not None and new_status != row.status:
        # 状态流转白名单：open → in_progress → fulfilled/closed；closed/fulfilled 不回退（重开需另立规则）
        allowed = {
            "open": {"in_progress", "closed"},
            "in_progress": {"fulfilled", "closed"},
            "fulfilled": set(),
            "closed": set(),
        }
        if new_status not in allowed[row.status]:
            raise BizError(400, f"需求状态不允许从 {row.status} 流转到 {new_status}")
        changes["status"] = new_status
    if changes.get("position_name") and _demand_conflict(
        db, changes["position_name"], changes.get("project_id", row.project_id), exclude_id=demand_id
    ):
        raise BizError(409, DEMAND_CONFLICT)
    # A11：补齐组织锚点时与创建接口保持同一关联校验（无物理外键，service 层保证）。
    if changes.get("project_id") is not None and db.get(Project, changes["project_id"]) is None:
        raise BizError(404, "项目不存在")

    # A11：审批状态只能经审批接口变更。业务生命周期 status 的推进不算内容变更，
    # 但审批中禁止改内容；已批/已驳回的需求改内容会生成新版本并令旧审批失效。
    content_changed = any(key in _DEMAND_CONTENT_FIELDS for key in changes)
    if content_changed and row.approval_status == "pending":
        raise BizError(409, "审批中的需求不可修改内容，请先退回补充或撤回")
    invalidate = content_changed and row.approval_status in ("approved", "rejected")
    old_status = row.approval_status
    for key, value in changes.items():
        setattr(row, key, value)
    row.updated_by = user_id
    if invalidate:
        row.approval_version += 1
        row.approval_status = "draft"
        row.approved_by = None
        row.approved_at = None
        # approved_version 保留为「最后一次获批的版本」，与新版本不等即不可匹配。
        _add_approval_record(
            db, row,
            action="revision", from_status=old_status, to_status="draft",
            user_id=user_id,
            reason="内容变更触发版本升级，原审批自动失效",
        )
    db.commit()
    return row


# ---- A11 岗位/人才需求审批链（§6.11 步骤 1 + Master 执行表 A11）----


def _demand_snapshot(row: ProjectDemand) -> dict[str, Any]:
    """决策时刻的需求字段值拷贝（A11-v1）；后续编辑不回写已落库的记录。"""
    return {
        "schema_version": DEMAND_APPROVAL_SNAPSHOT_SCHEMA,
        **_snapshot_of(row, _DEMAND_SNAPSHOT_FIELDS),
    }


def _add_approval_record(
    db: Session,
    row: ProjectDemand,
    *,
    action: str,
    from_status: str,
    to_status: str,
    user_id: int,
    reason: str | None = None,
    actor_roles: tuple[str, ...] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> DemandApprovalRecord:
    """只追加一行审批留痕；调用方负责 commit。"""
    roles = actor_roles if actor_roles is not None else get_role_codes(db, user_id)
    record = DemandApprovalRecord(
        demand_id=row.id,
        demand_version=row.approval_version,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=user_id,
        actor_roles=",".join(sorted(set(roles))) or "unknown",
        reason=reason,
        snapshot_json=snapshot if snapshot is not None else _demand_snapshot(row),
    )
    db.add(record)
    return record


def _missing_required_demand_fields(row: ProjectDemand) -> list[str]:
    missing: list[str] = []
    for name in DEMAND_REQUIRED_FIELDS:
        value = getattr(row, name, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
        elif name == "headcount" and (not isinstance(value, int) or value < 1):
            missing.append(name)
        elif name == "priority" and value not in DEMAND_PRIORITIES:
            missing.append(name)
    if not any(getattr(row, name, None) is not None for name in DEMAND_ANCHOR_FIELDS):
        missing.append("project_id/project_group_id/job_profile_id")
    return missing


def submit_demand(
    db: Session, demand_id: int, *, user_id: int, actor_roles: tuple[str, ...], note: str | None = None
) -> ProjectDemand:
    """HR 发起提交（draft/rejected → pending）；重复提交 409、缺必填 400。"""
    row = get_demand(db, demand_id)
    if row.approval_status == "withdrawn":
        raise BizError(400, "需求已撤回，不可再提交；须另立需求")
    if row.approval_status == "pending":
        raise BizError(409, "需求已在审批中，不可重复提交")
    if row.approval_status == "approved":
        raise BizError(400, "需求已审批通过；变更内容会自动生成新版本并回到草稿")
    missing = _missing_required_demand_fields(row)
    if missing:
        raise BizError(400, "提交审批前须补齐必填项：" + "、".join(missing))
    old_status = row.approval_status
    _add_approval_record(
        db, row, action="submit", from_status=old_status, to_status="pending",
        user_id=user_id, actor_roles=actor_roles, reason=note,
    )
    row.approval_status = "pending"
    db.commit()
    return row


def decide_demand(
    db: Session, demand_id: int, decision: str, *, user_id: int,
    actor_roles: tuple[str, ...], reason: str | None = None,
) -> ProjectDemand:
    """业务负责人审批（仅 pending 可决定）；重复批准/非待审批 409、四眼 409、原因必填 400。"""
    row = get_demand(db, demand_id)
    if decision not in DEMAND_DECISIONS:
        raise BizError(400, f"decision 仅支持 {' / '.join(DEMAND_DECISIONS)}")
    if row.approval_status != "pending":
        raise BizError(409, f"当前审批状态 {row.approval_status}，无可审批决定（重复批准被拒绝）")
    if decision in ("reject", "return") and not (reason or "").strip():
        raise BizError(400, "拒绝或退回补充必须填写原因")
    # 四眼原则：本版本提交人不可自批（按用户 id，而非角色，避免同角色不同账号绕过）。
    submitter = db.execute(
        select(DemandApprovalRecord)
        .where(
            DemandApprovalRecord.demand_id == row.id,
            DemandApprovalRecord.demand_version == row.approval_version,
            DemandApprovalRecord.action == "submit",
        )
        .order_by(DemandApprovalRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if submitter is not None and submitter.actor_user_id == user_id:
        raise BizError(409, "四眼原则：本版本提交人不可自行审批")

    old_status = row.approval_status
    to_status = {"approve": "approved", "reject": "rejected", "return": "draft"}[decision]
    _add_approval_record(
        db, row, action=decision, from_status=old_status, to_status=to_status,
        user_id=user_id, actor_roles=actor_roles, reason=reason,
    )
    row.approval_status = to_status
    if decision == "approve":
        row.approved_version = row.approval_version
        row.approved_by = user_id
        row.approved_at = datetime.now()
    db.commit()
    return row


def withdraw_demand(
    db: Session, demand_id: int, *, user_id: int,
    actor_roles: tuple[str, ...], reason: str,
) -> ProjectDemand:
    """撤回需求（终态）；原因必填，撤回后不可再提交或审批。"""
    row = get_demand(db, demand_id)
    if not (reason or "").strip():
        raise BizError(400, "撤回需求必须填写原因")
    if row.approval_status == "withdrawn":
        raise BizError(409, "需求已撤回，不可重复撤回")
    old_status = row.approval_status
    _add_approval_record(
        db, row, action="withdraw", from_status=old_status, to_status="withdrawn",
        user_id=user_id, actor_roles=actor_roles, reason=reason,
    )
    row.approval_status = "withdrawn"
    row.approved_by = None
    row.approved_at = None
    db.commit()
    return row


def list_demand_approval_records(db: Session, demand_id: int) -> list[DemandApprovalRecord]:
    """按时间正序返回该需求的全部审批留痕（只追加，不提供改/删接口）。"""
    get_demand(db, demand_id)  # 404 语义与其他接口一致
    return list(
        db.execute(
            select(DemandApprovalRecord)
            .where(DemandApprovalRecord.demand_id == demand_id)
            .order_by(DemandApprovalRecord.id.asc())
        ).scalars().all()
    )


def capture_application_input_snapshot(
    db: Session,
    *,
    talent_profile_id: int,
    job_profile_id: int,
    job_profile_version: int,
    resume_file_id: int | None,
) -> dict[str, Any]:
    """创建申请时冻结输入值拷贝（A08）。纯读，不改任何源行。

    设计取舍：
    - **值拷贝而非引用**：`job_profile` / `talent_profile` 等按白名单复制字段，
      源行后续 UPDATE 不会反映到已冻结的快照。
    - **缺源容错**：历史/演示数据可能只有 id 没有实体行，缺失项写入
      `missing_sources` 并置 None，**不阻断创建**（保持既有接口行为）。
    - **原文不整段复制**：`parsed_resumes.raw_text` 是 MEDIUMTEXT，改用
      sha256 + 长度固定其可核对性；评分真正使用的 `structured_json` 完整冻结，
      原件本身由 `resume_file.file_hash` 追溯。
    - 年龄/性别等受保护属性只在**评分时**排除（T-A34），快照忠实记录历史输入，
      不代表参与评分。
    """
    missing: list[str] = []
    now = datetime.now()

    job = db.get(JobProfile, job_profile_id)
    job_snapshot = _snapshot_of(job, _JOB_SNAPSHOT_FIELDS) if job is not None else None
    if job is None:
        missing.append("job_profile")

    profile = db.get(TalentProfile, talent_profile_id)
    profile_snapshot = _snapshot_of(profile, _PROFILE_SNAPSHOT_FIELDS) if profile is not None else None
    if profile is None:
        missing.append("talent_profile")

    jd = db.execute(
        select(JdPublication)
        .where(
            JdPublication.job_profile_id == job_profile_id,
            JdPublication.status == "published",
        )
        .order_by(JdPublication.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    jd_snapshot = _snapshot_of(jd, _JD_SNAPSHOT_FIELDS) if jd is not None else None
    if jd is None:
        missing.append("jd_publication")

    # 未显式指定原件时回退到该档案最近一条解析记录，仅用于快照，不改申请行的 resume_file_id
    effective_file_id = resume_file_id
    parsed: ParsedResume | None = None
    if effective_file_id is None:
        parsed = db.execute(
            select(ParsedResume)
            .where(ParsedResume.talent_profile_id == talent_profile_id)
            .order_by(ParsedResume.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if parsed is not None:
            effective_file_id = parsed.resume_file_id

    file_row = db.get(ResumeFile, effective_file_id) if effective_file_id is not None else None
    file_snapshot = _snapshot_of(file_row, _RESUME_FILE_SNAPSHOT_FIELDS) if file_row is not None else None
    if file_row is None:
        missing.append("resume_file")

    if parsed is None and effective_file_id is not None:
        parsed = db.execute(
            select(ParsedResume).where(ParsedResume.resume_file_id == effective_file_id)
        ).scalar_one_or_none()
    if parsed is None:
        missing.append("parsed_resume")
        parsed_snapshot = None
    else:
        raw_text = parsed.raw_text or ""
        parsed_snapshot = {
            "id": parsed.id,
            "resume_file_id": parsed.resume_file_id,
            "talent_profile_id": parsed.talent_profile_id,
            "structured_json": _plain(parsed.structured_json),
            "llm_model": parsed.llm_model,
            "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "raw_text_length": len(raw_text),
        }

    return {
        "schema_version": APPLICATION_SNAPSHOT_SCHEMA,
        "captured_at": now.isoformat(timespec="seconds"),
        "job_profile_id": job_profile_id,
        "job_profile_version": job_profile_version,
        "talent_profile_id": talent_profile_id,
        "resume_file_id": effective_file_id,
        "job_profile": job_snapshot,
        "jd_publication": jd_snapshot,
        "talent_profile": profile_snapshot,
        "resume_file": file_snapshot,
        "parsed_resume": parsed_snapshot,
        "missing_sources": missing,
    }


def get_application_input_snapshot(db: Session, application_id: int) -> dict[str, Any]:
    """管理侧只读：返回冻结快照；迁移前历史行 `available=false`，不报错。"""
    row = db.get(Application, application_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    snapshot = row.input_snapshot_json
    return {
        "application_id": row.id,
        "available": snapshot is not None,
        "schema_version": (snapshot or {}).get("schema_version"),
        "captured_at": (
            row.input_snapshot_at.isoformat(timespec="seconds")
            if row.input_snapshot_at else None
        ),
        "snapshot": snapshot,
    }


def create_application(
    db: Session,
    *,
    talent_profile_id: int,
    job_profile_id: int,
    job_profile_version: int,
    resume_file_id: int | None,
    user_id: int,
) -> Application:
    """创建岗位申请（D-26 多岗位独立）：同人在**同一岗位**存在在途申请时 409。

    A08：在同事务内一次性冻结申请输入快照，之后永不回写。
    """
    in_flight = db.execute(
        select(func.count()).select_from(Application).where(
            Application.talent_profile_id == talent_profile_id,
            Application.job_profile_id == job_profile_id,
            Application.internal_stage.not_in(("offer_accepted", "rejected", "withdrawn", "job_closed")),
        )
    ).scalar_one()
    if in_flight:
        raise BizError(409, "该候选人已有同岗位在途申请")
    snapshot = capture_application_input_snapshot(
        db,
        talent_profile_id=talent_profile_id,
        job_profile_id=job_profile_id,
        job_profile_version=job_profile_version,
        resume_file_id=resume_file_id,
    )
    row = Application(
        talent_profile_id=talent_profile_id,
        job_profile_id=job_profile_id,
        job_profile_version=job_profile_version,
        resume_file_id=resume_file_id,
        input_snapshot_json=snapshot,
        input_snapshot_at=datetime.fromisoformat(snapshot["captured_at"]),
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    return row


def set_application_stage(
    db: Session, application_id: int, internal_stage: str, *, reason: str | None, user_id: int
) -> Application:
    """人工推进内部阶段；对外阶段按 EXTERNAL_STAGE_MAP 自动更新（V2.6 已确认映射）。

    A08：本函数与 `confirm_application_result` 都**不得**触碰
    `input_snapshot_json` / `input_snapshot_at`——快照只在创建时冻结一次。
    """
    from app.modules.recruitment.models import INTERNAL_STAGES, TERMINAL_STAGES, external_stage_of

    row = db.get(Application, application_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if internal_stage not in INTERNAL_STAGES:
        raise BizError(400, f"internal_stage 仅支持 {' / '.join(INTERNAL_STAGES)}")
    if row.internal_stage in TERMINAL_STAGES:
        raise BizError(400, f"申请已终态（{row.internal_stage}），不可再流转；终态重开须另行授权")
    row.internal_stage = internal_stage
    row.external_stage = external_stage_of(internal_stage)
    row.stage_reason = reason
    row.stage_updated_by = user_id
    row.stage_updated_at = datetime.now()
    if internal_stage in TERMINAL_STAGES:
        # 终态结果类别：录用仅来自 offer_accepted；其余终态为未录用（未确认前不对外）
        row.result_category = "hired" if internal_stage == "offer_accepted" else "not_hired"
        row.closed_at = datetime.now()
    db.commit()
    return row


def confirm_application_result(db: Session, application_id: int, *, user_id: int) -> Application:
    """HR 确认终态结果类别（V2.6：终态及最终结果须 HR 确认后才对外发布）。"""
    from app.modules.recruitment.models import TERMINAL_STAGES

    row = db.get(Application, application_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if row.internal_stage not in TERMINAL_STAGES:
        raise BizError(400, "申请未到终态，无可确认结果")
    if row.result_confirmed_by is None:
        row.result_confirmed_by = user_id
        db.commit()
    return row


def list_applications(
    db: Session, *, talent_profile_id: int | None, job_profile_id: int | None, external_stage: str | None, offset: int, limit: int
) -> tuple[list[Application], int]:
    stmt = select(func.count()).select_from(Application)
    rows_stmt = select(Application).order_by(Application.id.desc())
    for column, value in (
        (Application.talent_profile_id, talent_profile_id),
        (Application.job_profile_id, job_profile_id),
        (Application.external_stage, external_stage),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
            rows_stmt = rows_stmt.where(column == value)
    total = db.execute(stmt).scalar_one()
    rows = db.execute(rows_stmt.offset(offset).limit(limit)).scalars().all()
    return list(rows), int(total)


# ---- JD 生成投放（T-A60d）----


def create_jd_publication(
    db: Session,
    *,
    job_profile_id: int,
    job_profile_version: int,
    title: str,
    content: dict[str, Any],
    channel: str,
    user_id: int,
) -> JdPublication:
    row = JdPublication(
        job_profile_id=job_profile_id,
        job_profile_version=job_profile_version,
        title=title,
        content_snapshot_json=content,
        channel=channel,
        published_by=user_id,
        published_at=datetime.now(),
        status="published",
    )
    db.add(row)
    db.commit()
    return row


def withdraw_jd_publication(db: Session, publication_id: int, *, user_id: int) -> JdPublication:
    row = db.get(JdPublication, publication_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if row.status != "published":
        raise BizError(400, f"仅已投放（published）可撤回，当前 {row.status}")
    row.status = "withdrawn"
    row.withdrawn_by = user_id
    row.withdrawn_at = datetime.now()
    db.commit()
    return row


def list_jd_publications(
    db: Session, *, job_profile_id: int | None, offset: int, limit: int
) -> tuple[list[JdPublication], int]:
    stmt = select(func.count()).select_from(JdPublication)
    rows_stmt = select(JdPublication).order_by(JdPublication.id.desc())
    if job_profile_id is not None:
        stmt = stmt.where(JdPublication.job_profile_id == job_profile_id)
        rows_stmt = rows_stmt.where(JdPublication.job_profile_id == job_profile_id)
    total = db.execute(stmt).scalar_one()
    rows = db.execute(rows_stmt.offset(offset).limit(limit)).scalars().all()
    return list(rows), int(total)


def demand_gap_summary(
    db: Session, *, status: str | None, project_id: int | None, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """缺口统计（T-A24，指标口径交付 §7）：缺口 = headcount − 已录用数。

    已录用数取 HR 确认口径（§12.3「人工推进 vs 已录用不混称」）：与本需求
    `job_profile_id` 同岗的 `applications` 中 `internal_stage='offer_accepted'`
    且 `result_confirmed_by` 非空的行，按候选人去重计数（同一人的历史申请行
    不重复计入）。需求未关联岗位（job_profile_id 空）时无录用路径，
    hired 恒为 0、缺口 = headcount。gap 为负表示超编，如实返回不做截断。
    过滤参数（status/project_id）与 `list_demands` 同口径。
    """
    stmt = select(func.count()).select_from(ProjectDemand)
    rows_stmt = select(ProjectDemand).order_by(ProjectDemand.id.desc())
    if status is not None:
        if status not in DEMAND_STATUSES:
            raise BizError(400, f"status 仅支持 {' / '.join(DEMAND_STATUSES)}")
        stmt = stmt.where(ProjectDemand.status == status)
        rows_stmt = rows_stmt.where(ProjectDemand.status == status)
    if project_id is not None:
        stmt = stmt.where(ProjectDemand.project_id == project_id)
        rows_stmt = rows_stmt.where(ProjectDemand.project_id == project_id)
    total = db.execute(stmt).scalar_one()
    rows = db.execute(rows_stmt.offset(offset).limit(limit)).scalars().all()

    hired_counts: dict[int, int] = {}
    advanced_counts: dict[int, int] = {}
    job_ids = {row.job_profile_id for row in rows if row.job_profile_id is not None}
    if job_ids:
        hired_stmt = (
            select(
                Application.job_profile_id,
                func.count(func.distinct(Application.talent_profile_id)),
            )
            .where(
                Application.job_profile_id.in_(job_ids),
                Application.internal_stage == "offer_accepted",
                Application.result_confirmed_by.is_not(None),
            )
            .group_by(Application.job_profile_id)
        )
        hired_counts = {int(job_id): int(cnt) for job_id, cnt in db.execute(hired_stmt)}
        # T-A24 页面口径「人工标记进入下一步人数」：同一岗位申请中曾被人
        # 工推进（internal_stage ≠ new）的候选人去重数（含后续录用/淘汰，
        # 与「已录用」分列展示，二者不混称——§12.3）。
        advanced_stmt = (
            select(
                Application.job_profile_id,
                func.count(func.distinct(Application.talent_profile_id)),
            )
            .where(
                Application.job_profile_id.in_(job_ids),
                Application.internal_stage != "new",
            )
            .group_by(Application.job_profile_id)
        )
        advanced_counts = {int(job_id): int(cnt) for job_id, cnt in db.execute(advanced_stmt)}

    items = []
    for row in rows:
        hired = hired_counts.get(row.job_profile_id, 0) if row.job_profile_id is not None else 0
        advanced = advanced_counts.get(row.job_profile_id, 0) if row.job_profile_id is not None else 0
        items.append(
            {
                "demand_id": row.id,
                "position_name": row.position_name,
                "project_id": row.project_id,
                "project_group_id": row.project_group_id,
                "job_profile_id": row.job_profile_id,
                "demand_status": row.status,
                "headcount": row.headcount,
                "advanced_count": advanced,
                "hired_count": hired,
                "gap": row.headcount - hired,
            }
        )
    return items, int(total)
