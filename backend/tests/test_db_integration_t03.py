"""L2 集成测试 · T-03 真实 MySQL 验收。

对应 todolist T-03 三个验收点：
1. 空库执行一次成功、重复执行幂等（含改过并发值后再跑 init_db 不被重置）
2. 表结构逐字段对照数据库设计 v1.2 一致
3. 备份脚本产出可恢复的 SQL

真实连本机 MySQL（不外呼 LLM / Milvus）；临时恢复库在 finally 中删除。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import bcrypt
import pymysql
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backup_db  # noqa: E402
import init_db  # noqa: E402

from app.config import Settings, settings  # noqa: E402
from app.dao.sys_settings import get_value  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    JobProfile,
    SysSetting,
    TalentProfile,
    User,
)
from app.models.enums import SETTING_TASK_CONCURRENCY  # noqa: E402
from tests.test_models_structure import EXPECTED_COLUMNS  # noqa: E402

pytestmark = pytest.mark.integration

RESTORE_DB = "ai_talent_restore_test"
PROBE_NAME = "_t03_mutable_probe"

# T-D04 引擎检查点表：Core 层管理（saver.setup() 幂等建表），契约明确不入 ORM
# 基线（工作流引擎契约 §1），与 alembic_version 同为非业务表，从逐表对照中排除。
CORE_ENGINE_TABLES = {"workflow_checkpoints", "workflow_checkpoint_writes"}


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    """测试自包含：库不存在也能重建；不删已有数据。"""
    init_db.create_database()
    init_db.create_tables()
    with SessionLocal() as session:
        init_db.seed(session)
    yield


@pytest.fixture
def backup_dir():
    """Windows 上系统 Temp 的 pytest-of-* 目录可能拒绝访问，改用项目内临时目录。"""
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        yield Path(directory)


def _drop_restore_db():
    conn = backup_db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{RESTORE_DB}`")
    finally:
        conn.close()


def test_real_schema_matches_expected_columns():
    """真实库 information_schema 逐表对照，不只看 ORM metadata。

    `alembic_version` 是迁移状态表（非业务表），从对照中排除；
    workflow_checkpoints / workflow_checkpoint_writes 是引擎检查点表（Core 层
    管理，不入 ORM 基线），一并排除；业务表清单仍必须与 EXPECTED_COLUMNS 完全一致。
    """
    conn = backup_db.connect(settings.mysql_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = {row[0] for row in cur.fetchall()} - {"alembic_version"} - CORE_ENGINE_TABLES
            assert tables == set(EXPECTED_COLUMNS)

            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s",
                (settings.mysql_db,),
            )
            actual: dict[str, set[str]] = {}
            for table, column in cur.fetchall():
                actual.setdefault(table, set()).add(column)
    finally:
        conn.close()
    for table, expected in EXPECTED_COLUMNS.items():
        assert actual[table] == expected, (
            f"{table}: 多={actual[table]-expected}, 少={expected-actual[table]}"
        )


def test_seed_data_is_usable():
    with SessionLocal() as db:
        user = db.query(User).filter_by(username="hr").one_or_none()
        assert user is not None
        assert bcrypt.checkpw(b"hr123456", user.password_hash.encode("ascii"))
        assert user.is_active == 1 and user.role == "hr"

        job = db.query(JobProfile).filter_by(title="高级后端工程师", version=1).one_or_none()
        assert job is not None
        assert job.min_degree == "本科"
        assert job.must_skills_json == ["python", "fastapi", "mysql"]
        assert sum(job.weights_json.values()) == 100

        setting = db.query(SysSetting).filter_by(setting_key=SETTING_TASK_CONCURRENCY).one()
        assert setting.value_type == "int"
        assert 1 <= int(setting.setting_value) <= 5


def test_init_db_is_idempotent_and_preserves_changed_concurrency():
    """改过并发值后重跑 init_db，现值必须保留（§9 第 4 条）。"""
    with SessionLocal() as db:
        setting = db.query(SysSetting).filter_by(setting_key=SETTING_TASK_CONCURRENCY).one()
        original = setting.setting_value
        user_count = db.query(User).count()
        job_count = db.query(JobProfile).count()

    try:
        with SessionLocal() as db:
            db.query(SysSetting).filter_by(setting_key=SETTING_TASK_CONCURRENCY).one().setting_value = "4"
            db.commit()

        exit_code = init_db.main([])  # 完整重跑：建库 + 建表 + 种子
        assert exit_code == 0

        with SessionLocal() as db:
            setting = db.query(SysSetting).filter_by(setting_key=SETTING_TASK_CONCURRENCY).one()
            assert setting.setting_value == "4", "init_db 不得用 .env 覆盖用户改过的并发值"
            assert db.query(User).count() == user_count, "重复执行不得新增用户"
            assert db.query(JobProfile).count() == job_count, "重复执行不得新增岗位"

        # config.load_runtime_overrides 现在应真正从表读到 4（用独立实例，不污染全局 settings）
        assert get_value(SETTING_TASK_CONCURRENCY) == "4"
        cfg = Settings()
        cfg.load_runtime_overrides()
        assert cfg.task_concurrency == 4
    finally:
        with SessionLocal() as db:
            db.query(SysSetting).filter_by(
                setting_key=SETTING_TASK_CONCURRENCY
            ).one().setting_value = original
            db.commit()


def test_get_value_missing_key_returns_default():
    assert get_value("__not_exists__") is None
    assert get_value("__not_exists__", "9") == "9"


def test_json_mutable_change_is_persisted():
    """§1 隐蔽 bug：JSON 原地修改必须落库，不能静默丢失。"""
    with SessionLocal() as db:
        db.query(TalentProfile).filter_by(name=PROBE_NAME).delete()
        db.commit()
        db.add(TalentProfile(name=PROBE_NAME, skills_json=["python"], tags_json=["t1"]))
        db.commit()

    try:
        with SessionLocal() as db:
            profile = db.query(TalentProfile).filter_by(name=PROBE_NAME).one()
            profile.skills_json.append("fastapi")  # 原地修改，不整体重新赋值
            profile.tags_json.append("t2")
            db.commit()

        with SessionLocal() as db:
            profile = db.query(TalentProfile).filter_by(name=PROBE_NAME).one()
            assert profile.skills_json == ["python", "fastapi"]
            assert profile.tags_json == ["t1", "t2"]
            assert profile.vector_status == "pending"  # 默认值由 DDL 生效
    finally:
        with SessionLocal() as db:
            db.query(TalentProfile).filter_by(name=PROBE_NAME).delete()
            db.commit()


def test_backup_sql_is_restorable(backup_dir):
    sql_path, counts = backup_db.dump_sql(backup_dir / "backup.sql")
    assert sql_path.exists() and sql_path.stat().st_size > 0
    # 备份覆盖全部库表（含引擎检查点表）；业务表与 EXPECTED_COLUMNS 一致
    assert set(counts) - CORE_ENGINE_TABLES == set(EXPECTED_COLUMNS)
    assert set(counts) <= set(EXPECTED_COLUMNS) | CORE_ENGINE_TABLES
    sql = sql_path.read_text(encoding="utf-8")
    assert "CREATE TABLE" in sql and "INSERT INTO `users`" in sql
    statements = list(backup_db.iter_statements(sql))
    assert len(statements) >= 13

    try:
        restored = backup_db.restore(sql_path, RESTORE_DB)
        assert restored == counts, "恢复后每张表行数必须与备份时一致"

        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=RESTORE_DB,
            charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT username, password_hash FROM users WHERE username='hr'")
                row = cur.fetchone()
                assert row is not None
                assert bcrypt.checkpw(b"hr123456", row[1].encode("ascii"))
                cur.execute("SELECT setting_value FROM sys_settings WHERE setting_key=%s",
                            (SETTING_TASK_CONCURRENCY,))
                assert cur.fetchone() is not None
        finally:
            conn.close()
    finally:
        _drop_restore_db()


def test_restore_refuses_production_db(backup_dir):
    sql_path, _ = backup_db.dump_sql(backup_dir / "backup2.sql")
    with pytest.raises(ValueError, match="拒绝恢复到生产库"):
        backup_db.restore(sql_path, settings.mysql_db)
    _drop_restore_db()


def test_engine_still_connects():
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1
