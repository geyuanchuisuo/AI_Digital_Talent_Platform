"""Business task runtime: fresh authorization, short DB locks, fenced writes."""
from datetime import datetime
import threading
from sqlalchemy import select, update
from app.config import settings
from app.modules.knowledge import service


class LeaseLost(Exception):
    pass


class Runtime:
    def __init__(self, model, task_id, token, factory):
        self.model, self.id, self.token, self.factory = model, task_id, token, factory
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, name=f'knowledge-heartbeat-{task_id}', daemon=True)

    def _where(self):
        return (self.model.id == self.id, self.model.status == 'running', self.model.lease_token == self.token)

    def _heartbeat(self):
        interval = min(5.0, max(1, settings.task_heartbeat_timeout) / 3)
        while not self.stop.wait(interval):
            try:
                with self.factory() as db:
                    result = db.execute(update(self.model).where(*self._where()).values(heartbeat_at=datetime.now()))
                    db.commit()
                    if result.rowcount != 1:
                        return
            except Exception:
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)

    def guard(self, db, *, final=False):
        db.rollback()
        db.expire_all()
        stmt = select(self.model).where(*self._where())
        row = db.scalar(stmt.with_for_update() if final else stmt)
        if row is None:
            raise LeaseLost()
        return row

    def write(self, db, *, before_commit=None, **values):
        result = db.execute(update(self.model).where(*self._where()).values(heartbeat_at=datetime.now(), **values))
        if result.rowcount == 1 and before_commit is not None:
            before_commit()
        db.commit()
        return result.rowcount == 1

    def fail(self, db, exc, *, stage):
        db.rollback()
        row = db.get(self.model, self.id)
        if row is None:
            return
        from app.core.errors import BizError
        retry = (not isinstance(exc, BizError) or exc.code >= 500) and row.attempt_count < row.max_attempts
        self.write(db, status='pending' if retry else 'failed', stage=stage, lease_token=None,
            error='处理暂不可用，等待重试' if retry else '处理未完成，请核对权限、来源及依赖后重试',
            finished_at=None if retry else datetime.now())
