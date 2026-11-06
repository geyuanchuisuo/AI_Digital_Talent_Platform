"""`users` 读取与登录时间回写（T-07）。

设计口径：
- DAO 只做数据访问，**不提交事务**：session 由调用方（API 依赖 `get_db`）持有，
  service 决定什么时候 commit，避免"两次提交半写"。
- 登录失败不抛异常，只返回 None：401/403 的判定属 service 职责（业务语义）。
- 用户名查询走 `uk_username` 唯一索引，不接受模糊匹配。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_by_username(db: Session, username: str) -> User | None:
    """按登录名精确查询；不存在返回 None。"""
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def get_by_id(db: Session, user_id: int) -> User | None:
    """按主键查询；`get_current_user` 用它把 JWT 的 sub 还原成用户行。"""
    return db.get(User, user_id)


def touch_last_login(db: Session, user: User, when: datetime) -> None:
    """登录成功后更新 `last_login_at`（不 commit，由 service 统一提交）。

    `user` 由本 session 查出的持久态对象，属性赋值即进入脏检查，无需 `db.add()`。
    """
    user.last_login_at = when
