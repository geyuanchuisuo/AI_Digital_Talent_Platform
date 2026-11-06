"""SQLAlchemy 同步 engine / session（T-01 骨架）。

架构 §4：`backend/app/database.py  ← 同步 engine/session`。
T-03 会在 app/models/ 定义 13 张表并继承此处的 Base；本文件不含任何表定义，不抢 T-03。
T-01 阶段 `ai_talent` 库尚未创建，因此 import 本模块不会建立连接（engine 是懒连接）。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.mysql_pool_size,
    max_overflow=settings.mysql_max_overflow,
    pool_recycle=3600,
    echo=settings.mysql_echo,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """所有 ORM 实体的公共基类（T-03 使用）。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个 session，结束即关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
