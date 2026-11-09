"""tests/D 共享脚手架（T-D00f）。

sys.path 前置 backend/，使 `import app.*` 在仓库根目录的 pytest 运行中可用；
D 域 harness 的所有 fixture 都从这里出，用法见 tests/D/README.md。

SQLite 编译规则（逻辑测试免 MySQL 即可建表）：
- MySQL 方言类型 TINYINT/MEDIUMTEXT 映射等价通用类型；
- BIGINT（含 PK_TYPE 的 mysql 变体）映射 INTEGER，使 INTEGER PRIMARY KEY
  成为 rowid 别名、自增主键可自动赋值；
- 公共 UpdatedAtMixin 的 ON UPDATE CURRENT_TIMESTAMP 从 DDL 中剥离。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import BigInteger, create_engine, select  # noqa: E402,F401 (select 供测试导入)
from sqlalchemy.dialects import mysql  # noqa: E402
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.sql.ddl import CreateTable  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    """SQLite 只对 INTEGER PRIMARY KEY 做 rowid 自增；BIGINT 一律映射 INTEGER。"""
    return "INTEGER"


@compiles(mysql.BIGINT, "sqlite")
def _mysql_bigint_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


@compiles(TINYINT, "sqlite")
def _tinyint_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "SMALLINT"


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "TEXT"


@compiles(CreateTable, "sqlite")
def _create_table_strip_mysql_onupdate(element, compiler, **kw):  # noqa: ANN001, ANN202
    """剥离 MySQL 专有的 ON UPDATE CURRENT_TIMESTAMP（公共 UpdatedAtMixin）。"""
    return compiler.visit_create_table(element).replace(" ON UPDATE CURRENT_TIMESTAMP", "")
