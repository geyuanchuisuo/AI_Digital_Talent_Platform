"""工作流定时触发扫描器（T-D18 工作流 / T-D-SELF-09 数字员工，默认关闭）。

职责边界：只做「开关检查 → 参与对象枚举 → 到期判定 → 受控创建」，不含任何
业务节点逻辑；工作流运行本体走 run_service.start_manual_run 同族的受控路径
（trigger_type="schedule"，钉发布版本、引擎执行、状态镜像全复用）；数字员工
三类只读场景走 digital.service.run_task（trigger="schedule"，结果入库 + 站内
通知，绝不站外外发）。

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

数字员工（T-D-SELF-09）：独立开关/间隔/场景清单/通知收件人，与工作流定时互不
影响；默认关闭，未启用时零副作用。幂等由 digital_tasks.idempotency_key 承载
（同场景同周期窗口只落一行），到期判定为第二层限速。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from app.core.errors import BizError
from app.database import SessionLocal
from app.models import DigitalTask
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

# 数字员工定时（T-D-SELF-09）：独立开关与配置，默认关闭。
DIGITAL_ENABLED_KEY = "workflow_digital_schedule_enabled"
DIGITAL_INTERVAL_KEY = "workflow_digital_schedule_interval_minutes"
DIGITAL_TASK_TYPES_KEY = "workflow_digital_schedule_task_types"
DIGITAL_NOTIFY_ADMINS_KEY = "workflow_digital_schedule_notify_admins"

SCAN_INTERVAL_SECONDS = 30
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
DIGITAL_DEFAULT_INTERVAL_MINUTES = 60


def _get_setting(key: str, default: str | None = None) -> str | None:
    """sys_settings 读取统一入口（get_value 走全局 SessionLocal——配置在请求之外也生效）。

    模块级函数便于测试注入（测试库与全局 SessionLocal 不同源）。
    """
    from app.dao.sys_settings import get_value

    return get_value(key, default)


def schedule_settings(db) -> dict[str, Any]:
    """读当前定时配置（管理侧 GET 用；缺行 = 默认关闭）。

    工作流与数字员工定时相互独立，数字员工作嵌套 `digital` 对象返回。
    """
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
        "digital": digital_schedule_settings(db),
    }


def digital_schedule_settings(db) -> dict[str, Any]:
    """数字员工定时配置（独立开关；缺行 = 默认关闭）。

    场景清单读取时按 TASK_TYPES 白名单过滤，非法项不静默混入执行；
    写入时则直接 422 拒绝（见 update_schedule_settings）。
    """
    from app.dao.sys_settings import get_int

    from app.modules.workflows.digital.models import TASK_TYPES  # noqa: PLC0415

    enabled = (_get_setting(DIGITAL_ENABLED_KEY, "0") or "0").strip() == "1"
    interval = get_int(db, DIGITAL_INTERVAL_KEY, DIGITAL_DEFAULT_INTERVAL_MINUTES)
    interval = max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, interval))
    raw_types = _get_setting(DIGITAL_TASK_TYPES_KEY, "") or ""
    task_types = [
        t.strip() for t in raw_types.split(",")
        if t.strip() and t.strip() in TASK_TYPES
    ]
    raw_admins = _get_setting(DIGITAL_NOTIFY_ADMINS_KEY, "") or ""
    notify_admin_ids: list[int] = []
    for item in raw_admins.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value > 0 and value not in notify_admin_ids:
            notify_admin_ids.append(value)
    return {
        "enabled": enabled,
        "interval_minutes": interval,
        "task_types": task_types,
        "notify_admin_ids": notify_admin_ids,
    }


def update_schedule_settings(
    db, *, enabled: bool, interval_minutes: int | None,
    definition_codes: list[str] | None, user, actor_user_id: int | None = None,
    digital_enabled: bool | None = None,
    digital_interval_minutes: int | None = None,
    digital_task_types: list[str] | None = None,
    digital_notify_admin_ids: list[int] | None = None,
) -> dict[str, Any]:
    """管理员启停/配置定时触发（管理侧；不 commit，调用方事务收口）。

    工作流与数字员工定时参数均可选；传入才写，未传保持原值。数字员工场景
    清单写入前按 TASK_TYPES 白名单校验，非法项直接 422（不静默过滤）。
    """
    from app.dao.sys_settings import set_value

    from app.modules.workflows.digital.models import TASK_TYPES  # noqa: PLC0415

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
    if digital_enabled is not None:
        values[DIGITAL_ENABLED_KEY] = "1" if digital_enabled else "0"
    if digital_interval_minutes is not None:
        if not MIN_INTERVAL_MINUTES <= digital_interval_minutes <= MAX_INTERVAL_MINUTES:
            raise BizError(
                422,
                f"digital_interval_minutes 须在 {MIN_INTERVAL_MINUTES}–{MAX_INTERVAL_MINUTES} 之间",
            )
        values[DIGITAL_INTERVAL_KEY] = str(digital_interval_minutes)
    if digital_task_types is not None:
        seen_types: list[str] = []
        for task_type in digital_task_types:
            task_type = (task_type or "").strip()
            if not task_type:
                continue
            if task_type not in TASK_TYPES:
                raise BizError(
                    422,
                    f"数字员工场景 {task_type!r} 不在白名单（{', '.join(TASK_TYPES)}）",
                )
            if task_type not in seen_types:
                seen_types.append(task_type)
        values[DIGITAL_TASK_TYPES_KEY] = ",".join(seen_types)
    if digital_notify_admin_ids is not None:
        seen_admins: list[int] = []
        for admin_id in digital_notify_admin_ids:
            if not isinstance(admin_id, int) or isinstance(admin_id, bool) or admin_id <= 0:
                raise BizError(422, "notify_admin_ids 须为正整数数组")
            if admin_id not in seen_admins:
                seen_admins.append(admin_id)
        values[DIGITAL_NOTIFY_ADMINS_KEY] = ",".join(str(a) for a in seen_admins)
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
        """单轮扫描：返回本轮创建的对象 id 列表（工作流 run + 数字员工 task）。

        工作流与数字员工定时相互独立：任一开关关闭时该侧为空操作，互不影响。
        """
        created: list[int] = []
        if (_get_setting(ENABLED_KEY, "0") or "0").strip() == "1":
            created.extend(self._scan_workflow_definitions())
        created.extend(self._scan_digital_tasks())
        return created

    def _scan_workflow_definitions(self) -> list[int]:
        """工作流定义定时扫描（原 scan_once 主体）。"""
        created: list[int] = []
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

    def _scan_digital_tasks(self) -> list[int]:
        """数字员工定时扫描（T-D-SELF-09）：独立开关，默认关闭时零副作用。

        到期判定：对每个参与场景取最近一次 trigger=schedule 的任务，created_at
        距今超过 interval 才执行；idempotency_key 按「场景 + 周期窗口」生成，
        同窗口重入只落一行（digital_tasks.uk_digital_tasks_idem 兼底）。
        """
        if (_get_setting(DIGITAL_ENABLED_KEY, "0") or "0").strip() != "1":
            return []
        from app.modules.workflows.digital import service as digital_service  # noqa: PLC0415

        created: list[int] = []
        with self._session_factory() as db:
            settings = digital_schedule_settings(db)
            now = datetime.now()
            window_seconds = settings["interval_minutes"] * 60
            for task_type in settings["task_types"]:
                last = db.execute(
                    select(DigitalTask.created_at)
                    .where(
                        DigitalTask.task_type == task_type,
                        DigitalTask.trigger == "schedule",
                    )
                    .order_by(DigitalTask.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if last is not None and now - last < timedelta(minutes=settings["interval_minutes"]):
                    continue
                window_index = int(now.timestamp() // window_seconds)
                task, created_now = digital_service.run_task(
                    db,
                    task_type=task_type,
                    idempotency_key=f"digital:schedule:{task_type}:{window_index}",
                    notify_admin_ids=settings["notify_admin_ids"],
                    trigger="schedule",
                    actor=None,
                    triggered_by=None,
                )
                # 仅本轮真正新建才计入返回值；同窗口幂等重入不算新任务
                if created_now:
                    created.append(task.id)
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
