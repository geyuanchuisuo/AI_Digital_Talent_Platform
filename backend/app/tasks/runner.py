"""TaskRunner 常驻任务框架（T-12，架构 §5.4 / 决策 D-1 / D-C / D-I）。

职责边界：**只做"扫描 → 认领 → 派发 → 心跳/恢复"**，不含任何业务解析逻辑。
各任务的执行体（parse/screen/match worker）在 T-13 / T-24 / T-25 实现后通过
`runner.register(kind, handler)` 挂进来；handler 接收任务主键，自己开 session、自己写
终态（success/done/failed）与 error。这样 runner 可以独立测试，也不会把三类业务
耦合在一个文件里。

关键口径（与架构逐条对应，改前先读文档）：
1. **只消费 `pending` 与心跳超时的 `running`**：`duplicate_pending`（等人工 resolve）、
   `cancelled`（软取消）**永不扫描**；screen/match 的 `done` 同理不扫。
2. **乐观锁认领**：`UPDATE ... SET status='running', started_at=..., heartbeat_at=...
   WHERE id=? AND status='pending'`，按 `rowcount` 判定，抢不到就跳过；两个 runner 同时
   扫同一行也只有一个能成功。
3. **三类任务共用一个线程池**：并发数每周期从 `sys_settings.task_concurrency` 读
   （1–5），**只有当前无在跑任务时才重建池**；有任务在跑就等它结束的下一周期，
   最迟一个周期生效，无需重启且重启后保持。
4. **启动无条件恢复**：一期单进程，启动时不可能有真在跑的任务，因此把三类
   `running` 全部重置 `pending`，**保留 `stage` / `progress`**；`TASK_HEARTBEAT_TIMEOUT`
   （10 分钟）只用于**运行期**任务卡死。
5. **优雅停止**：等 `TASK_SHUTDOWN_TIMEOUT`（30s）；超时未完成的任务回置 `pending`
   并保留断点。Python 线程无法强杀，进程退出前残留的 handler 可能再写一次终态，
   这是单进程部署下的已知边界（多进程会重复扫描，故 `--workers 1` 是硬约束）。
6. **健康上报**：每轮把 alive/last_heartbeat/backlog 写到 `core.worker_state`，
   由 `GET /api/health` 读取；backlog 含 `duplicate_pending` / `cancelled`。

一期没有 handler 时 `run_once()` 不认领任何任务（只做恢复与上报），
因此 T-12 落地后应用可以安全启动，不会把 pending 任务卡在 running。

B08 扩展：profile_qa 共用线程池，认领时携带独立lease_token及有限执行次数。
制度知识入库knowledge_ingest与问答knowledge_qa沿用同一fenced机制。
该类型的恢复会废弃旧凭证；执行体、异常兜底和停止回置均校验凭证，避免迟到写入。
parse/screen/match 保持原有单参数handler及恢复语义；单进程部署前提不变。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.core import worker_state
from app.database import SessionLocal
from app.models import MatchBatch, ParseTask, ScreenBatch
from app.models.talent import ProfileQuestionJob
from app.modules.knowledge.models import KnowledgeRevision, KnowledgeQuestionJob
from app.modules.talents.graph_models import GraphSyncJob

logger = logging.getLogger("app.runner")

#: 旧handler接收主键；fenced类型额外接收认领凭证，业务自行开session。
TaskHandler = Callable[..., None]
ClaimedTask = int | tuple[int, str]

MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 5


@dataclass(frozen=True)
class _Kind:
    name: str
    model: type
    label: str
    fenced: bool = False


#: 扫描顺序：解析优先（上传后最需要及时反馈），其余按批次类型
KINDS: tuple[_Kind, ...] = (
    _Kind("parse", ParseTask, "解析"),
    _Kind("screen", ScreenBatch, "筛选"),
    _Kind("match", MatchBatch, "匹配"),
    _Kind("profile_qa", ProfileQuestionJob, "档案问答", fenced=True),
    _Kind("knowledge_ingest", KnowledgeRevision, "知识入库", fenced=True),
    _Kind("knowledge_qa", KnowledgeQuestionJob, "制度问答", fenced=True),
    _Kind("graph_sync", GraphSyncJob, "图谱同步", fenced=True),
)

KIND_NAMES: tuple[str, ...] = tuple(kind.name for kind in KINDS)


class TaskRunner:
    """单例任务调度器；`app/main.py` lifespan 启停，测试可自行 new 一个实例。"""

    def __init__(self, *, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory
        self._handlers: dict[str, TaskHandler] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._concurrency = 0
        #: key=(kind, task_id)；先占位再提交，避免"任务跑完才登记"导致漏减
        self._inflight: dict[tuple[str, int], Future | None] = {}
        self._tokens: dict[tuple[str, int], str] = {}
        self._last_heartbeat: str | None = None

    # ------------------------------------------------------------ 生命周期

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def register(self, kind: str, handler: TaskHandler) -> None:
        """挂执行体（T-13 起由 main 在启动时注册）。

        重复注册视为覆盖（便于测试替换），但 kind 必须合法；拼错类别属于编程错误，
        直接抛 ValueError 而不是静默忽略。
        """
        if kind not in KIND_NAMES:
            raise ValueError(f"未知任务类型：{kind}（可选 {KIND_NAMES}）")
        if kind in self._handlers:
            logger.warning("runner: 任务类型 %s 的 handler 被覆盖", kind)
        self._handlers[kind] = handler

    def start(self) -> None:
        """启动主循环；重复调用无副作用。启动前先做无条件恢复（保留 stage）。"""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()

        self._startup_recovery()
        self._sync_executor(self._desired_concurrency(), force=True)

        thread = threading.Thread(target=self._loop, name="task-runner", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        logger.info(
            "runner: 已启动 scan=%.1fs concurrency=%d handlers=%s",
            self._scan_interval(),
            self._concurrency,
            sorted(self._handlers) or "-",
        )

    def unregister(self, kind: str) -> None:
        """Disable opt-in consumption before start; pending rows remain durable."""
        if self.is_running:
            raise RuntimeError('Cannot unregister a running task consumer')
        self._handlers.pop(kind, None)

    def stop(self, *, timeout: float | None = None) -> None:
        """优雅停止：等任务至 `TASK_SHUTDOWN_TIMEOUT`，超时的回置 pending 保留断点。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._scan_interval() + 5)

        with self._lock:
            futures = {key: future for key, future in self._inflight.items() if future is not None}

        not_done: set[Future] = set()
        if futures:
            _, not_done = wait(list(futures.values()), timeout=timeout or self._shutdown_timeout())
            for key, future in futures.items():
                if future in not_done:
                    logger.warning("runner: 停止超时，任务回置 pending：%s#%s", key[0], key[1])
                    self._reset_to_pending(key)

        with self._lock:
            executor, self._executor = self._executor, None
            self._concurrency = 0
            self._thread = None
        if executor is not None:
            executor.shutdown(wait=False)

        self._report_state(alive=False)
        logger.info("runner: 已停止（超时未完成 %d 个）", len(not_done))

    # ------------------------------------------------------------ 主循环

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - 单轮失败不能让常驻线程退出
                logger.exception("runner: 扫描周期异常，下一周期继续")
            self._stop_event.wait(self._scan_interval())

    def run_once(self) -> int:
        """执行一轮：读并发 → 恢复卡死 → 认领 → 派发 → 上报；返回本轮认领数。

        单测直接调用它验证调度行为，不必真的等 2s 主循环。
        """
        self._sync_executor(self._desired_concurrency())
        self._recover_stale()

        claimed_total = 0
        for kind in KINDS:
            if kind.name not in self._handlers:
                continue  # 没挂执行体就不认领，绝不把任务留在 running
            with self._lock:
                free = self._concurrency - len(self._inflight)
            if free <= 0:
                break
            claimed = self._claim(kind, free)
            if claimed:
                self._submit(kind, claimed)
                claimed_total += len(claimed)
            # 池可能在等任务期间被并发 stop 置空
            with self._lock:
                if self._executor is None:
                    break

        self._report_state()
        return claimed_total

    # ------------------------------------------------------------ 认领与派发

    def _claim(self, kind: _Kind, limit: int) -> list[ClaimedTask]:
        """扫描 + 逐行乐观锁；`rowcount == 1` 才算抢到。"""
        model = kind.model
        now = datetime.now()
        with self._session_factory() as db:
            if kind.fenced:
                db.execute(update(model).where(model.status == 'pending', model.attempt_count >= model.max_attempts)
                    .values(status='failed', lease_token=None,
                            error='已达到问答任务重试上限', finished_at=now))
            with self._lock:
                active_ids = [tid for name, tid in self._inflight if name == kind.name] if kind.fenced else []
            ids = list(
                db.execute(
                    select(model.id)
                    .where(model.status == "pending")
                    .where(model.id.not_in(active_ids))
                    .order_by(model.id)
                    .limit(limit)
                ).scalars()
            )
            claimed: list[ClaimedTask] = []
            for task_id in ids:
                token = str(uuid4()) if kind.fenced else None
                values = dict(status="running", started_at=now, heartbeat_at=now)
                if kind.fenced:
                    values.update(lease_token=token, attempt_count=model.attempt_count + 1, error=None)
                conditions = [model.id == task_id, model.status == "pending"]
                if kind.fenced:
                    conditions.append(model.attempt_count < model.max_attempts)
                result = db.execute(
                    update(model)
                    .where(*conditions)
                    .values(**values)
                )
                if result.rowcount == 1:
                    claimed.append((task_id, token) if kind.fenced else task_id)
            db.commit()
        if claimed:
            logger.info("runner: 认领%s任务 %s", kind.label, claimed)
        return claimed

    def _submit(self, kind: _Kind, task_ids: list[ClaimedTask]) -> None:
        with self._lock:
            executor = self._executor
        if executor is None:
            # stop 正在发生：把刚认领的任务放回 pending，避免卡在 running
            for claimed in task_ids:
                task_id, token = claimed if kind.fenced else (claimed, None)
                if token:
                    self._tokens[(kind.name, task_id)] = token
                self._reset_to_pending((kind.name, task_id))
            return

        for claimed in task_ids:
            task_id, token = claimed if kind.fenced else (claimed, None)
            key = (kind.name, task_id)
            with self._lock:
                self._inflight[key] = None  # 先占位：任务极快结束时也不会漏减
                if token:
                    self._tokens[key] = token
            try:
                future = executor.submit(self._execute, kind, task_id, token)
            except RuntimeError:
                # stop() 恰好在此刻关池：把任务放回 pending，等重启后续跑
                logger.warning("runner: 线程池已关闭，任务回置 pending：%s#%s", kind.name, task_id)
                self._reset_to_pending(key)
                continue
            with self._lock:
                if key in self._inflight:
                    self._inflight[key] = future

    def _execute(self, kind: _Kind, task_id: int, token: str | None = None) -> None:
        handler = self._handlers.get(kind.name)
        try:
            if handler is None:
                raise RuntimeError(f"任务类型 {kind.name} 的 handler 已注销")
            if kind.fenced:
                handler(task_id, token)
            else:
                handler(task_id)
        except Exception as exc:  # noqa: BLE001 - handler 未兜住的异常统一落 failed
            logger.exception("runner: %s任务 %s 执行异常", kind.label, task_id)
            self._mark_failed(kind, task_id, exc, token)
        finally:
            with self._lock:
                key = (kind.name, task_id)
                if not kind.fenced or self._tokens.get(key) == token:
                    self._inflight.pop(key, None)
                    self._tokens.pop(key, None)

    def _mark_failed(self, kind: _Kind, task_id: int, exc: Exception, token: str | None = None) -> None:
        """handler 没写终态就抛异常时的兜底：置 failed，避免永远 running。"""
        message = f"任务执行异常：{exc}"[:2000]
        try:
            with self._session_factory() as db:
                stmt = update(kind.model).where(kind.model.id == task_id, kind.model.status == "running")
                values = dict(status="failed", error=message, finished_at=datetime.now())
                if kind.fenced:
                    stmt = stmt.where(kind.model.lease_token == token)
                    values.update(self._recovery_values(kind))
                db.execute(stmt.values(**values))
                db.commit()
        except SQLAlchemyError:
            logger.exception("runner: 任务 %s#%s 置 failed 失败", kind.name, task_id)

    # ------------------------------------------------------------ 恢复

    @staticmethod
    def _recovery_values(kind):
        if not kind.fenced:
            return dict(status='pending', started_at=None, heartbeat_at=None)
        exhausted = kind.model.attempt_count >= kind.model.max_attempts
        return dict(status=case((exhausted, 'failed'), else_='pending'),
            stage=kind.model.stage,
            error=case((exhausted, '已达到问答任务重试上限'), else_=None),
            finished_at=case((exhausted, datetime.now()), else_=None),
            lease_token=None, started_at=None, heartbeat_at=None)

    def _startup_recovery(self) -> None:
        """启动无条件 running→pending，保留 stage/progress（单进程前提，架构 §5.4）。"""
        with self._session_factory() as db:
            for kind in KINDS:
                result = db.execute(
                    update(kind.model)
                    .where(kind.model.status == "running")
                    .values(**self._recovery_values(kind))
                )
                if result.rowcount:
                    logger.info("runner: 启动恢复 %s 个%s任务 running→pending", result.rowcount, kind.label)
            db.commit()

    def _recover_stale(self) -> None:
        """运行期心跳超时：`running` 且心跳早于阈值（或缺失）→ pending，保留断点。"""
        cutoff = datetime.now() - timedelta(seconds=self._heartbeat_timeout())
        with self._session_factory() as db:
            for kind in KINDS:
                model = kind.model
                result = db.execute(
                    update(model)
                    .where(
                        model.status == "running",
                        or_(model.heartbeat_at.is_(None), model.heartbeat_at < cutoff),
                    )
                    .values(**self._recovery_values(kind))
                )
                if result.rowcount:
                    logger.warning(
                        "runner: %s任务心跳超时，恢复 %d 个（cutoff=%s）",
                        kind.label,
                        result.rowcount,
                        cutoff.isoformat(timespec="seconds"),
                    )
            db.commit()

    def _reset_to_pending(self, key: tuple[str, int]) -> None:
        kind_name, task_id = key
        kind = next((item for item in KINDS if item.name == kind_name), None)
        if kind is None:
            return
        try:
            with self._session_factory() as db:
                stmt = update(kind.model).where(kind.model.id == task_id, kind.model.status == "running")
                if kind.fenced:
                    token = self._tokens.get(key)
                    if token is None:
                        return
                    stmt = stmt.where(kind.model.lease_token == token)
                db.execute(stmt.values(**self._recovery_values(kind)))
                db.commit()
        except SQLAlchemyError:
            logger.exception("runner: 回置 pending 失败 %s#%s", kind_name, task_id)
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                self._tokens.pop(key, None)

    # ------------------------------------------------------------ 配置与状态

    def _desired_concurrency(self) -> int:
        """每周期从 `sys_settings` 重读（运行配置 v1.3：表有值以表为准）。"""
        settings.load_runtime_overrides()
        return max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(settings.task_concurrency)))

    def _sync_executor(self, desired: int, *, force: bool = False) -> None:
        """无在跑任务时重建线程池；有任务在跑就等下一周期（D-C）。"""
        with self._lock:
            if self._inflight and not force:
                return
            if not force and self._concurrency == desired and self._executor is not None:
                return
            old, self._executor = self._executor, ThreadPoolExecutor(
                max_workers=desired, thread_name_prefix="task"
            )
            self._concurrency = desired
        if old is not None:
            old.shutdown(wait=False)
        logger.info("runner: 线程池并发=%d", desired)

    def _report_state(self, *, alive: bool | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._last_heartbeat = now
        worker_state.set_worker_state(
            alive=self.is_running if alive is None else alive,
            last_heartbeat=now,
            backlog=self._backlog(),
        )

    def _backlog(self) -> dict[str, int]:
        try:
            with self._session_factory() as db:
                return {
                    "parse": self._count(db, ParseTask, "pending"),
                    "screen": self._count(db, ScreenBatch, "pending"),
                    "match": self._count(db, MatchBatch, "pending"),
                    "duplicate_pending": self._count(db, ParseTask, "duplicate_pending"),
                    "cancelled": self._count(db, ParseTask, "cancelled"),
                }
        except SQLAlchemyError:
            logger.warning("runner: backlog 统计失败（下一周期重试）", exc_info=True)
            return {key: 0 for key in worker_state.BACKLOG_KEYS}

    @staticmethod
    def _count(db, model: type, status: str) -> int:
        return int(
            db.execute(select(func.count(model.id)).where(model.status == status)).scalar_one()
        )

    def snapshot(self) -> dict:
        """调试/测试用快照：进程内状态 + 当前在跑数与并发。"""
        data = worker_state.get_worker_state()
        with self._lock:
            data.update({"concurrency": self._concurrency, "inflight": len(self._inflight)})
        return data

    @staticmethod
    def _scan_interval() -> float:
        return max(0.1, float(settings.runner_scan_interval))

    @staticmethod
    def _heartbeat_timeout() -> int:
        return max(1, int(settings.task_heartbeat_timeout))

    @staticmethod
    def _shutdown_timeout() -> int:
        return max(1, int(settings.task_shutdown_timeout))


#: 全局单例：`main.py` lifespan 启停；测试要隔离时自行 `TaskRunner(...)`
runner = TaskRunner()
