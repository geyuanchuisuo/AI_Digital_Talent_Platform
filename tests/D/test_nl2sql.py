"""T-D09 自由问数用例：负例矩阵 + 超时/上限实测 + 拒答不猜测 + 审计留痕。

分层：校验器纯单测（sqlparse 负例矩阵）→ 编排单测（Fake 生成器+SQLite 审计）→
真实 PG 副本集成（只读事务/超时/行上限/禁写兜底，aitalent-app-pg-analytics@5433）。
运行：pytest tests/D/test_nl2sql.py -v
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.database import Base
from app.models import Nl2SqlAudit
from app.modules.analytics.nl2sql import service as nl2sql_service
from app.modules.analytics.nl2sql.executor import QueryTimeoutError, execute_readonly
from app.modules.analytics.nl2sql.generator import GeneratorUnavailable, OpenAICompatGenerator
from app.modules.analytics.nl2sql.validator import validate_sql
from app.modules.analytics.nl2sql.whitelist import reset_whitelist_cache
from app.modules.analytics.router import router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)


def _pg_up() -> bool:
    try:
        socket.create_connection(("localhost", 5433), timeout=0.5)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 校验器：负例矩阵（验收①④的快速失败层）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sql", [
    "INSERT INTO job_profiles (title) VALUES ('x')",
    "UPDATE job_profiles SET title='x'",
    "DELETE FROM job_profiles",
    "DROP TABLE job_profiles",
    "TRUNCATE job_profiles",
    "ALTER TABLE job_profiles ADD c int",
    "CREATE TABLE hack (id int)",
    "GRANT ALL ON job_profiles TO x",
    "SELECT 1; DROP TABLE job_profiles",
    "WITH t AS (DELETE FROM job_profiles RETURNING *) SELECT * FROM t",
])
def test_validator_write_statements_rejected(sql):
    vr = validate_sql(sql, {"job_profiles": frozenset({"id", "title"})}, max_rows=100)
    assert not vr.ok and vr.reason is not None


def test_validator_select_ok_and_limit_forced():
    vr = validate_sql("SELECT id, title FROM job_profiles",
                      {"job_profiles": frozenset({"id", "title"})}, max_rows=100)
    assert vr.ok
    assert vr.sql.upper().endswith("LIMIT 100")
    assert vr.tables == {"job_profiles"}


def test_validator_existing_limit_not_wrapped():
    vr = validate_sql("SELECT id FROM job_profiles LIMIT 5",
                      {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert vr.ok and "AS _q" not in vr.sql and vr.sql.upper().endswith("LIMIT 5")


def test_validator_unknown_table_rejected():
    vr = validate_sql("SELECT * FROM pg_catalog.pg_authid",
                      {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert not vr.ok and "白名单" in vr.reason


def test_validator_for_update_rejected():
    vr = validate_sql("SELECT id FROM job_profiles FOR UPDATE",
                      {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert not vr.ok  # UPDATE 关键词或加锁子句，任一拦截即通过


def test_validator_subquery_tables_checked():
    vr = validate_sql("SELECT * FROM (SELECT secret FROM hidden_table) t",
                      {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert not vr.ok and "白名单" in vr.reason


def test_validator_keyword_inside_string_not_false_positive():
    vr = validate_sql("SELECT id FROM job_profiles WHERE title = '请 DROP TABLE 一下'",
                      {"job_profiles": frozenset({"id", "title"})}, max_rows=100)
    assert vr.ok  # 引号内关键词不误伤


def test_validator_with_cte_select_ok():
    vr = validate_sql(
        "WITH c AS (SELECT id, is_active FROM job_profiles) "
        "SELECT id FROM c WHERE is_active = 1",
        {"job_profiles": frozenset({"id", "is_active"})}, max_rows=100)
    assert vr.ok and vr.tables == {"job_profiles", "c"}


def test_validator_empty_rejected():
    assert not validate_sql("", {"job_profiles": frozenset({"id"})}, max_rows=100).ok
    assert not validate_sql("   ", {"job_profiles": frozenset({"id"})}, max_rows=100).ok


# --------------------------------------------------------------------------- #
# 生成器：验收②「无法安全映射拒答不猜测」
# --------------------------------------------------------------------------- #

def test_refusing_generator_returns_none():
    from app.modules.analytics.nl2sql.generator import RefusingGenerator
    assert RefusingGenerator().generate_sql("q", "schema") is None


def test_placeholder_key_raises_unavailable():
    with pytest.raises(GeneratorUnavailable):
        OpenAICompatGenerator(api_key="sk-your-llm-key-here")


def test_openai_compat_generator_parses_sql(monkeypatch):
    gen = OpenAICompatGenerator(api_key="sk-test-real-looking",
                                base_url="http://127.0.0.1:9", model="fake")

    class _Resp:
        class _C:
            message = SimpleNamespace(
                content="```sql\nSELECT id FROM job_profiles LIMIT 10\n```")
        choices = [_C()]

    monkeypatch.setattr(gen._client.chat.completions, "create", lambda **kw: _Resp())
    assert gen.generate_sql("有多少在招岗位", "job_profiles(...)") == \
        "SELECT id FROM job_profiles LIMIT 10"


def test_openai_compat_generator_cannot_answer_returns_none(monkeypatch):
    gen = OpenAICompatGenerator(api_key="sk-test-real-looking",
                                base_url="http://127.0.0.1:9", model="fake")

    class _Resp:
        class _C:
            message = SimpleNamespace(content="CANNOT_ANSWER")
        choices = [_C()]

    monkeypatch.setattr(gen._client.chat.completions, "create", lambda **kw: _Resp())
    assert gen.generate_sql("今天天气如何", "job_profiles(...)") is None  # 拒答不猜测


def test_openai_compat_generator_unavailable_on_call_failure(monkeypatch):
    gen = OpenAICompatGenerator(api_key="sk-test-real-looking",
                                base_url="http://127.0.0.1:9", model="fake")

    def _boom(**kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(gen._client.chat.completions, "create", _boom)
    with pytest.raises(GeneratorUnavailable):
        gen.generate_sql("q", "schema")


# --------------------------------------------------------------------------- #
# 编排 + 真实 PG 副本执行（集成：只读事务/超时/行上限/禁写兜底）
# --------------------------------------------------------------------------- #

class _FakeGenerator:
    def __init__(self) -> None:
        self.next_sql: str | None = None
        self.calls: list[str] = []

    def set(self, sql: str | None) -> None:
        self.next_sql = sql

    def generate_sql(self, question: str, schema_context: str) -> str | None:
        self.calls.append(question)
        return self.next_sql


@pytest.fixture()
def orchestration_env(tmp_path, monkeypatch):
    if not _pg_up():
        pytest.skip("分析 PG 副本(5433) 未运行")
    from app.config import settings

    pg = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Nl2SqlAudit.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(nl2sql_service, "_resolve_engine", lambda: (pg, "replica"))
    monkeypatch.setattr(nl2sql_service, "get_generator", lambda: fake_gen)
    monkeypatch.setattr(nl2sql_service, "SessionLocal", factory)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_timeout", lambda: 3)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_max_rows", lambda: 1000)
    reset_whitelist_cache()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    yield SimpleNamespace(pg=pg, factory=factory, fake=fake_gen, client=client)
    reset_whitelist_cache()
    engine.dispose()
    pg.dispose()


def _seed_replica_job(pg, title: str, n: int) -> int:
    """副本表无自增（PK 由 MySQL information_schema 映射，无 IDENTITY）——显式给 id。"""
    with pg.begin() as db:
        job_id = db.execute(
            text("SELECT COALESCE(MAX(id), 0) + 1 FROM job_profiles")).scalar_one()
        db.execute(text(
            "INSERT INTO job_profiles (id, title, pass_threshold, version, is_active) "
            "VALUES (:i, :t, 60, 1, 1)"
        ), {"i": job_id, "t": title})
        app_base = db.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM applications")).scalar_one()
        db.execute(text(
            "INSERT INTO applications (id, talent_profile_id, job_profile_id, "
            "internal_stage, external_stage) "
            "SELECT :base + g, 990000 + g, :j, 'offer_accepted', 'closed' "
            "FROM generate_series(1, :n) g"
        ), {"base": app_base, "j": job_id, "n": n})
        return job_id


def _cleanup_replica_job(pg, job_id: int) -> None:
    with pg.begin() as db:
        db.execute(text(
            "DELETE FROM applications WHERE job_profile_id = :j"), {"j": job_id})
        db.execute(text("DELETE FROM job_profiles WHERE id = :j"), {"j": job_id})


def test_full_chain_generate_validate_execute_audit(orchestration_env):
    env = orchestration_env
    job_id = _seed_replica_job(env.pg, "NL2SQL链路测试", 3)
    env.fake.set(
        "SELECT title, COUNT(*) AS c FROM applications a "
        "JOIN job_profiles jp ON jp.id = a.job_profile_id "
        "WHERE jp.title = 'NL2SQL链路测试' GROUP BY title"
    )
    try:
        resp = env.client.post("/api/analytics/nl2sql/query",
                               json={"question": "这个岗位几人？"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["row_count"] == 1 and data["rows"][0]["c"] == 3
        assert data["source"] == "replica" and data["audit_id"] is not None
        with env.factory() as db:
            audit = db.get(Nl2SqlAudit, data["audit_id"])
        assert audit.stage == "executed" and audit.row_count == 1
    finally:
        _cleanup_replica_job(env.pg, job_id)


def test_generation_refusal_422_audited(orchestration_env):
    env = orchestration_env
    env.fake.set(None)  # 模型回答 CANNOT_ANSWER → 拒答不猜测（验收②）
    resp = env.client.post("/api/analytics/nl2sql/query",
                           json={"question": "公司明年战略是什么"})
    assert resp.status_code == 422
    with env.factory() as db:
        audits = db.execute(
            select(Nl2SqlAudit).where(Nl2SqlAudit.stage == "refused_unmappable")
        ).scalars().all()
    assert any(a.refused_reason and "无法安全映射" in a.refused_reason for a in audits)


def test_unsafe_sql_rejected_422_audited(orchestration_env):
    env = orchestration_env
    env.fake.set("DELETE FROM job_profiles")  # 生成器被注入 → 校验器拦截
    resp = env.client.post("/api/analytics/nl2sql/query", json={"question": "删掉所有岗位"})
    assert resp.status_code == 422 and "安全校验" in resp.json()["message"]
    with env.factory() as db:
        audits = db.execute(
            select(Nl2SqlAudit).where(Nl2SqlAudit.stage == "refused_unsafe")
        ).scalars().all()
    assert any(a.generated_sql and "DELETE" in a.generated_sql for a in audits)


def test_personal_denied_403(orchestration_env):
    env = orchestration_env
    env.client.app.dependency_overrides[get_current_user] = lambda: U_PERSONAL
    resp = env.client.post("/api/analytics/nl2sql/query", json={"question": "q"})
    assert resp.status_code == 403


def test_empty_question_422(orchestration_env):
    env = orchestration_env
    assert env.client.post("/api/analytics/nl2sql/query",
                           json={"question": "  "}).status_code == 422
    assert env.client.post("/api/analytics/nl2sql/query",
                           json={"question": "x" * 501}).status_code == 422


# ---- 真实 PG：超时 / 行上限 / 禁写兜底（验收③④硬强制层） ----

@pytest.mark.skipif(not _pg_up(), reason="分析 PG 副本(5433) 未运行")
def test_pg_statement_timeout_enforced():
    from app.config import settings
    from sqlalchemy import create_engine

    pg = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    with pytest.raises(QueryTimeoutError):
        execute_readonly(pg, "SELECT pg_sleep(5)", timeout_seconds=3, max_rows=100)


@pytest.mark.skipif(not _pg_up(), reason="分析 PG 副本(5433) 未运行")
def test_pg_row_limit_and_truncated():
    from app.config import settings
    from sqlalchemy import create_engine

    pg = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    result = execute_readonly(pg, "SELECT generate_series(1, 5000) AS g",
                              timeout_seconds=10, max_rows=1000)
    assert result["row_count"] == 1000 and result["truncated"] is True


@pytest.mark.skipif(not _pg_up(), reason="分析 PG 副本(5433) 未运行")
def test_pg_read_only_rejects_write():
    from app.config import settings
    from sqlalchemy import create_engine

    pg = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    with pytest.raises(Exception) as ei:
        execute_readonly(pg, "INSERT INTO job_profiles (title) VALUES ('x')",
                         timeout_seconds=10, max_rows=100)
    assert "read" in str(ei.value).lower() or "只读" in str(ei.value)
