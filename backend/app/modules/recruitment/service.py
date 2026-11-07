"""招聘域业务服务（M3-V2.6，T-A60b/c/d）：人才需求/项目、JD 投放、申请状态机。

约定：无物理外键，关联一致性在本层同事务校验；重复提交返回 409；非法状态
流转返回 400（枚举见 models.py）。所有写接口由调用方传入 user_id 记录操作人。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import Project, ProjectDemand, ProjectGroup
from app.modules.recruitment.models import (
    DEMAND_STATUSES,
    Application,
    JdPublication,
    ProjectDemand,
)

RESULT_NOT_FOUND = "资源不存在"
DEMAND_CONFLICT = "同名同项目的人才需求已存在"


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
    for key, value in changes.items():
        setattr(row, key, value)
    row.updated_by = user_id
    db.commit()
    return row


def create_application(
    db: Session,
    *,
    talent_profile_id: int,
    job_profile_id: int,
    job_profile_version: int,
    resume_file_id: int | None,
    user_id: int,
) -> Application:
    """创建岗位申请（D-26 多岗位独立）：同人在**同一岗位**存在在途申请时 409。"""
    in_flight = db.execute(
        select(func.count()).select_from(Application).where(
            Application.talent_profile_id == talent_profile_id,
            Application.job_profile_id == job_profile_id,
            Application.internal_stage.not_in(("offer_accepted", "rejected", "withdrawn", "job_closed")),
        )
    ).scalar_one()
    if in_flight:
        raise BizError(409, "该候选人已有同岗位在途申请")
    row = Application(
        talent_profile_id=talent_profile_id,
        job_profile_id=job_profile_id,
        job_profile_version=job_profile_version,
        resume_file_id=resume_file_id,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    return row


def set_application_stage(
    db: Session, application_id: int, internal_stage: str, *, reason: str | None, user_id: int
) -> Application:
    """人工推进内部阶段；对外阶段按 EXTERNAL_STAGE_MAP 自动更新（V2.6 已确认映射）。"""
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
