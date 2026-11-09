"""C 模块测试公共夹具（stdlib unittest / pytest 均可运行）。

环境说明：
- 不依赖 MySQL/Milvus/真实模型：用 SQLite 持久化 + 测试内注入评分/草稿替身。
- 当前开发机未安装 pytest 与 python-jose；本夹具为 JWT 库注入最小替身，
  只为让 `app.api.deps` 可导入，测试本身不使用真实 token。
- 只创建 C 的 17 张表；`users` 表因公共模型使用 MySQL 专用类型，按同结构手写 SQLite DDL。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Iterator

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 配置必填项的最小占位值：测试不会发起真实 LLM/MySQL 连接
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9/v1")
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")

if "jose" not in sys.modules:
    jose_stub = types.ModuleType("jose")

    class JWTError(Exception):
        pass

    class ExpiredSignatureError(JWTError):
        pass

    jose_stub.JWTError = JWTError
    jose_stub.ExpiredSignatureError = ExpiredSignatureError
    jose_stub.jwt = types.SimpleNamespace(
        encode=lambda *args, **kwargs: "stub-token",
        decode=lambda *args, **kwargs: {},
    )
    sys.modules["jose"] = jose_stub

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine, text  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# 公共 PKMixin 使用 BIGINT；SQLite 只有 INTEGER PRIMARY KEY 才能自增
@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(type_, compiler, **kwargs):  # noqa: ANN001
    return "INTEGER"

from app.api.deps import get_current_user  # noqa: E402
from app.core.errors import BizError, register_exception_handlers  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models import User  # noqa: E402
from app.modules.assessments.registry import ALL_ROUTERS  # noqa: E402
from app.modules.assessments.registry_models import ALL_MODELS  # noqa: E402
# C 培训/测评会向 D 域 notifications 表写入事件；测试库需一并建表
from app.modules.notifications.models import Notification  # noqa: E402

USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,
    display_name VARCHAR(50),
    role VARCHAR(20) NOT NULL DEFAULT 'hr',
    -- person_id BIGINT: synced with A's migration A_0002_users_person (2026-11-07,
    -- edited by A under the user's cooperation rule; nullable, unique in MySQL)
    person_id BIGINT,
    is_active INTEGER NOT NULL DEFAULT 1,
    last_login_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class Harness:
    """独立 SQLite 测试应用；每个测试实例互不影响。"""

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as connection:
            connection.execute(text(USERS_DDL))
        Base.metadata.create_all(
            self.engine, tables=[model.__table__ for model in ALL_MODELS] + [Notification.__table__]
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        app = FastAPI()
        register_exception_handlers(app)
        for router in ALL_ROUTERS:
            app.include_router(router)

        def override_get_db() -> Iterator[Session]:
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_current_user(
            request: Request, db: Session = Depends(get_db)
        ) -> User:
            raw = request.headers.get("X-Test-User")
            if not raw:
                raise BizError(401, "未登录或登录已过期")
            user = db.get(User, int(raw))
            if user is None:
                raise BizError(401, "未登录或登录已过期")
            return user

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_current_user
        self.app = app
        self.client = TestClient(app)

    # ---- 数据准备与断言辅助

    def create_user(self, username: str, role: str = "employee") -> User:
        db = self.SessionLocal()
        try:
            user = User(
                username=username,
                password_hash="x",
                display_name=username,
                role=role,
                is_active=1,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user
        finally:
            db.close()

    @staticmethod
    def headers(user: User) -> dict[str, str]:
        return {"X-Test-User": str(user.id)}

    def session(self) -> Session:
        return self.SessionLocal()

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()
