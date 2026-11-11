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
from app.modules.analytics.nl2sql.executor import (
    QueryTimeoutError,
    ReadOnlyViolation,
    execute_readonly,
)
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


# ---- D15 补强：子查询 LIMIT 误判 / INTO / EXPLAIN / PERFORM ----

def test_validator_subquery_limit_not_counted_as_top_level():
    """子查询里的 LIMIT 不算顶层——外层必须被包装限行（修复 flatten 误判）。"""
    vr = validate_sql(
        "SELECT * FROM (SELECT id FROM job_profiles LIMIT 5) AS sub",
        {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert vr.ok, vr.reason
    assert vr.sql.upper().endswith("LIMIT 100")
    assert "as _q" in vr.sql.lower()


def test_validator_top_level_limit_still_not_wrapped():
    """顶层 LIMIT 仍不包装（回归保护）。"""
    vr = validate_sql(
        "SELECT id FROM (SELECT id FROM job_profiles) AS sub LIMIT 7",
        {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert vr.ok and "AS _q" not in vr.sql and vr.sql.upper().endswith("LIMIT 7")


@pytest.mark.parametrize("sql", [
    "SELECT id INTO new_table FROM job_profiles",      # SELECT ... INTO（建表）
    "SELECT id FROM job_profiles INTO OUTFILE '/tmp/x'",  # INTO OUTFILE（写文件）
    "EXPLAIN SELECT id FROM job_profiles",             # EXPLAIN（含 ANALYZE 会真实执行）
    "EXPLAIN ANALYZE SELECT pg_sleep(10)",
    "ANALYZE job_profiles",                            # 维护命令
    "PERFORM pg_sleep(10)",                            # 执行表达式
])
def test_validator_dangerous_read_like_statements_rejected(sql):
    vr = validate_sql(sql, {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert not vr.ok, f"应当拒绝：{sql}"


def test_validator_into_inside_string_not_false_positive():
    """引号内 INTO 不误伤（回归保护）。"""
    vr = validate_sql(
        "SELECT id FROM job_profiles WHERE title = 'INTO OUTFILE'",
        {"job_profiles": frozenset({"id", "title"})}, max_rows=100)
    assert vr.ok, vr.reason


def test_validator_comment_write_keyword_not_executed():
    """注释内的写关键词不执行——校验放行是安全的（注释非语句）。"""
    vr = validate_sql(
        "SELECT id FROM job_profiles WHERE title = 'x' -- DROP TABLE job_profiles",
        {"job_profiles": frozenset({"id", "title"})}, max_rows=100)
    assert vr.ok, vr.reason


def test_validator_union_non_whitelist_table_rejected():
    """UNION 注入非白名单表 → 拒绝。"""
    vr = validate_sql(
        "SELECT id FROM job_profiles UNION SELECT password FROM users",
        {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert not vr.ok and "白名单" in vr.reason


def test_validator_union_whitelist_tables_allowed():
    """UNION 两侧均白名单 → 放行（不越权）。"""
    vr = validate_sql(
        "SELECT id FROM job_profiles UNION SELECT id FROM job_profiles",
        {"job_profiles": frozenset({"id"})}, max_rows=100)
    assert vr.ok, vr.reason


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


@pytest.mark.parametrize("content", [
    "我不知道这个问题怎么回答",
    "抱歉，我无法回答这个问题。",
    "I'm sorry, I can't help with that.",
    "",
])
def test_openai_compat_generator_non_sql_text_returns_none(monkeypatch, content):
    """拒答一致性：非 SQL 文本（空/自然语言/歧义）统一返回 None，不当 SQL 猜测。"""
    gen = OpenAICompatGenerator(api_key="sk-test-real-looking",
                                base_url="http://127.0.0.1:9", model="fake")

    class _Resp:
        class _C:
            message = SimpleNamespace(content=content)
        choices = [_C()]

    monkeypatch.setattr(gen._client.chat.completions, "create", lambda **kw: _Resp())
    assert gen.generate_sql("q", "job_profiles(...)") is None


def test_openai_compat_generator_with_cte_accepted(monkeypatch):
    """WITH CTE 开头应被接受（不误伤合法 SQL）。"""
    gen = OpenAICompatGenerator(api_key="sk-test-real-looking",
                                base_url="http://127.0.0.1:9", model="fake")

    class _Resp:
        class _C:
            message = SimpleNamespace(content="WITH c AS (SELECT 1) SELECT * FROM c")
        choices = [_C()]

    monkeypatch.setattr(gen._client.chat.completions, "create", lambda **kw: _Resp())
    assert gen.generate_sql("q", "job_profiles(...)") == "WITH c AS (SELECT 1) SELECT * FROM c"


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


# --------------------------------------------------------------------------- #
# 执行失败路径的审计状态（不依赖真实 PG：monkeypatch execute_readonly）
# --------------------------------------------------------------------------- #

@pytest.fixture()
def no_pg_env(tmp_path, monkeypatch):
    """SQLite 审计 + Fake 生成器 + 静态白名单；执行器由具体用例 monkeypatch。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Nl2SqlAudit.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    fake_gen = _FakeGenerator()
    fake_gen.set("SELECT id FROM job_profiles")

    monkeypatch.setattr(nl2sql_service, "_resolve_engine", lambda: (None, "fake"))
    monkeypatch.setattr(nl2sql_service, "get_generator", lambda: fake_gen)
    monkeypatch.setattr(nl2sql_service, "SessionLocal", factory)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_timeout", lambda: 3)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_max_rows", lambda: 1000)
    monkeypatch.setattr(
        nl2sql_service, "get_whitelist",
        lambda _engine: {"job_profiles": frozenset({"id"})},
    )
    reset_whitelist_cache()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    yield SimpleNamespace(factory=factory, fake=fake_gen, client=client)
    reset_whitelist_cache()
    engine.dispose()


def test_timeout_audit_state(no_pg_env, monkeypatch):
    """超时 → 503 + 审计 stage=timeout、refused_reason 落库。"""
    def _timeout(*a, **kw):
        raise QueryTimeoutError("查询超时")
    monkeypatch.setattr(nl2sql_service, "execute_readonly", _timeout)

    resp = no_pg_env.client.post("/api/analytics/nl2sql/query",
                                  json={"question": "q"})
    assert resp.status_code == 503
    with no_pg_env.factory() as db:
        audit = db.execute(
            select(Nl2SqlAudit).where(Nl2SqlAudit.stage == "timeout")
        ).scalars().first()
    assert audit is not None and audit.refused_reason == "查询超时"


def test_readonly_violation_audit_state(no_pg_env, monkeypatch):
    """只读违规 → 422 + 审计 stage=refused_unsafe。"""
    def _violation(*a, **kw):
        raise ReadOnlyViolation("只读事务拒绝写操作")
    monkeypatch.setattr(nl2sql_service, "execute_readonly", _violation)

    resp = no_pg_env.client.post("/api/analytics/nl2sql/query",
                                  json={"question": "q"})
    assert resp.status_code == 422
    with no_pg_env.factory() as db:
        audit = db.execute(
            select(Nl2SqlAudit).where(Nl2SqlAudit.stage == "refused_unsafe")
        ).scalars().first()
    assert audit is not None and "只读" in (audit.refused_reason or "")


def test_execution_error_audit_state(no_pg_env, monkeypatch):
    """执行期其余失败（如列不存在）→ 422 + 审计 stage=error。"""
    def _error(*a, **kw):
        raise RuntimeError("column does not exist")
    monkeypatch.setattr(nl2sql_service, "execute_readonly", _error)

    resp = no_pg_env.client.post("/api/analytics/nl2sql/query",
                                  json={"question": "q"})
    assert resp.status_code == 422
    with no_pg_env.factory() as db:
        audit = db.execute(
            select(Nl2SqlAudit).where(Nl2SqlAudit.stage == "error")
        ).scalars().first()
    assert audit is not None and "column does not exist" in (audit.refused_reason or "")


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
        # D15 审计绑定：原始生成 SQL 与实际执行 SQL 分离，截断标志落库
        assert audit.generated_sql and "NL2SQL链路测试" in audit.generated_sql
        assert audit.executed_sql and audit.executed_sql == audit.generated_sql
        assert audit.truncated == 0   # SQLite TINYINT；真实 MySQL 为 0/False
    finally:
        _cleanup_replica_job(env.pg, job_id)


def test_audit_records_executed_sql_and_truncated(tmp_path, monkeypatch):
    """审计绑定（SQLite 单测）：generated_sql 保留原始输出，executed_sql 记录实际执行 SQL，truncated 落库。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Nl2SqlAudit.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(nl2sql_service, "SessionLocal", factory)

    audit_id = nl2sql_service._audit(
        U_HR, "测试问题", "SELECT id FROM job_profiles",
        "generated", None, None, None)
    nl2sql_service._audit_update(
        audit_id, stage="validated",
        executed_sql="SELECT * FROM (SELECT id FROM job_profiles) AS _q LIMIT 100")
    nl2sql_service._audit_update(
        audit_id, stage="executed", row_count=1000, duration_ms=50, truncated=True)

    with factory() as db:
        audit = db.get(Nl2SqlAudit, audit_id)
    assert audit.generated_sql == "SELECT id FROM job_profiles"      # 原始输出不被覆盖
    assert audit.executed_sql == "SELECT * FROM (SELECT id FROM job_profiles) AS _q LIMIT 100"
    assert audit.stage == "executed" and audit.row_count == 1000
    assert audit.duration_ms == 50 and audit.truncated == 1   # SQLite TINYINT 返回 1


def test_audit_not_truncated_defaults_false(tmp_path, monkeypatch):
    """未触发截断时 truncated=False。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Nl2SqlAudit.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(nl2sql_service, "SessionLocal", factory)

    audit_id = nl2sql_service._audit(
        U_HR, "q", "SELECT 1", "generated", None, None, None)
    nl2sql_service._audit_update(
        audit_id, stage="executed", row_count=3, duration_ms=5, truncated=False)

    with factory() as db:
        audit = db.get(Nl2SqlAudit, audit_id)
    assert audit.truncated == 0 and audit.executed_sql is None  # 未校验前不写 executed_sql


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


def test_personal_denied_403_no_pg(no_pg_env):
    """不依赖 PG 的越权反例：personal 角色 403，且不泄露对象存在性。"""
    no_pg_env.client.app.dependency_overrides[get_current_user] = lambda: U_PERSONAL
    resp = no_pg_env.client.post("/api/analytics/nl2sql/query", json={"question": "q"})
    assert resp.status_code == 403
    assert resp.json()["message"] == "无权访问该资源"


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
