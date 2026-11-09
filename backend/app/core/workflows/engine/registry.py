"""引擎运行实例登记（T-D04）。

业务流程定义/节点/审批等模型归 T-D14（app/modules/workflows/），不在这里抢跑。
ORM 类见 app/core/workflows/engine/models.py（经 app.models 包导入）。
"""

from __future__ import annotations

from typing import Any, Callable


def mark_status(session_factory, thread_id: str, status: str, *, error: str | None = None) -> bool:
    """登记状态流转；行不存在时返回 False（登记缺失不应炸引擎，调用方记日志即可）。

    受控转换：仅允许 running→(waiting|succeeded|failed)、waiting→(running|succeeded|
    failed)、failed→running（失败重试，T-D18）、终态→cancelled 的方向被写入；
    cancelled 不再被任何结果覆盖——迟到的引擎回执不得复活已取消的运行
    （T-D18 等待态取消语义）。违反方向时返回 False。

    条件 UPDATE 原子判定转移方向（先读旧值仅供快速拒绝），取消与迟到回执的
    竞态在数据库行级判断下收敛为「先到先得」。
    """
    from datetime import datetime as _dt

    from app.models import WorkflowEngineRun
    from app.core.workflows.engine.models import ENGINE_RUN_STATUSES
    from sqlalchemy import select, update

    if status not in ENGINE_RUN_STATUSES:
        raise ValueError(f"非法引擎状态 {status!r}，仅允许 {ENGINE_RUN_STATUSES}")
    ALLOWED: dict[str, set[str]] = {
        "running": {"waiting", "succeeded", "failed"},
        "waiting": {"running", "succeeded", "failed"},
        "succeeded": {"cancelled"},
        "failed": {"cancelled", "running"},
        "cancelled": set(),
    }
    with session_factory() as db:
        row = db.execute(
            select(WorkflowEngineRun.status).where(WorkflowEngineRun.thread_id == thread_id)
        ).first()
        if row is None:
            return False
        if status not in ALLOWED.get(row[0], set()):
            return False
        # CAS 锁定源状态：读判写之间被并发改动时 rowcount=0，拒绝覆盖
        result = db.execute(
            update(WorkflowEngineRun)
            .where(
                WorkflowEngineRun.thread_id == thread_id,
                WorkflowEngineRun.status == row[0],
            )
            .values(
                status=status,
                error=error,
                finished_at=(
                    _dt.now() if status in ("succeeded", "failed", "cancelled") else None
                ),
            )
        )
        db.commit()
        return bool(result.rowcount)


def register_run(session_factory, *, graph_name: str, thread_id: str) -> None:
    """start 时登记 running；同 thread_id 重复 start 视为程序错误，抛给调用方。"""
    from app.models import WorkflowEngineRun

    with session_factory() as db:
        db.add(WorkflowEngineRun(graph_name=graph_name, thread_id=thread_id, status="running"))
        db.commit()


def recover_registry(session_factory) -> dict[str, int]:
    """进程重启后的恢复：孤儿 running → failed（可重试）；**waiting 一律不碰**。

    返回 {recovered: n, waiting: n}；waiting 数量原样保留（审计可见）。
    """
    from datetime import datetime as _dt

    from sqlalchemy import func, select, update

    from app.models import WorkflowEngineRun
    from app.core.workflows.engine.models import RECOVERABLE_STATUSES

    with session_factory() as db:
        waiting = int(
            db.execute(
                select(func.count()).select_from(WorkflowEngineRun).where(
                    WorkflowEngineRun.status == "waiting"
                )
            ).scalar_one()
            or 0
        )
        result = db.execute(
            update(WorkflowEngineRun)
            .where(WorkflowEngineRun.status.in_(RECOVERABLE_STATUSES))
            .values(status="failed", error="engine recovery: process restarted mid-run", finished_at=_dt.now())
        )
        db.commit()
        return {"recovered": int(result.rowcount or 0), "waiting": waiting}
