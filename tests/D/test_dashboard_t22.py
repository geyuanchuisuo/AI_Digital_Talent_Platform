"""T-D22 管理员工作台用例。

验收对照（todo v0.3 T-D22 / 需求 V2.6 F09）：
①六类数字与业务记录回查一致——种子业务行后逐卡断言计数 == 人工 SQL 口径；
②未公布成绩/内部反馈/人才池不出现——响应字符串化后不含相关敏感内容断言；
③新增业务数据后数量随之变化——插入前后两次查询计数递增；
越权：personal 403（deny-by-default 门禁）；
软删排除：deleted_at 非空档案不计入。

运行：pytest tests/D/test_dashboard_t22.py -v（SQLite 内存库隔离）。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.database import Base, get_db
from app.models import (
    Application,
    AssessmentAssignment,
    JobProfile,
    MatchBatch,
    MatchResult,
    ProjectDemand,
    ScreenBatch,
    ScreenResult,
    TalentProfile,
    TrainingPlan,
    User,
)
from app.modules.dashboard import overview
from app.modules.dashboard.router import router
from app.modules.inventory.models import InventoryReview
from app.modules.training.models import TrainingPlan as TrainingPlanModel

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

TABLES = (
    User.__table__,
    JobProfile.__table__,
    TalentProfile.__table__,
    ScreenBatch.__table__,
    ScreenResult.__table__,
    MatchBatch.__table__,
    MatchResult.__table__,
    ProjectDemand.__table__,
    Application.__table__,
    AssessmentAssignment.__table__,
    TrainingPlanModel.__table__,
    InventoryReview.__table__,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.models import SysRole, SysUserRole, UserScopeGrant
    Base.metadata.create_all(engine, tables=[SysRole.__table__, SysUserRole.__table__, UserScopeGrant.__table__])
    monkeypatch.setattr('app.database.SessionLocal', factory)
    from app.models import User as ScopeUser
    ScopeUser.__table__.create(factory.kw['bind'], checkfirst=True)
    with factory() as scope_db:
        for key, value in list(globals().items()):
            if key.startswith('U_') and isinstance(getattr(value, 'id', None), int) and hasattr(value, 'role') and value.id not in [2, 3]:
                if scope_db.get(ScopeUser, value.id) is None:
                    scope_db.add(ScopeUser(id=value.id, username='scope_fixture_'+str(value.id),
                        password_hash='test-only', role=value.role, person_id=getattr(value, 'person_id', None)))
        scope_db.commit()

    with factory() as db:
        db.add(User(id=2, username="hr1", password_hash="x", role="hr"))
        db.add(User(id=3, username="p", password_hash="x", role="personal", person_id=1))
        db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    def override_db():
        db2 = factory()
        try:
            yield db2
        finally:
            db2.close()

    app.dependency_overrides[get_db] = override_db
    yield SimpleNamespace(
        engine=engine, factory=factory, app=app, monkeypatch=None, engine_source="primary"
    )
    engine.dispose()


def _call(env, user=U_HR) -> dict:
    with env.factory() as db:
        return overview.admin_overview(
            db, user=user, engine=env.engine, engine_source=env.engine_source
        )


def _make_job(db, title="后端", version=1, active=1) -> JobProfile:
    row = JobProfile(
        title=title,
        version=version,
        is_active=active,
        jd_text="x",
        requirements_text="x",
        hard_rule_switches_json={},
        weights_json={},
        pass_threshold=Decimal("60"),
    )
    db.add(row)
    db.flush()
    return row


def _make_profile(db, name="张三", deleted=False) -> TalentProfile:
    row = TalentProfile(
        name=name,
        skills_json=[],
        manual_edited_fields=[],
        deleted_at=None if not deleted else __import__("datetime").datetime(2026, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def _make_screen_result(db, verdict="pending", manual=None) -> ScreenResult:
    job = _make_job(db, title=f"scr_{verdict}_{manual}")
    batch = ScreenBatch(
        job_profile_id=job.id,
        job_profile_version=job.version,
        config_snapshot_json={"pass_threshold": 75, "pending_threshold": 60},
        scope_json={},
        created_by=2,
        status="done",
    )
    db.add(batch)
    db.flush()
    profile = _make_profile(db, name=f"p_scr_{verdict}_{manual}")
    row = ScreenResult(
        screen_batch_id=batch.id,
        job_profile_id=job.id,
        talent_profile_id=profile.id,
        score=Decimal("70"),
        verdict=verdict,
        manual_verdict=manual,
    )
    db.add(row)
    db.flush()
    return row


def _make_match_result(db, manual) -> MatchResult:
    job = _make_job(db, title=f"m_{manual}")
    batch = MatchBatch(
        job_profile_id=job.id,
        job_profile_version=job.version,
        config_snapshot_json={},
        talent_scope_json={},
        created_by=2,
        status="done",
    )
    db.add(batch)
    db.flush()
    profile = _make_profile(db, name=f"p_m_{manual}")
    row = MatchResult(
        match_batch_id=batch.id,
        talent_profile_id=profile.id,
        score=Decimal("80"),
        manual_verdict=manual,
    )
    db.add(row)
    db.flush()
    return row


def _make_inventory_review(db, status) -> InventoryReview:
    row = InventoryReview(
        employee_user_id=2,
        cycle_name="2026A",
        status=status,
    )
    db.add(row)
    db.flush()
    return row


# ---- 门禁 ----

def test_personal_forbidden(env):
    with env.factory() as db:
        with pytest.raises(Exception) as ei:
            overview.admin_overview(
                db, user=U_PERSONAL, engine=env.engine, engine_source="primary"
            )
    assert ei.value.code == 403


def test_personal_403_via_http(env):
    env.app.dependency_overrides[get_current_user] = lambda: _UserRow(3)
    client = TestClient(env.app)
    resp = client.get("/api/ai-platform/admin-overview")
    assert resp.status_code == 403
    env.app.dependency_overrides.pop(get_current_user, None)


class _UserRow:
    """最小 User 形状（id/role/person_id），供 get_current_user 覆写。"""

    def __init__(self, user_id: int):
        self.id = user_id
        self.role = "personal" if user_id == 3 else "hr"
        self.person_id = 1 if user_id == 3 else None
        self.is_active = True
        self.username = f"u{user_id}"
        self.password_hash = "x"


# ---- 验收①：六类数字与业务记录回查一致 ----

def test_empty_counts(env):
    data = _call(env)
    cards = data["cards"]
    assert cards["jobs"]["count"] == 0
    assert cards["profiles"]["count"] == 0
    assert cards["reviews"]["count"] == 0
    assert cards["scoring"]["count"] == 0
    assert cards["training"]["count"] == 0
    assert cards["gaps"]["count"] == 0


def test_jobs_active_caliber(env):
    with env.factory() as db:
        _make_job(db, title="后端", version=1, active=1)
        _make_job(db, title="后端", version=2, active=0)  # 新版未启用
        _make_job(db, title="前端", version=1, active=1)
        _make_job(db, title="算法", version=1, active=0)
        db.commit()
    data = _call(env)
    assert data["cards"]["jobs"]["count"] == 1  # 仅前端 v1


def test_profiles_exclude_deleted(env):
    with env.factory() as db:
        _make_profile(db, name="在职")
        _make_profile(db, name="已删", deleted=True)
        db.commit()
    data = _call(env)
    assert data["cards"]["profiles"]["count"] == 1


def test_reviews_three_way_breakdown(env):
    with env.factory() as db:
        # 初筛：自动 pending + 人工改判覆盖
        _make_screen_result(db, verdict="pending")
        _make_screen_result(db, verdict="pending", manual="passed")  # 有效结论 passed，不计
        _make_screen_result(db, verdict="passed", manual="pending")  # 人工优先，计
        # 匹配：未标记 2 行、已标记 1 行
        _make_match_result(db, manual=None)
        _make_match_result(db, manual=None)
        _make_match_result(db, manual="passed")
        # 盘点：draft/submitted 计、confirmed 不计
        _make_inventory_review(db, "draft")
        _make_inventory_review(db, "submitted")
        _make_inventory_review(db, "confirmed")
        db.commit()
    data = _call(env)
    breakdown = data["cards"]["reviews"]["breakdown"]
    assert breakdown["screen_pending"] == 2
    assert breakdown["match_unmarked"] == 2
    assert breakdown["inventory_pending"] == 2
    assert data["cards"]["reviews"]["count"] == 6


def test_scoring_grading_only(env):
    with env.factory() as db:
        job = _make_job(db)
        for i, status in enumerate(("grading", "grading", "completed", "assigned")):
            db.add(
                AssessmentAssignment(
                    plan_id=0,
                    plan_version=1,
                    user_id=3,
                    job_id=job.id,
                    purpose="recruitment",
                    status=status,
                    idempotency_key=f"t22-{i}",
                )
            )
        db.commit()
    data = _call(env)
    assert data["cards"]["scoring"]["count"] == 2


def test_training_draft_and_assigned(env):
    with env.factory() as db:
        for status in ("draft", "assigned", "draft"):
            db.add(
                TrainingPlan(
                    employee_user_id=2,
                    status=status,
                    input_snapshot_json={},
                )
            )
        db.commit()
    data = _call(env)
    assert data["cards"]["training"]["count"] == 3


def test_gaps_caliber(env):
    """缺口 = headcount − HR 确认已录用（逐需求非负）；预警数 = gap>0 行数。"""
    with env.factory() as db:
        job = _make_job(db)
        db.add(
            ProjectDemand(
                position_name="Java",
                headcount=3,
                job_profile_id=job.id,
                status="open",
            )
        )
        db.flush()
        profile = _make_profile(db, name="已录用")
        db.add(
            Application(
                job_profile_id=job.id,
                talent_profile_id=profile.id,
                internal_stage="offer_accepted",
                result_confirmed_by=2,
            )
        )
        db.commit()
    data = _call(env)
    gaps = data["cards"]["gaps"]
    assert gaps["count"] == 1  # 一条需求 gap=2>0
    assert gaps["breakdown"]["total_gap"] == 2


# ---- 验收②：敏感数据不出现 ----

def test_no_sensitive_content(env):
    """响应只含计数与口径字符串：未公布成绩、面试评价、人才池类别不在任何字段。"""
    with env.factory() as db:
        _make_screen_result(db, verdict="pending")
        _make_match_result(db, manual=None)
        _make_inventory_review(db, "submitted")
        _make_profile(db, name="王五")
        db.commit()
    data = _call(env)
    text = str(data)
    # 人才池类别值与内部评级不在响应
    for banned in ("high_potential", "key_role", "nominated", "grid_cell", "performance_score"):
        assert banned not in text, f"响应含敏感字段 {banned}"
    # 业务明细行不出现（只有计数）
    assert "张三" not in text and "王五" not in text
    assert "evaluation" not in text and "recommendation" not in text


# ---- 验收③：新增数据后数量随之变化 ----

def test_counts_change_with_new_data(env):
    before = _call(env)
    assert before["cards"]["profiles"]["count"] == 0
    assert before["cards"]["reviews"]["count"] == 0
    with env.factory() as db:
        _make_screen_result(db, verdict="pending")
        _make_profile(db, name="新档案")
        db.commit()
    after = _call(env)
    assert after["cards"]["profiles"]["count"] == 2  # screen 种子自带 1 档案 + 新档案
    assert after["cards"]["reviews"]["count"] == 1


# ---- 业务入口与口径说明 ----

def test_cards_have_entry_and_caliber(env):
    data = _call(env)
    for key, card in data["cards"].items():
        assert card["entry"].startswith("/"), f"{key} 缺业务入口"
        assert card["caliber"], f"{key} 缺口径说明"
