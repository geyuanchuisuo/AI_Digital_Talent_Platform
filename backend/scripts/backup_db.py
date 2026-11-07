"""MySQL 逻辑备份 / 恢复（T-03）。

输入文档：`docs/数据库设计.md` v1.2 §10 —— `backups/ai_talent_YYYYMMDD.sql`。
Windows 可用：不依赖 `mysqldump` 是否在 PATH，直接用 pymysql 读
`SHOW CREATE TABLE` + `SELECT *` 生成「可重新执行的 SQL」（DDL + INSERT）。

用法（`main/backend` 目录，一律用 venv 解释器）：
    ./.venv/Scripts/python.exe scripts/backup_db.py
    ./.venv/Scripts/python.exe scripts/backup_db.py --output D:/tmp/bak.sql
    ./.venv/Scripts/python.exe scripts/backup_db.py --restore D:/tmp/bak.sql --database ai_talent_restore_test

⚠️ 恢复会先 DROP 目标库再重建；为防误删，目标库名**不允许等于**当前生产库 `MYSQL_DB`。
向量属派生数据不入备份，恢复后按 `docs/数据库设计.md` §10 用 rebuild_vectors.py 重建。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pymysql  # noqa: E402

from app.config import settings  # noqa: E402

BACKUPS_DIR = BACKEND_DIR.parent / "backups"  # main/backups/


def connect(database: str | None = None):
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=database,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=60,
    )


def sql_literal(value: object) -> str:
    """把 pymysql 返回值转成能直接写进 SQL 的 MySQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "X'" + bytes(value).hex() + "'"
    if isinstance(value, (datetime, date, time, timedelta)):
        text = value.isoformat(sep=" ") if isinstance(value, datetime) else str(value)
        return "'" + text.replace("'", "''") + "'"
    text = str(value)
    escaped = (
        text.replace("\\", "\\\\")
        .replace("'", "''")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x00", "\\0")
        .replace("\x1a", "\\Z")
    )
    return "'" + escaped + "'"


def iter_statements(sql: str):
    """按分号切分 SQL，跳过单引号/反引号内部与注释行（供 --restore 与测试复用）。

    字符串内的分号、换行不会误切；反斜杠转义按 MySQL 规则跳过下一字符。
    """
    buf: list[str] = []
    in_single = False
    in_backtick = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_single:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(sql):
                buf.append(sql[i + 1])
                i += 2
                continue
            if ch == "'":
                in_single = False
        elif in_backtick:
            buf.append(ch)
            if ch == "`":
                in_backtick = False
        elif ch == "'":
            in_single = True
            buf.append(ch)
        elif ch == "`":
            in_backtick = True
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            cleaned = _strip_comment_lines(stmt)
            if cleaned:
                yield cleaned
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = _strip_comment_lines("".join(buf).strip())
    if tail:
        yield tail


def _strip_comment_lines(stmt: str) -> str:
    lines = [
        line for line in stmt.splitlines()
        if not line.lstrip().startswith(("--", "#", "/*"))
    ]
    return "\n".join(lines).strip()


def build_dump(conn) -> tuple[str, dict[str, int]]:
    """生成完整 SQL 文本，并返回每张表的行数。"""
    parts: list[str] = [
        f"-- AI 数字化人才平台 逻辑备份  database={settings.mysql_db}  date={date.today()}",
        "-- 由 scripts/backup_db.py 生成；恢复：backup_db.py --restore FILE --database NAME",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS=0;",
        "SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';",
    ]
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
        # alembic_version 是迁移状态表，不属于业务数据：不入备份，
        # 恢复后由 init_db stamp / alembic 重新对齐（见 docs/modules/A/迁移链说明.md）
        tables = [t for t in tables if t != "alembic_version"]
        for table in sorted(tables):
            cur.execute(f"SHOW CREATE TABLE `{table}`")
            ddl = cur.fetchone()[1]
            parts.append(f"DROP TABLE IF EXISTS `{table}`;")
            parts.append(ddl + ";")
            cur.execute(f"SELECT * FROM `{table}`")
            columns = [desc[0] for desc in cur.description]
            col_sql = ", ".join(f"`{c}`" for c in columns)
            rows = cur.fetchall()
            for row in rows:
                values = ", ".join(sql_literal(v) for v in row)
                parts.append(f"INSERT INTO `{table}` ({col_sql}) VALUES ({values});")
            counts[table] = len(rows)
    parts.append("SET FOREIGN_KEY_CHECKS=1;")
    return "\n".join(parts) + "\n", counts


def dump_sql(output: Path | None = None) -> tuple[Path, dict[str, int]]:
    path = Path(output) if output else BACKUPS_DIR / f"{settings.mysql_db}_{date.today():%Y%m%d}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.mysql_db)
    try:
        sql, counts = build_dump(conn)
    finally:
        conn.close()
    path.write_text(sql, encoding="utf-8")
    return path, counts


def restore(sql_path: Path, database: str) -> dict[str, int]:
    """把备份 SQL 恢复到指定库（先 DROP 再重建）；返回恢复后的表行数。"""
    if database == settings.mysql_db:
        raise ValueError(f"拒绝恢复到生产库 {database}；请换一个库名（如 {database}_restore_test）")
    if not database.replace("_", "").isalnum():
        raise ValueError(f"非法库名：{database}")

    sql = Path(sql_path).read_text(encoding="utf-8")
    statements = list(iter_statements(sql))
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cur.execute(
                f"CREATE DATABASE `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute(f"USE `{database}`")
            for stmt in statements:
                cur.execute(stmt)
            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]
            # 与 dump 侧一致：alembic_version 不计入业务表统计
            tables = [t for t in tables if t != "alembic_version"]
            counts = {}
            for table in tables:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MySQL 逻辑备份 / 恢复")
    parser.add_argument("--output", type=Path, help="备份文件路径（默认 main/backups/ai_talent_YYYYMMDD.sql）")
    parser.add_argument("--restore", type=Path, help="要恢复的 SQL 文件")
    parser.add_argument("--database", help="恢复到哪个库（不可等于 MYSQL_DB）")
    args = parser.parse_args(argv)

    if args.restore:
        if not args.database:
            parser.error("--restore 必须同时给 --database")
        counts = restore(args.restore, args.database)
        print(f"已恢复到 {args.database}：{len(counts)} 张表，{sum(counts.values())} 行")
        return 0

    path, counts = dump_sql(args.output)
    print(f"备份完成：{path}")
    print(f"共 {len(counts)} 张表 / {sum(counts.values())} 行：{counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
