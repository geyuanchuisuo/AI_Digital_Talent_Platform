"""C06 真实 MySQL 回归：并发报名不超卖 + 候补递补写入 21 字符状态。

默认跳过（普通套件不依赖 MySQL）。运行方式：

    C06_MYSQL_TEST_URL="mysql+pymysql://root:***@127.0.0.1:3307/c06_regress_local?charset=utf8mb4" \
      backend/.venv/Scripts/python.exe -m pytest tests/C/test_c06_mysql_regression.py -v

约束：URL 指向的库名必须以 `c06_regress_` 开头；测试会 DROP/CREATE 该库并执行
`alembic upgrade head`，结束后再 DROP，防止误连业务库。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import unittest
import uuid
from datetime import datetime, timedelta

from c_harness import BACKEND_DIR  # noqa: F401 - 同时注入 app 导入路径与配置占位

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import User
from app.modules.training import service
from app.modules.training.models import TrainingCourse, TrainingEnrollment
from app.modules.training.schemas import EnrollmentRequest

MYSQL_URL = os.getenv("C06_MYSQL_TEST_URL", "").strip()
DB_NAME_RE = re.compile(r"^c06_regress_[A-Za-z0-9_]+$")
WAITLIST_STATUS = "awaiting_confirmation"


class EnrollmentStatusWidthModelTest(unittest.TestCase):
    """模型列宽必须覆盖最长状态；MySQL 回归未启用时也由普通套件守护。"""

    def test_model_width_covers_waitlist_status(self):
        column = TrainingEnrollment.__table__.c.status
        self.assertGreaterEqual(
            column.type.length or 0,
            len(WAITLIST_STATUS),
            "training_enrollments.status 必须容纳 awaiting_confirmation",
        )


@unittest.skipUnless(MYSQL_URL, "set C06_MYSQL_TEST_URL to a disposable MySQL database")
class C06MySQLRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.url = make_url(MYSQL_URL)
        cls.db_name = cls.url.database or ""
        if not DB_NAME_RE.fullmatch(cls.db_name):
            raise unittest.SkipTest("C06_MYSQL_TEST_URL 库名必须匹配 c06_regress_*")
        if not cls.url.username:
            raise unittest.SkipTest("C06_MYSQL_TEST_URL 缺少用户名")
        # URL.set(database=None) 保留原库名，必须传空串才能连到服务器级
        cls.server_engine = create_engine(
            cls.url.set(database=""), isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        cls._drop_database()
        try:
            with cls.server_engine.connect() as conn:
                conn.execute(text(f"CREATE DATABASE `{cls.db_name}` CHARACTER SET utf8mb4"))
            cls._migrate()
            cls.engine = create_engine(MYSQL_URL, pool_pre_ping=True)
            cls.SessionLocal = sessionmaker(bind=cls.engine, expire_on_commit=False)
        except Exception:
            cls._drop_database()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls._drop_database()
        cls.server_engine.dispose()

    @classmethod
    def _drop_database(cls) -> None:
        with cls.server_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{cls.db_name}`"))

    @classmethod
    def _migrate(cls) -> None:
        env = os.environ.copy()
        env.update(
            {
                "MYSQL_HOST": cls.url.host or "127.0.0.1",
                "MYSQL_PORT": str(cls.url.port or 3306),
                "MYSQL_USER": cls.url.username or "root",
                "MYSQL_PASSWORD": cls.url.password or "",
                "MYSQL_DB": cls.db_name,
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "alembic upgrade head 失败：\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )

    def _create_user(self, username: str) -> int:
        db = self.SessionLocal()
        try:
            user = User(
                username=username,
                password_hash="x",
                display_name=username,
                role="employee",
                is_active=1,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user.id
        finally:
            db.close()

    def _create_course(self, capacity: int = 1) -> int:
        db = self.SessionLocal()
        try:
            course = TrainingCourse(
                title=f"C06 并发回归 {uuid.uuid4().hex[:8]}",
                description="mysql regression",
                material_url="http://example.test/c06",
                capacity=capacity,
                approval_mode="admin",
            )
            db.add(course)
            db.commit()
            db.refresh(course)
            return course.id
        finally:
            db.close()

    def _apply(self, course_id: int, user_id: int) -> tuple[int, str]:
        db = self.SessionLocal()
        try:
            user = db.get(User, user_id)
            row = service.apply_enrollment(db, EnrollmentRequest(course_id=course_id), user=user)
            return row.id, row.status
        finally:
            db.close()

    def _concurrent_apply(self, course_id: int, user_ids: list[int]) -> list[tuple]:
        barrier = threading.Barrier(len(user_ids))
        results: queue.Queue[tuple] = queue.Queue()

        def worker(user_id: int) -> None:
            db = self.SessionLocal()
            try:
                # 模拟真实请求：鉴权查询先建立 REPEATABLE READ 快照，再加课程行锁
                user = db.scalar(select(User).where(User.id == user_id))
                barrier.wait(timeout=15)
                row = service.apply_enrollment(
                    db, EnrollmentRequest(course_id=course_id), user=user
                )
                results.put(("ok", user_id, row.status))
            except Exception as exc:  # noqa: BLE001 - 汇总给断言展示
                results.put(("err", user_id, f"{type(exc).__name__}: {exc}"))
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(uid,)) for uid in user_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=45)
        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f"并发报名线程未在超时内结束：{alive}")
        return [results.get(timeout=2) for _ in user_ids]

    def test_status_column_accepts_waitlist_promotion(self):
        with self.engine.connect() as conn:
            columns = {c["name"]: c for c in inspect(conn).get_columns("training_enrollments")}
        self.assertGreaterEqual(
            columns["status"]["type"].length or 0,
            len(WAITLIST_STATUS),
            "迁移后 training_enrollments.status 必须能写入 awaiting_confirmation",
        )

    def test_concurrent_enrollment_does_not_oversell(self):
        with self.engine.connect() as conn:
            isolation = conn.scalar(text("SELECT @@transaction_isolation"))
        self.assertEqual(
            str(isolation).upper(), "REPEATABLE-READ",
            "该缺陷只在 REPEATABLE READ 快照读下复现，回归环境须匹配",
        )
        course_id = self._create_course(capacity=1)
        user_ids = [
            self._create_user(f"c06_conc_{uuid.uuid4().hex[:8]}")
            for _ in range(2)
        ]
        results = self._concurrent_apply(course_id, user_ids)
        errors = [item[2] for item in results if item[0] == "err"]
        self.assertEqual(errors, [], f"并发报名出现异常：{errors}")
        statuses = sorted(item[2] for item in results if item[0] == "ok")
        self.assertEqual(
            statuses,
            ["pending", "waitlisted"],
            "容量 1 的课程并发报名只能有一个正式占位，另一个必须候补",
        )
        with self.SessionLocal() as db:
            occupying = db.scalar(
                select(func.count())
                .select_from(TrainingEnrollment)
                .where(
                    TrainingEnrollment.course_id == course_id,
                    TrainingEnrollment.status.in_(("pending", "pending_admin_review", "approved")),
                )
            )
        self.assertEqual(int(occupying or 0), 1)

    def test_waitlist_promotion_persists_awaiting_confirmation(self):
        course_id = self._create_course(capacity=1)
        first_user, second_user = [
            self._create_user(f"c06_promo_{uuid.uuid4().hex[:8]}")
            for _ in range(2)
        ]
        first_id, first_status = self._apply(course_id, first_user)
        second_id, second_status = self._apply(course_id, second_user)
        self.assertEqual(first_status, "pending")
        self.assertEqual(second_status, "waitlisted")

        with self.SessionLocal() as db:
            db.get(TrainingEnrollment, first_id).expires_at = datetime.now() - timedelta(hours=1)
            db.commit()

        with self.SessionLocal() as db:
            out = service.release_expired_enrollments(db)
        self.assertEqual(out["released"], 1, out)
        self.assertEqual(out["promoted"], 1, out)

        with self.SessionLocal() as db:
            self.assertEqual(
                db.get(TrainingEnrollment, second_id).status,
                WAITLIST_STATUS,
                "候补递补必须在 MySQL 成功写入 awaiting_confirmation",
            )


if __name__ == "__main__":
    unittest.main()