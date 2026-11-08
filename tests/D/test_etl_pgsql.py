"""ETL 集成用例（T-D02 验收）：真实 MySQL(3307) → 真实 PG(5433)。

前置：`docker compose -f deploy/postgres/docker-compose.yml up -d`；
MySQL 副本源为真实业务库（backend/.env 的 MYSQL_*）。任一依赖不可达自动 skip。
运行：pytest tests/D/test_etl_pgsql.py -v
"""

from __future__ import annotations

import importlib.util
import socket
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

# scripts/ 非 Python 包，按文件路径加载 ETL 模块
_ETL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "D" / "etl_pgsql.py"
_spec = importlib.util.spec_from_file_location("etl_pgsql", _ETL_PATH)
etl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(etl)

PG_HOST, PG_PORT = "localhost", 5433
PG_URL = f"postgresql+psycopg://postgres:aitalent_2026@{PG_HOST}:{PG_PORT}/ai_talent_analytics"


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _reachable("localhost", 3307), reason="业务 MySQL(3307) 未运行"
    ),
    pytest.mark.skipif(not _reachable(PG_HOST, PG_PORT), reason="分析 PG(5433) 未运行（docker compose up）"),
]


@pytest.fixture(scope="module")
def engines():
    mysql = create_engine(settings.database_url, pool_pre_ping=True)
    pg = create_engine(PG_URL, pool_pre_ping=True)
    yield mysql, pg
    mysql.dispose()
    pg.dispose()


def test_spec_excludes_sensitive_large_fields():
    """敏感排除断言：JD 全文/简历原文/画像快照/理由文本不进副本。"""
    assert "jd_text" not in etl.ETL_SPEC["job_profiles"]["columns"]
    assert "requirements_text" not in etl.ETL_SPEC["job_profiles"]["columns"]
    assert "error" not in etl.ETL_SPEC["parse_tasks"]["columns"]
    assert "reason" not in etl.ETL_SPEC["match_results"]["columns"]
    assert "reason_summary" not in etl.ETL_SPEC["screen_results"]["columns"]
    assert "stage_reason" not in etl.ETL_SPEC["applications"]["columns"]
    # 简历内容表整体不进副本
    for banned in ("resume_files", "parsed_resumes", "talent_profiles"):
        assert banned not in etl.ETL_SPEC


def test_full_sync_and_verify_match(engines):
    mysql, pg = engines
    for table, spec in etl.ETL_SPEC.items():
        stat = etl.sync_table(mysql, pg, table, spec, mode="full")
        assert stat["inserted"] >= 0
    report = etl.verify(mysql, pg, etl.ETL_SPEC)
    assert all(r["match"] for r in report), report
    # 类型映射：副本里不该有 MySQL 方言类型残留
    with pg.connect() as conn:
        bad = conn.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema='public' AND udt_name IN ('tinyint','mediumtext')"
            )
        ).fetchall()
    assert not bad


def test_incremental_picks_up_new_row(engines):
    mysql, pg = engines
    # 建一行新需求（带唯一标记），增量后必须出现在副本
    marker = f"T-D02增量-{datetime.now().strftime('%H%M%S%f')}"
    with mysql.begin() as db:
        r = db.execute(
            text(
                "INSERT INTO project_demands (position_name, headcount, status, created_by) "
                "VALUES (:n, 3, 'open', NULL)"
            ),
            {"n": marker},
        )
        demand_id = r.lastrowid
    try:
        stat = etl.sync_table(mysql, pg, "project_demands", etl.ETL_SPEC["project_demands"], mode="incremental")
        assert stat["inserted"] >= 1
        with pg.connect() as conn:
            row = conn.execute(
                text('SELECT position_name, headcount FROM "project_demands" WHERE id = :i'),
                {"i": demand_id},
            ).first()
        assert row is not None and row.position_name == marker and row.headcount == 3

        # 幂等：重复增量不产生重复行（水位已追平）
        again = etl.sync_table(mysql, pg, "project_demands", etl.ETL_SPEC["project_demands"], mode="incremental")
        with pg.connect() as conn:
            count = conn.execute(
                text('SELECT COUNT(*) FROM "project_demands" WHERE id = :i'), {"i": demand_id}
            ).scalar_one()
        assert count == 1
        assert again["inserted"] == 0
    finally:
        with mysql.begin() as db:
            db.execute(text("DELETE FROM project_demands WHERE id = :i"), {"i": demand_id})
        with pg.begin() as db:
            db.execute(text('DELETE FROM "project_demands" WHERE id = :i'), {"i": demand_id})


def test_watermark_aligned_after_incremental(engines):
    """验收②：同步延迟——副本水位必须追平源库（无积压即延迟为 0；≤15 分钟目标）。"""
    mysql, pg = engines
    spec = etl.ETL_SPEC["projects"]
    etl.sync_table(mysql, pg, "projects", spec, mode="incremental")
    with mysql.connect() as conn:
        src_max = conn.execute(text("SELECT MAX(`updated_at`) FROM `projects`")).scalar()
    with pg.connect() as conn:
        tgt_max = conn.execute(text('SELECT MAX("updated_at") FROM "projects"')).scalar()
    assert src_max is not None and tgt_max == src_max  # 水位一致 = 零积压
    # 排查接口：verify 的 lag_seconds 反映数据新鲜度（源无写入时偏旧属正常）
