"""ORM 公共类型与 Mixin（T-03）。

实现口径：`docs/数据库设计.md` v1.2 §1 总体约定。
- 主键统一 `BIGINT UNSIGNED AUTO_INCREMENT`
- `created_at DATETIME DEFAULT CURRENT_TIMESTAMP`；需要更新的表加 `ON UPDATE CURRENT_TIMESTAMP`
- 布尔 `TINYINT(1)`；分数 `DECIMAL(5,2)` / 年限 `DECIMAL(4,1)`
- JSON 一律用 `MutableDict` / `MutableList` 包裹，保证原地修改能落库
  （文档 §1 明确点名的隐蔽 bug；也允许整体重新赋值）

类型用 `with_variant` 绑定 mysql，便于 L1 用 MySQL dialect 编译 DDL 做结构对照。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, JSON, text
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

# BIGINT UNSIGNED：MySQL 下渲染 BIGINT UNSIGNED，其他 dialect 退回 BigInteger
PK_TYPE = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
UINT_TYPE = mysql.INTEGER(unsigned=True)
TINY_TYPE = mysql.TINYINT()                       # parse_tasks.retries：TINYINT
BOOL_TYPE = mysql.TINYINT(display_width=1)       # TINYINT(1)
MEDIUMTEXT_TYPE = mysql.MEDIUMTEXT()

# JSON 可变类型：dict 与 list 分开，避免串型数据被错误 coerce
JSONDict = MutableDict.as_mutable(JSON())
JSONList = MutableList.as_mutable(JSON())

CREATED_DEFAULT = text("CURRENT_TIMESTAMP")
UPDATED_DEFAULT = text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")

# 文档 §1：库/表统一 InnoDB + utf8mb4 / utf8mb4_unicode_ci
MYSQL_TABLE_OPTIONS: dict[str, str] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


class PKMixin:
    """所有表统一主键。"""

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CREATED_DEFAULT
    )


class UpdatedAtMixin(CreatedAtMixin):
    """created_at + updated_at（ON UPDATE 由 MySQL 维护，不依赖客户端 onupdate）。"""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=UPDATED_DEFAULT
    )


def str_default(value: str) -> Any:
    """字符串列的 server_default（DDL 侧），配合 Python 侧 default 使用。"""
    return text("'" + value.replace("'", "''") + "'")


def num_default(value: int | float | str) -> Any:
    """数值列的 server_default：必须写成 SQL 表达式，否则会渲染成字符串字面量 '1'。"""
    return text(str(value))
