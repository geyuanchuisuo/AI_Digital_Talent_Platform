"""`sys_settings` 读写（T-03 读取 / T-14 写入）。

调用方：
- `app.config.Settings.load_runtime_overrides()` —— 启动与环境刷新时把
  `task_concurrency` 从本表读到运行时；本表有值时以本表为准，重启后仍保持
  （`docs/运行配置.md` v1.3「读取优先级」、数据库设计 v1.2 §3.2）。
- `app.service.queue_service.update_concurrency()` —— T-14 `PATCH /resumes/queue/settings`
  写入本表，TaskRunner 下一扫描周期读到新值（接口 §4 / 决策 D-C）。

设计取舍：
- `get_value()` 用独立 session（配置刷新可能在请求之外）；新增的 `get_int()` /
  `set_value()` 使用调用方传入的 session，属于请求事务的一部分。
- 表不存在 / 连接失败时 `get_value()` 一律返回 default，不抛异常：T-03 之前 dao 未实现时
  config 靠 ImportError 兜底；dao 存在但库尚未初始化时也必须保持应用可启动（不刷 warning）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SysSetting


def get_value(key: str, default: str | None = None) -> str | None:
    """返回 setting_value 原始字符串；缺行/表不存在/连接失败返回 default。"""
    try:
        with SessionLocal() as db:
            value = db.execute(
                select(SysSetting.setting_value).where(SysSetting.setting_key == key)
            ).scalar_one_or_none()
        return default if value is None else value
    except SQLAlchemyError:
        return default


def get_int(db: Session, key: str, default: int) -> int:
    """在调用方 session 里读整数配置；缺行或脏值回退 default（不抛异常）。

    队列接口的 `concurrency` 用它：脏值不应让 `GET /resumes/queue` 500，
    TaskRunner 侧的合法值由写入接口的 1–5 校验保证。
    """
    value = db.execute(
        select(SysSetting.setting_value).where(SysSetting.setting_key == key)
    ).scalar_one_or_none()
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def set_value(
    db: Session,
    key: str,
    value: str,
    *,
    value_type: str = "int",
    description: str | None = None,
    updated_by: int | None = None,
) -> SysSetting:
    """按 `setting_key` upsert（`uk_setting_key` 唯一，最多一行）；不 commit。

    只覆盖 `setting_value` / `updated_by`（`description` 仅在传入时更新），
    避免把 `init_db.py` 写入的说明文字冲掉。
    """
    row = db.execute(
        select(SysSetting).where(SysSetting.setting_key == key)
    ).scalar_one_or_none()
    if row is None:
        row = SysSetting(
            setting_key=key,
            setting_value=value,
            value_type=value_type,
            description=description,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.setting_value = value
        row.updated_by = updated_by
        if description:
            row.description = description
    db.flush()
    return row
