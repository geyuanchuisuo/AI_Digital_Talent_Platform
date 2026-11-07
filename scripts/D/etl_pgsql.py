#!/usr/bin/env python3
"""MySQL → PostgreSQL 只读分析副本批同步（T-D02）。

口径（docs/modules/D/ETL说明.md / 开发计划-todolist T-D02）：
- 一期定时批同步（全量初始化 + 增量），二期按需 CDC（DEC-20261107-DLINE-CONFIRMED）；
- 表清单按白名单指标目录圈定（A 域 3 卡 + B 域解析卡），**列级 allowlist**
  排除敏感大字段（JD 全文/简历原文/评分理由快照等）；
- 类型从 MySQL information_schema 自动映射，不手抄 DDL；
- 空 ANALYTICS_DB_URL = 副本未启用 → 明确退出（不静默降级，禁止假数据）；
- 增量水位 = 目标表 MAX(incr_col)，无状态（删表重跑即全量）。

用法（backend/.venv 环境）：
    python scripts/D/etl_pgsql.py --full            # 全量初始化（TRUNCATE+重灌）
    python scripts/D/etl_pgsql.py --incremental     # 增量（按 updated_at/created_at 水位）
    python scripts/D/etl_pgsql.py --verify          # 行数/延迟核对
    python scripts/D/etl_pgsql.py --full --tables project_demands,applications
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text  # noqa: E402

# --------------------------------------------------------------------------- #
# ETL 规格：表 → 允许列 + 增量列（V2.6 指标口径最小集；敏感/大字段全部排除）
# --------------------------------------------------------------------------- #
ETL_SPEC: dict[str, dict] = {
    # ---- A 域（卡片 1 岗位需求/人工推进/缺口 + 维度）----
    "project_groups": {
        "columns": ["id", "name", "description", "status", "created_at", "updated_at"],
        "incr": "updated_at",
    },
    "projects": {
        "columns": ["id", "project_group_id", "name", "status", "created_at", "updated_at"],
        "incr": "updated_at",
    },
    "project_demands": {
        "columns": [
            "id", "project_id", "project_group_id", "job_profile_id", "position_name",
            "headcount", "expected_start_date", "priority", "status",
            "created_at", "updated_at",
        ],
        "incr": "updated_at",
    },
    "applications": {
        "columns": [
            "id", "talent_profile_id", "job_profile_id", "resume_file_id",
            "internal_stage", "external_stage", "result_category", "closed_at",
            "created_at", "updated_at",
        ],
        "incr": "updated_at",
        # 排除：stage_reason（敏感原因文本）、*_by（操作人）——指标只需阶段/终态计数
    },
    "job_profiles": {
        "columns": [
            "id", "title", "department", "min_degree", "min_work_years",
            "pass_threshold", "pending_threshold", "version", "is_active",
            "created_at", "updated_at",
        ],
        "incr": "updated_at",
        # 排除：jd_text / requirements_text（JD 全文大字段）、*_json 配置——问数不需要
    },
    "screen_results": {
        "columns": [
            "id", "screen_batch_id", "job_profile_id", "talent_profile_id",
            "hard_fail", "score", "verdict", "manual_verdict", "created_at",
        ],
        "incr": "created_at",
        # 排除：reason_summary / hits_json / misses_json / profile_input_snapshot_json（画像内容快照）
    },
    "match_batches": {
        "columns": [
            "id", "job_profile_id", "job_profile_version", "status", "created_at",
        ],
        "incr": "created_at",
        # 排除：config/scope 快照 json；本表无 updated_at（CreatedAtMixin）
    },
    "match_results": {
        "columns": [
            "id", "match_batch_id", "talent_profile_id",
            "score", "hard_fail", "created_at",
        ],
        "incr": "created_at",
        # job 维度经 match_batches.job_profile_id 关联（本表无该列——规格漂移防护抓出）
        # 排除：reason / dimension_scores_json / hits / misses
    },
    # ---- B 域（卡片 2 解析成功失败）----
    "parse_tasks": {
        "columns": [
            "id", "resume_file_id", "status", "stage", "retries", "truncated",
            "started_at", "profiled_at", "finished_at", "created_at", "updated_at",
        ],
        "incr": "updated_at",
        # 排除：error（运行时错误细节，失败原因分组由 B 契约另定后再评估）
    },
}

# MySQL → PG 类型映射（information_schema.DATA_TYPE 驱动）
_PG_TYPE_MAP = {
    "bigint": "BIGINT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "tinyint": "SMALLINT",
    "smallint": "SMALLINT",
    "varchar": "VARCHAR",       # 长度从 COLUMN_TYPE 解析
    "char": "VARCHAR",
    "text": "TEXT",
    "mediumtext": "TEXT",
    "longtext": "TEXT",
    "datetime": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "date": "DATE",
    "decimal": None,            # 特殊处理：NUMERIC(precision, scale)
    "json": "JSONB",
}

JSON_TYPES = {"json", "jsonb"}


def _die(msg: str) -> "None":
    print(f"[ETL] ✗ {msg}", file=sys.stderr)
    sys.exit(2)


def make_engines():
    from app.config import settings

    if not settings.analytics_db_url:
        _die("ANALYTICS_DB_URL 未配置（副本未启用口径）；请先在 backend/.env 配置并起 PG 容器。"
             "主库直查降级属查询服务（T-D08）职责，不在 ETL 内静默降级。")
    return (
        create_engine(settings.database_url, pool_pre_ping=True),
        create_engine(settings.analytics_db_url, pool_pre_ping=True),
    )


def source_columns(mysql, table: str, allowed: list[str]) -> dict[str, dict]:
    """从 information_schema 取源列定义（仅 allowlist 内存在的列）。"""
    with mysql.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ),
            {"t": table},
        ).mappings()
        rows = [dict(r) for r in rows]
    cols = {}
    for r in rows:
        if r["COLUMN_NAME"] in allowed:
            cols[r["COLUMN_NAME"]] = dict(r)
    missing = set(allowed) - set(cols)
    if missing:
        _die(f"表 {table} 源库缺少规格列：{sorted(missing)}（schema 与规格漂移，先对齐再跑）")
    return cols


def pg_type_of(col: dict) -> str:
    dtype = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()
    if dtype == "decimal":
        return "NUMERIC" + ctype.removeprefix("decimal")  # (m,n) 原样
    base = _PG_TYPE_MAP.get(dtype)
    if base is None:
        _die(f"未映射的源类型 {dtype!r}（{ctype!r}）")
    if base == "VARCHAR":
        import re

        m = re.search(r"\((\d+)\)", ctype)
        return f"VARCHAR({min(int(m.group(1)), 65535)})" if m else "TEXT"
    return base


def ensure_pg_table(pg, table: str, cols: dict[str, dict]) -> None:
    pk = [name for name, c in cols.items() if c["COLUMN_KEY"] == "PRI"] or ["id"]
    defs = ", ".join(f'"{name}" {pg_type_of(c)}' for name, c in cols.items())
    pk_sql = ", ".join(f'"{p}"' for p in pk)
    with pg.begin() as conn:
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{table}" ({defs}, PRIMARY KEY ({pk_sql}))'))


def _jsonify(value, col: dict):
    if value is not None and (col["DATA_TYPE"] or "").lower() == "json":
        return json.loads(value) if isinstance(value, (str, bytes)) else value
    return value


def sync_table(mysql, pg, table: str, spec: dict, *, mode: str) -> dict:
    cols = source_columns(mysql, table, spec["columns"])
    names = list(cols)
    ensure_pg_table(pg, table, cols)
    src_cols = ", ".join(f"`{n}`" for n in names)   # MySQL 源：反引号（双引号会被当字符串字面量）
    pg_cols = ", ".join(f'"{n}"' for n in names)    # PG 目标：双引号
    incr = spec["incr"]

    if mode == "full":
        with pg.begin() as conn:
            conn.execute(text(f'TRUNCATE TABLE "{table}"'))
        watermark = None
    else:
        with pg.connect() as conn:
            watermark = conn.execute(text(f'SELECT MAX("{incr}") FROM "{table}"')).scalar()

    src_stmt = text(f"SELECT {src_cols} FROM `{table}`" + (f" WHERE `{incr}` > :w" if watermark else ""))
    params = {"w": watermark} if watermark else {}
    inserted = 0
    CHUNK = 500
    with mysql.connect() as src:
        result = src.execute(src_stmt, params)
        while True:
            rows = result.fetchmany(CHUNK)
            if not rows:
                break
            data = [{n: _jsonify(getattr(r, n), cols[n]) for n in names} for r in rows]
            insert_sql = text(
                f'INSERT INTO "{table}" ({pg_cols}) VALUES ({", ".join(f":{n}" for n in names)})'
                f' ON CONFLICT ("id") DO NOTHING'
            )
            with pg.begin() as conn:
                conn.execute(insert_sql, data)
            inserted += len(data)
    return {"table": table, "mode": mode, "inserted": inserted}


def verify(mysql, pg, tables: dict[str, dict]) -> list[dict]:
    report = []
    for table, spec in tables.items():
        with mysql.connect() as conn:
            src_n = conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        try:
            with pg.connect() as conn:
                tgt_n = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                lag = conn.execute(text(f'SELECT MAX("{spec["incr"]}") FROM "{table}"')).scalar_one()
        except Exception:  # noqa: BLE001 - 目标表缺失按 0 处理
            tgt_n, lag = 0, None
        lag_seconds = (
            int((datetime.now() - lag).total_seconds()) if isinstance(lag, datetime) else None
        )
        report.append(
            {
                "table": table,
                "source": int(src_n),
                "target": int(tgt_n),
                "match": int(src_n) == int(tgt_n),
                "incr_max": lag.isoformat() if lag else None,
                "lag_seconds": lag_seconds,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="MySQL → PG 分析副本 ETL（T-D02）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--full", action="store_true", help="全量初始化（TRUNCATE + 重灌）")
    group.add_argument("--incremental", action="store_true", help="增量（按水位追平）")
    group.add_argument("--verify", action="store_true", help="行数/延迟核对（不写数据）")
    parser.add_argument("--tables", help="逗号分隔子集（默认规格全表）")
    args = parser.parse_args()

    mysql, pg = make_engines()
    spec = ETL_SPEC
    if args.tables:
        wanted = [t.strip() for t in args.tables.split(",") if t.strip()]
        unknown = set(wanted) - set(ETL_SPEC)
        if unknown:
            _die(f"规格外表：{sorted(unknown)}（目录外不进副本）")
        spec = {t: ETL_SPEC[t] for t in wanted}

    if args.verify:
        report = verify(mysql, pg, spec)
        print(f"{'table':<20}{'source':>8}{'target':>8}{'match':>7}{'lag_s':>8}  incr_max")
        all_ok = True
        for r in report:
            all_ok &= r["match"]
            print(
                f"{r['table']:<20}{r['source']:>8}{r['target']:>8}"
                f"{'OK' if r['match'] else 'DRIFT':>7}"
                f"{str(r['lag_seconds'] or '-'):>8}  {r['incr_max'] or '-'}"
            )
        print("[ETL] verify " + ("PASS" if all_ok else "DRIFT（先 --incremental/--full 追平）"))
        return 0 if all_ok else 1

    mode = "full" if args.full else "incremental"
    total = 0
    for table, s in spec.items():
        stat = sync_table(mysql, pg, table, s, mode=mode)
        total += stat["inserted"]
        print(f"[ETL] {mode:<11} {table:<20} +{stat['inserted']}")
    print(f"[ETL] 完成：{len(spec)} 表，共 {total} 行（mode={mode}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
