"""C 模块测试公共夹具（stdlib unittest / pytest 均可运行）。

环境说明：
- 不依赖 MySQL/Milvus/真实模型：用 SQLite 持久化 + 测试内注入评分/草稿替身。
- jose 优先导入真实库；仅在未安装时注入最小替身让 `app.api.deps` 可导入，
  测试本身不使用真实 token。替身不覆盖已安装的真实库（防跨套件进程级污染）。
- 只创建 C 的 17 张表；`users` 表因公共模型使用 MySQL 专用类型，按同结构手写 SQLite DDL。
- 全局 `app.database.SessionLocal` 在 Harness 生命周期内替换为内存 SQLite，
  `close()` 恢复原值——从仓库根混跑 pytest 时不污染其他套件。
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

try:
    import jose  # noqa: F401  真实库优先，避免替身遮蔽已安装的 python-jose
except ImportError:
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
from sqlalchemy import BigInteger, create_engine, select, text  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# 公共 PKMixin 使用 BIGINT；SQLite 只有 INTEGER PRIMARY KEY 才能自增
@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(type_, compiler, **kwargs):  # noqa: ANN001
    return "INTEGER"

from app.api.deps import get_current_user  # noqa: E402
from app.core.auth.scope import ROLE_CODES  # noqa: E402
from app.core.errors import BizError, register_exception_handlers  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models import SysRole, SysUserRole, User  # noqa: E402
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
    data_scope_mode VARCHAR(20) NOT NULL DEFAULT 'legacy',
    -- person_id BIGINT: synced with A's migration A_0002_users_person (2026-11-07,
    -- edited by A under the user's cooperation rule; nullable, unique in MySQL)
    person_id BIGINT,
    -- department_id INTEGER: synced with A's migration A_0009_users_department_id
    -- (A06-2 dept row-level scope; nullable)
    department_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    last_login_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# A06 角色门禁以 sys_user_roles 聚合为准；system 域模型的 UpdatedAtMixin 仅适用
# MySQL（server_default 含 ON UPDATE），SQLite 下须按同结构手写 DDL。
ROLES_DDL = """
CREATE TABLE sys_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    description VARCHAR(500),
    data_scope VARCHAR(20) NOT NULL DEFAULT 'self',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

USER_ROLES_DDL = """
CREATE TABLE sys_user_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    UNIQUE (user_id, role_id)
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
            connection.execute(text(ROLES_DDL))
            connection.execute(text(USER_ROLES_DDL))
        Base.metadata.create_all(
            self.engine, tables=[model.__table__ for model in ALL_MODELS] + [Notification.__table__]
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        # A06 角色门禁的服务层入口（require_management_guard）读取全局
        # app.database.SessionLocal；测试须替换为内存 SQLite，否则会尝试连接
        # 真实 MySQL 并因查不到角色表而把所有账号判为 personal。
        import app.database as _app_database

        self._app_database = _app_database
        self._prev_session_local = _app_database.SessionLocal
        _app_database.SessionLocal = self.SessionLocal

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
            db.flush()
            # A06 门禁以 sys_user_roles 聚合为准（deny-by-default）：
            # "admin" 简值映射为 system_admin，其余六类角色代码原样绑定，
            # 未知值仅保留 users.role 简值（回退路径按 personal 处理）。
            bind_code = {"admin": "system_admin"}.get(role, role if role in ROLE_CODES else None)
            if bind_code is not None:
                role_row = db.execute(
                    select(SysRole).where(SysRole.code == bind_code)
                ).scalar_one_or_none()
                if role_row is None:
                    role_row = SysRole(
                        code=bind_code,
                        name=bind_code,
                        data_scope="all" if bind_code in ("system_admin", "hr") else "dept",
                        status="active",
                    )
                    db.add(role_row)
                    db.flush()
                db.add(SysUserRole(user_id=user.id, role_id=role_row.id))
            db.commit()
            db.refresh(user)
            return user
        finally:
            db.close()

    @staticmethod
    def headers(user: User) -> dict[str, str]:
        return {"X-Test-User": str(user.id)}

    def set_department(self, user: User, department_id: int | None) -> User:
        """设置账号所属部门（A06 部门行级范围测试用）；同步刷新传入对象。"""
        db = self.SessionLocal()
        try:
            row = db.get(User, user.id)
            row.department_id = department_id
            db.commit()
        finally:
            db.close()
        user.department_id = department_id
        return user

    def session(self) -> Session:
        return self.SessionLocal()

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()
        # 恢复全局 SessionLocal，避免混跑时其他套件拿到已 dispose 的 SQLite 工厂
        self._app_database.SessionLocal = self._prev_session_local
