"""工作流定时触发扫描器（T-D18，默认关闭）。

职责边界：只做「开关检查 → 参与定义枚举 → 到期判定 → 受控创建运行」，不含
任何业务节点逻辑；运行本体走 run_service.start_manual_run 同族的受控路径
（trigger_type="schedule"，钉发布版本、引擎执行、状态镜像全复用）。

验收④口径（统一需求/AGENTS §5）：
- **默认关闭**：`sys_settings.workflow_schedule_enabled` 缺行或非 "1" 一律不创建；
- **管理员启用**：开关与参与定义列表只经管理侧 API 修改（router 落 sys_settings，
  updated_by 留审计）；
- **审计**：启停动作落 sys_settings（updated_by/updated_at）；每次定时创建的运行
  trigger_type="schedule" 在 workflow_runs 可查。

到期判定：对每个参与定义取最近一次 schedule 触发的运行，created_at 距今超过
interval（分钟）才创建新运行；定义没有发布版本或从未运行过则首轮即创建。
检查与创建之间不持锁——并发创建最多造成一次重复窗口，由 start_manual_run 的
受控路径兜底（一期单进程 + 30s 扫描间隔，实际风险可忽略）。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.workflows import run_service
from app.modules.workflows.models import (
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowRun,
)

logger = logging.getLogger("app.workflow_schedule")

ENABLED_KEY = "workflow_schedule_enabled"
INTERVAL_KEY = "workflow_schedule_interval_minutes"
DEFS_KEY = "workflow_schedule_definitions"
ACTOR_KEY = "workflow_schedule_actor_user_id"

SCAN_INTERVAL_SECONDS = 30
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440


def _get_setting(key: str, default: str | None = None) -> str | None:
    """sys_settings 读取统一入口（get_value 走全局 SessionLocal——配置在请求之外也生效）。

    模块级函数便于测试注入（测试库与全局 SessionLocal 不同源）。
    """
    from app.dao.sys_settings import get_value

    return get_value(key, default)


def schedule_settings(db) -> dict[str, Any]:
    """读当前定时配置（管理侧 GET 用；缺行 = 默认关闭）。"""
    from app.dao.sys_settings import get_int

    enabled = (_get_setting(ENABLED_KEY, "0") or "0").strip() == "1"
    interval = get_int(db, INTERVAL_KEY, 60)
    interval = max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, interval))
    raw_defs = _get_setting(DEFS_KEY, "") or ""
    codes = [c.strip() for c in raw_defs.split(",") if c.strip()]
    return {
        "enabled": enabled,
        "interval_minutes": interval,
        "definition_codes": codes,
        "actor_user_id": _resolve_actor_id(db),
    }


def update_schedule_settings(
    db, *, enabled: bool, interval_minutes: int | None,
    definition_codes: list[str] | None, user, actor_user_id: int | None = None,
) -> dict[str, Any]:
    """管理员启停/配置定时触发（管理侧；不 commit，调用方事务收口）。"""
    from app.dao.sys_settings import set_value

    run_service.service.require_management(user)
    values: dict[str, str] = {ENABLED_KEY: "1" if enabled else "0"}
    if interval_minutes is not None:
        if not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
            raise BizError(
                422,
                f"interval_minutes 须在 {MIN_INTERVAL_MINUTES}–{MAX_INTERVAL_MINUTES} 之间",
            )
        values[INTERVAL_KEY] = str(interval_minutes)
    if definition_codes is not None:
        seen: list[str] = []
        for code in definition_codes:
            code = code.strip()
            if not code:
                continue
            if code not in seen:
                seen.append(code)
        values[DEFS_KEY] = ",".join(seen)
    for key, value in values.items():
        set_value(db, key, value, value_type="str", updated_by=getattr(user, "id", None))
    if actor_user_id is not None:
        set_value(db, ACTOR_KEY, str(actor_user_id), value_type="int",
                  updated_by=getattr(user, "id", None))
    db.commit()
    settings = schedule_settings(db)
    settings["actor_user_id"] = _resolve_actor_id(db)
    return settings


class WorkflowScheduleLoop:
    """daemon 线程扫描器；main.py lifespan 启停，测试可注入 session_factory。"""

    def __init__(self, *, session_factory: Callable[[], Any] = SessionLocal,
                 scan_interval: float = SCAN_INTERVAL_SECONDS) -> None:
        self._session_factory = session_factory
        self._scan_interval = scan_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wf-schedule", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.wait(self._scan_interval):
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001 - 扫描失败不杀线程，下轮重试
                logger.exception("定时触发扫描失败（下轮重试）")

    def scan_once(self) -> list[int]:
        """单轮扫描：返回本轮创建的 run id 列表。开关关闭时为空操作。"""
        created: list[int] = []
        if (_get_setting(ENABLED_KEY, "0") or "0").strip() != "1":
            return created
        with self._session_factory() as db:
            settings = schedule_settings(db)
            now = datetime.now()
            for code in settings["definition_codes"]:
                definition = db.execute(
                    select(WorkflowDefinition).where(WorkflowDefinition.code == code)
                ).scalar_one_or_none()
                if definition is None:
                    logger.warning("定时触发：定义 code=%s 不存在，跳过", code)
                    continue
                last = db.execute(
                    select(WorkflowRun.created_at)
                    .where(
                        WorkflowRun.definition_id == definition.id,
                        WorkflowRun.trigger_type == "schedule",
                    )
                    .order_by(WorkflowRun.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if last is not None and now - last < timedelta(minutes=settings["interval_minutes"]):
                    continue
                run = self._create_run(code)
                if run is not None:
                    created.append(run.id)
        return created

    def _create_run(self, code: str):
        """按受控路径创建并执行一次 schedule 运行；无发布版本/执行主体时跳过（记日志）。

        执行身份：`workflow_schedule_actor_user_id` 指定的管理侧账号（节点域服务
        的权限主体）。未配置或账号无效时只登记运行（trigger_type=schedule 留审计）
        不执行引擎——运行停在 running 会被下一轮识别，如实失败优于静默跳过。
        """
        with self._session_factory() as db:
            definition = db.execute(
                select(WorkflowDefinition).where(WorkflowDefinition.code == code)
            ).scalar_one_or_none()
            if definition is None:
                return None
            has_published = db.execute(
                select(WorkflowDefinitionVersion.id)
                .where(
                    WorkflowDefinitionVersion.definition_id == definition.id,
                    WorkflowDefinitionVersion.status == "published",
                )
                .limit(1)
            ).first()
            if has_published is None:
                logger.info("定时触发：定义 %s 无发布版本，跳过", code)
                return None
            run = run_service.service.start_run(
                db,
                definition_id=definition.id,
                thread_id=f"schedule_{definition.id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                params=None,
                trigger_type="schedule",
                created_by=_resolve_actor_id(db),
            )
        run_service.execute_registered_run(run)
        return run


def _resolve_actor_id(db) -> int | None:
    """读定时执行主体配置；缺行返回 None（登记但不执行，审计可见）。"""
    raw = _get_setting(ACTOR_KEY, "") or ""
    try:
        return int(raw) if raw.strip() else None
    except ValueError:
        return None


_loop: WorkflowScheduleLoop | None = None


def get_loop() -> WorkflowScheduleLoop:
    global _loop  # noqa: PLW0603
    if _loop is None:
        _loop = WorkflowScheduleLoop()
    return _loop


def set_loop(loop: WorkflowScheduleLoop | None) -> None:
    global _loop  # noqa: PLW0603
    _loop = loop
