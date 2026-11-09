"""工作流运行编排（T-D17）：手动触发 → 引擎执行 → 审批恢复 → 状态镜像。

职责边界：
- service.py（T-D14/T-D15）负责定义/版本/登记原语（start_run 钉版本、record_step
  幂等落步骤、sync_run_status 镜像状态）；本模块把它们与引擎（T-D04）接线；
- 触发面一期只暴露手动（管理侧）；业务事件触发/定时触发归 T-D18（定时默认关闭）；
- 失败重试/取消/恢复编排归 T-D18；本模块把引擎 failed 如实镜像，可重试语义沿用引擎。

审批恢复口校验（三道，全部先于引擎恢复）：
①管理侧门禁（can_view_all_persons）；②四眼原则——发起人不可自行审批（403）；
③审批负载 deadline 过期 → 409 需人工接管（定时催办扫描归 T-D18）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.workflows.engine.runner import EngineRunResult, WorkflowEngine
from app.models import AssessmentAssignment, AssessmentResult, ParsedResume, ParseTask, ResumeFile, WorkflowEngineRun
from app.core.workflows.engine.saver import make_saver
from app.modules.workflows import service
from app.modules.workflows.models import WorkflowDefinition, WorkflowDefinitionVersion, WorkflowRun, WorkflowRunStep
from app.modules.workflows.nodes.graph_builder import build_version_graph, graph_name_for
from app.modules.workflows.nodes.runtime import NodeContext

_engine: WorkflowEngine | None = None
_session_factory: Any = None  # 节点自开短会话的工厂；默认 SessionLocal（测试可替换）


def get_session_factory() -> Any:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        from app.database import SessionLocal  # noqa: PLC0415

        _session_factory = SessionLocal
    return _session_factory


def set_session_factory(factory: Any) -> None:
    global _session_factory  # noqa: PLW0603
    _session_factory = factory


def get_engine() -> WorkflowEngine:
    """进程级引擎单例（检查点存主库；registry_factory 同源）。测试可替换 _engine。"""
    global _engine  # noqa: PLW0603
    if _engine is None:
        from app.config import settings  # noqa: PLC0415
        from app.database import SessionLocal  # noqa: PLC0415

        _engine = WorkflowEngine(
            make_saver(settings.database_url), registry_factory=SessionLocal
        )
    return _engine


def set_engine(engine: WorkflowEngine | None) -> None:
    global _engine  # noqa: PLW0603
    _engine = engine


def _make_context(run: WorkflowRun) -> NodeContext:
    return NodeContext(
        run_id=run.id,
        thread_id=run.thread_id,
        session_factory=get_session_factory(),
        user_id=run.created_by,
    )


def _build(run: WorkflowRun, db: Session):
    detail = service.get_version_detail(db, run.version_id)
    ctx = _make_context(run)
    return build_version_graph(detail, ctx)


def _mirror(db: Session, run: WorkflowRun, result: EngineRunResult,
            *, waiting_node: str | None = None) -> WorkflowRun:
    """引擎结果 → 业务侧镜像：running→waiting/succeeded/failed；waiting 时步骤记 waiting。"""
    if result.status == "succeeded":
        run_status = "succeeded"
    elif result.status == "waiting":
        run_status = "waiting"
    else:
        run_status = "failed"
    service.sync_run_status(db, run.id, status=run_status, error=result.error)
    if run_status == "waiting" and waiting_node:
        payload_node_type = _waiting_node_type(db, run, waiting_node)
        service.record_step(
            db, run.id, node_key=waiting_node, node_type=payload_node_type, status="waiting",
            output={"interrupt": result.interrupt or {}},
        )
    db.refresh(run)
    return run


def _waiting_node_type(db: Session, run: WorkflowRun, node_key: str) -> str:
    row = db.execute(
        select(WorkflowRunStep.node_type).where(
            WorkflowRunStep.run_id == run.id, WorkflowRunStep.node_key == node_key
        )
    ).first()
    if row is not None:
        return str(row[0])
    from app.modules.workflows.models import WorkflowNode  # noqa: PLC0415

    node = db.execute(
        select(WorkflowNode).where(
            WorkflowNode.version_id == run.version_id,
            WorkflowNode.node_key == node_key,
        )
    ).scalar_one_or_none()
    if node is None:
        raise BizError(422, f"等待节点 {node_key!r} 不在版本节点集合中")
    return node.node_type


# ---- 受控触发绑定 ----

def resolve_event_definition(db: Session, *, event_code: str) -> int | None:
    """从唯一发布版本解析业务事件绑定；未配置时不自动触发。"""
    if event_code not in ("employee_assessment_completed", "resume_profiled"):
        raise BizError(422, "业务事件未登记")
    definitions = list(db.scalars(
        select(WorkflowDefinition)
        .join(WorkflowDefinitionVersion, WorkflowDefinitionVersion.definition_id == WorkflowDefinition.id)
        .where(
            WorkflowDefinition.code == event_code,
            WorkflowDefinitionVersion.status == "published",
        )
        .limit(2)
    ))
    if len(definitions) > 1:
        raise BizError(409, "同一业务事件存在多个发布版本，请先收敛绑定")
    return definitions[0].id if definitions else None


def start_parse_profiled_run(
    db: Session, *, resume_file_id: int, actor,
) -> dict[str, Any] | None:
    """仅由上传同一简历的管理账号触发解析成功流程；未绑定发布定义时跳过。"""
    service.require_management(actor)
    task = db.execute(
        select(ParseTask).where(ParseTask.resume_file_id == resume_file_id)
    ).scalar_one_or_none()
    parsed = db.get(ParsedResume, resume_file_id)
    file_row = db.get(ResumeFile, resume_file_id)
    if task is None or parsed is None or file_row is None:
        raise BizError(404, "解析结果不存在")
    if task.status != "success" or parsed.talent_profile_id is None:
        raise BizError(422, "解析尚未成功并归属画像，不触发流程")
    if file_row.uploaded_by != actor.id:
        raise BizError(403, "仅该简历的上传人可触发后续匹配流程")
    definition_id = resolve_event_definition(db, event_code="resume_profiled")
    if definition_id is None:
        return None
    return start_event_run(
        db, definition_id=definition_id, event_type="resume.profiled",
        event_id=f"{resume_file_id}:profiled",
        params={
            "resume_file_id": resume_file_id,
            "talent_profile_id": parsed.talent_profile_id,
        },
        actor=actor,
    )


# ---- 手动触发 ----

def start_manual_run(
    db: Session,
    *,
    definition_id: int,
    params: dict[str, Any] | None,
    user,
) -> dict[str, Any]:
    """管理侧手动触发：登记运行（钉版本）→ 构建图 → 引擎启动 → 镜像状态。"""
    service.require_management(user)
    run = service.start_run(
        db,
        definition_id=definition_id,
        thread_id=uuid.uuid4().hex,
        params=params,
        trigger_type="manual",
        created_by=getattr(user, "id", None),
    )
    graph = _build(run, db)
    result = get_engine().start(
        graph_name_for(run.version_id, run.thread_id),
        graph,
        {"trigger": dict(params or {}), "form": {}, "vars": {}},
        thread_id=run.thread_id,
    )
    waiting_node = None
    if result.status == "waiting" and isinstance(result.interrupt, dict):
        waiting_node = str(result.interrupt.get("node_key") or "") or None
    _mirror(db, run, result, waiting_node=waiting_node)
    return {"run": run, "result": result}


def start_event_run(
    db: Session, *, definition_id: int, event_type: str, event_id: str,
    params: dict[str, Any], actor,
) -> dict[str, Any]:
    """内部生产方调用：确定性 thread_id 让重复事件只执行一次。"""
    service.require_management(actor)
    if not event_type or not event_id or len(event_type) > 64 or len(event_id) > 191:
        raise BizError(422, "事件类型与事件 ID 必填，且长度不得超过限制")
    if not isinstance(params, dict):
        raise BizError(422, "事件参数必须是对象")
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"workflow:{definition_id}:{event_type}:{event_id}").hex
    thread_id = f"event_{token}"
    existing = db.execute(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)).scalar_one_or_none()
    if existing is not None:
        return {"run": existing, "result": None, "duplicate": True}
    try:
        run = service.start_run(
            db, definition_id=definition_id, thread_id=thread_id, params=params,
            trigger_type="event", trigger_ref=event_id, created_by=actor.id,
        )
    except BizError as exc:
        if exc.code != 409:
            raise
        duplicate = db.execute(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)).scalar_one_or_none()
        if duplicate is None:
            raise
        return {"run": duplicate, "result": None, "duplicate": True}
    graph = _build(run, db)
    result = get_engine().start(
        graph_name_for(run.version_id, run.thread_id), graph,
        {"trigger": dict(params), "form": {}, "vars": {}}, thread_id=run.thread_id,
    )
    waiting_node = None
    if result.status == "waiting" and isinstance(result.interrupt, dict):
        waiting_node = str(result.interrupt.get("node_key") or "") or None
    _mirror(db, run, result, waiting_node=waiting_node)
    return {"run": run, "result": result, "duplicate": False}


def start_employee_assessment_run(
    db: Session, *, assignment_id: int, result_id: int, actor,
) -> dict[str, Any] | None:
    """由原测评分配人触发已完成的员工测评；未配置发布流程则跳过。"""
    service.require_management(actor)
    assignment = db.get(AssessmentAssignment, assignment_id)
    result = db.get(AssessmentResult, result_id)
    if assignment is None or result is None or result.assignment_id != assignment_id:
        raise BizError(404, "员工测评结果不存在")
    if assignment.purpose != "employee" or assignment.status != "completed":
        raise BizError(422, "仅已完成的员工测评可触发培训流程")
    if assignment.created_by != actor.id:
        raise BizError(403, "仅测评分配人可触发该员工的培训流程")
    definition_id = resolve_event_definition(db, event_code="employee_assessment_completed")
    if definition_id is None:
        return None
    return start_event_run(
        db, definition_id=definition_id, event_type="assessment.completed",
        event_id=f"{assignment.id}:v{result.version}",
        params={
            "assignment_id": assignment.id,
            "result_id": result.id,
            "employee_user_id": assignment.user_id,
        },
        actor=actor,
    )


# ---- 审批恢复 ----

def resume_approval(
    db: Session,
    *,
    thread_id: str,
    approved: bool,
    note: str | None,
    user,
) -> dict[str, Any]:
    """审批动作：管理侧 + 四眼 + 超时校验 → 引擎恢复 → 镜像。"""
    service.require_management(user)
    run = db.execute(
        select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
    ).scalar_one_or_none()
    if run is None:
        raise BizError(404, "运行实例不存在")
    if run.status != "waiting":
        raise BizError(409, f"运行不在等待态，当前 {run.status}")
    creator = run.created_by
    if creator is not None and getattr(user, "id", None) == creator:
        raise BizError(403, "四眼原则：流程发起人不可自行审批")

    # 当前挂起节点的审批负载（deadline/待办信息）从步骤记录读
    step = db.execute(
        select(WorkflowRunStep)
        .where(WorkflowRunStep.run_id == run.id, WorkflowRunStep.status == "waiting")
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    deadline = ((step.output_json or {}).get("interrupt") or {}).get("deadline") if step else None
    if deadline:
        try:
            if datetime.now() > datetime.fromisoformat(str(deadline)):
                raise BizError(409, "审批已超时（deadline 已过），需人工接管后重试")
        except ValueError as exc:
            raise BizError(422, f"审批 deadline 非法：{deadline!r}") from exc

    graph = _build(run, db)
    claimed = db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status == "waiting")
        .values(status="running")
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BizError(409, "等待态已被取消或其他审批占用")
    engine_claimed = db.execute(
        update(WorkflowEngineRun)
        .where(WorkflowEngineRun.thread_id == thread_id, WorkflowEngineRun.status == "waiting")
        .values(status="running")
    )
    if engine_claimed.rowcount != 1:
        db.rollback()
        raise BizError(409, "引擎等待态已变化，无法审批")
    db.commit()
    result = get_engine().resume(
        graph_name_for(run.version_id, run.thread_id),
        graph,
        thread_id,
        {
            "approved": bool(approved),
            "note": note or "",
            "by": getattr(user, "id", None),
            "at": datetime.now().isoformat(),
        },
    )
    _mirror(db, run, result)
    return {"run": run, "result": result}


def cancel_waiting_run(db: Session, *, thread_id: str, user) -> WorkflowRun:
    """仅取消人工等待中的运行；不打断执行中节点，不删除检查点。"""
    service.require_management(user)
    run = db.execute(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)).scalar_one_or_none()
    if run is None:
        raise BizError(404, "运行实例不存在")
    if run.status != "waiting":
        raise BizError(409, f"仅等待中的运行可取消，当前 {run.status}")
    changed = db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status == "waiting")
        .values(status="cancelled", finished_at=datetime.now(), error=None)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise BizError(409, "等待态已被其他操作占用")
    engine_changed = db.execute(
        update(WorkflowEngineRun)
        .where(WorkflowEngineRun.thread_id == thread_id, WorkflowEngineRun.status == "waiting")
        .values(status="cancelled", finished_at=datetime.now(), error=None)
    )
    if engine_changed.rowcount != 1:
        db.rollback()
        raise BizError(409, "引擎等待态已变化，无法取消")
    step = db.execute(
        select(WorkflowRunStep)
        .where(WorkflowRunStep.run_id == run.id, WorkflowRunStep.status == "waiting")
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if step is not None:
        step.output_json = {
            **(step.output_json or {}),
            "cancelled_by": user.id,
            "cancelled_at": datetime.now().isoformat(),
        }
    db.commit()
    db.refresh(run)
    return run


def cancel_running_run(db: Session, *, thread_id: str, user) -> WorkflowRun:
    """取消运行中的运行（T-D18）：管理侧；业务/引擎双侧条件更新置 cancelled。

    协作式中断：不打断引擎线程内已在执行的节点调用，后续节点在执行前检查
    业务状态（runtime 取消检查）停止推进；已取消运行不再被迟到回执覆盖
    （受控白名单拒绝 cancelled←failed/succeeded）。
    """
    service.require_management(user)
    run = db.execute(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)).scalar_one_or_none()
    if run is None:
        raise BizError(404, "运行实例不存在")
    if run.status != "running":
        raise BizError(409, f"仅运行中的运行可取消，当前 {run.status}")
    changed = db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status == "running")
        .values(status="cancelled", finished_at=datetime.now(),
                error=f"cancelled by user {user.id}")
    )
    if changed.rowcount != 1:
        db.rollback()
        raise BizError(409, "运行态已被其他操作占用")
    engine_changed = db.execute(
        update(WorkflowEngineRun)
        .where(WorkflowEngineRun.thread_id == thread_id, WorkflowEngineRun.status == "running")
        .values(status="cancelled", finished_at=datetime.now(),
                error=f"cancelled by user {user.id}")
    )
    if engine_changed.rowcount != 1:
        db.rollback()
        raise BizError(409, "引擎运行态已变化，无法取消")
    db.commit()
    db.refresh(run)
    return run


def retry_failed_run(db: Session, *, thread_id: str, user) -> dict[str, Any]:
    """失败阶段重试（T-D18）：管理侧；从失败检查点续跑，已成功节点不重执行。

    业务/引擎双侧 failed→running 仅经受控 retry 入口转移（CAS），再调引擎
    retry（invoke(None) 续跑）；终态由引擎回执经受控白名单镜像回来。
    """
    service.require_management(user)
    run = db.execute(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)).scalar_one_or_none()
    if run is None:
        raise BizError(404, "运行实例不存在")
    if run.status != "failed":
        raise BizError(409, f"仅失败的运行可重试，当前 {run.status}")
    graph = _build(run, db)
    claimed = db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status == "failed")
        .values(status="running", error=None, finished_at=None)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BizError(409, "失败态已被其他操作占用")
    engine_claimed = db.execute(
        update(WorkflowEngineRun)
        .where(WorkflowEngineRun.thread_id == thread_id, WorkflowEngineRun.status == "failed")
        .values(status="running", error=None, finished_at=None)
    )
    if engine_claimed.rowcount != 1:
        db.rollback()
        raise BizError(409, "引擎失败态已变化，无法重试")
    db.commit()
    result = get_engine().retry(
        graph_name_for(run.version_id, run.thread_id), graph, thread_id
    )
    _mirror(db, run, result)
    return {"run": run, "result": result}


# ---- 查询 ----

def list_runs(db: Session, *, definition_id: int | None = None,
              status: str | None = None) -> list[WorkflowRun]:
    stmt = select(WorkflowRun).order_by(WorkflowRun.id.desc())
    if definition_id is not None:
        stmt = stmt.where(WorkflowRun.definition_id == definition_id)
    if status:
        if status not in ("running", "waiting", "succeeded", "failed", "cancelled"):
            raise BizError(422, "运行状态非法")
        stmt = stmt.where(WorkflowRun.status == status)
    return list(db.scalars(stmt))


def run_detail(db: Session, thread_id: str) -> dict[str, Any]:
    run = db.execute(
        select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
    ).scalar_one_or_none()
    if run is None:
        raise BizError(404, "运行实例不存在")
    steps = list(
        db.scalars(
            select(WorkflowRunStep)
            .where(WorkflowRunStep.run_id == run.id)
            .order_by(WorkflowRunStep.id.asc())
        )
    )
    detail: dict[str, Any] = {
        "run": {
            "id": run.id,
            "definition_id": run.definition_id,
            "version_id": run.version_id,
            "thread_id": run.thread_id,
            "trigger_type": run.trigger_type,
            "trigger_ref": run.trigger_ref,
            "status": run.status,
            "error": run.error,
            "params": run.params_json,
            "created_by": run.created_by,
            "created_at": run.created_at,
            "finished_at": run.finished_at,
        },
        "steps": [
            {
                "node_key": s.node_key,
                "node_type": s.node_type,
                "attempt": s.attempt,
                "status": s.status,
                "output": s.output_json,
                "error": s.error,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
            }
            for s in steps
        ],
    }
    if run.status == "waiting":
        try:
            graph = _build(run, db)
            detail["engine_state"] = get_engine().state(
                graph_name_for(run.version_id, run.thread_id), graph, run.thread_id
            )
        except BizError:
            detail["engine_state"] = None
    return detail
