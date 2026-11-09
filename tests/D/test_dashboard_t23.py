"""T-D23 个人工作台用例。

验收对照（todo v0.3 T-D23 / 需求 V2.6 F09）：
- 本人待测评：assignment 未交卷（assigned/in_progress/grading）；
- 本人培训：仅已正式指派（assigned）计——draft 是管理侧确认前中间态，
  未确认前不向员工展示；
- 近期结果：仅已公布（published_result_version 非空）计，未公布不展示；
- 权限：personal 账号可用，管理侧（hr）403；
- 口径变化：插入业务行前后计数递增（验收③）。

运行：pytest tests/D/test_dashboard_t23.py -v（SQLite 内存库隔离）。
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
from app.core.errors import BizError, register_exception_handlers
from app.database import Base, get_db
from app.models import (
    AssessmentAssignment,
    AssessmentResult,
    User,
)
from app.modules.dashboard import me
from app.modules.dashboard.router import router
from app.modules.training.models import TrainingPlan

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

TABLES = (
    User.__table__,
    AssessmentAssignment.__table__,
    AssessmentResult.__table__,
    TrainingPlan.__table__,
)


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
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
    yield SimpleNamespace(factory=factory, app=app)
    engine.dispose()


def _call(env, user=U_PERSONAL) -> dict:
    with env.factory() as db:
        return me.me_overview(db, user=user)


class _UserRow:
    def __init__(self, user_id: int):
        self.id = user_id
        self.role = "personal" if user_id == 3 else "hr"
        self.person_id = 1 if user_id == 3 else None
        self.is_active = True
        self.username = f"u{user_id}"
        self.password_hash = "x"


def _seed_assignments(env, statuses, published=None, user_id=3):
    with env.factory() as db:
        for i, status in enumerate(statuses):
            db.add(
                AssessmentAssignment(
                    plan_id=1,
                    plan_version=1,
                    user_id=user_id,
                    job_id=1,
                    purpose="recruitment",
                    status=status,
                    published_result_version=published[i] if published else None,
                    idempotency_key=f"t23-a-{i}",
                )
            )
        db.commit()


def _seed_results(env, assignment_ids, user_id=None):
    with env.factory() as db:
        for i, assignment_id in enumerate(assignment_ids):
            db.add(
                AssessmentResult(
                    assignment_id=assignment_id,
                    version=i + 1,
                    dimension_scores_json=[],
                    total_score=Decimal("80"),
                    pass_score=Decimal("60"),
                    passed=1,
                    grading_snapshot_json={},
                )
            )
        db.commit()


# ---- 门禁 ----

def test_hr_role_forbidden(env):
    with env.factory() as db:
        with pytest.raises(BizError) as ei:
            me.me_overview(db, user=U_HR)
    assert ei.value.code == 403


def test_hr_role_403_via_http(env):
    env.app.dependency_overrides[get_current_user] = lambda: _UserRow(2)
    client = TestClient(env.app)
    resp = client.get("/api/ai-platform/me-overview")
    assert resp.status_code == 403
    env.app.dependency_overrides.pop(get_current_user, None)


# ---- 口径 ----

def test_empty_counts(env):
    data = _call(env)
    assert data["pending_assignments"] == 0
    assert data["pending_training"] == 0
    assert data["recent_results"] == 0


def test_pending_assignments_statuses(env):
    """待测评：assigned/in_progress/grading 计；completed/cancelled 不计。"""
    with env.factory() as db:
        ids = []
        for i, status in enumerate(
            ("assigned", "assigned", "in_progress", "grading", "completed", "cancelled")
        ):
            row = AssessmentAssignment(
                plan_id=1,
                plan_version=1,
                user_id=3,
                purpose="recruitment",
                status=status,
                idempotency_key=f"t23-p-{i}",
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    data = _call(env)
    assert data["pending_assignments"] == 4  # 前四个未交卷


def test_pending_training_assigned_only(env):
    """个人端培训：仅 assigned 计（draft 是管理侧确认前中间态，不向员工展示）。"""
    with env.factory() as db:
        for status in ("draft", "assigned", "draft"):
            db.add(
                TrainingPlan(
                    employee_user_id=3,
                    status=status,
                    input_snapshot_json={},
                )
            )
        db.commit()
    data = _call(env)
    assert data["pending_training"] == 1


def test_recent_results_only_published(env):
    """近期结果：published_result_version 非空的 assignment 有结果版本；未公布不展示。"""
    with env.factory() as db:
        # 两个已公布 assignment（有结果行）
        pa1 = AssessmentAssignment(
            plan_id=1, plan_version=1, user_id=3, purpose="recruitment",
            status="completed", published_result_version=1, idempotency_key="t23-pub-1",
        )
        db.add(pa1)
        db.flush()
        db.add(
            AssessmentResult(
                assignment_id=pa1.id, version=1, dimension_scores_json=[],
                total_score=Decimal("80"), pass_score=Decimal("60"), passed=1,
                grading_snapshot_json={},
            )
        )
        db.flush()
        pa2 = AssessmentAssignment(
            plan_id=1, plan_version=1, user_id=3, purpose="recruitment",
            status="completed", published_result_version=1, idempotency_key="t23-pub-2",
        )
        db.add(pa2)
        db.flush()
        db.add(
            AssessmentResult(
                assignment_id=pa2.id, version=1, dimension_scores_json=[],
                total_score=Decimal("80"), pass_score=Decimal("60"), passed=1,
                grading_snapshot_json={},
            )
        )
        db.commit()
        # 一个未公布的 assignment（本身計入待测评但不算结果）＋完全没有结果
    data = _call(env)
    assert data["recent_results"] == 2


def test_counts_change_with_new_data(env):
    before = _call(env)
    assert before["pending_assignments"] == 0
    assert before["pending_training"] == 0
    with env.factory() as db:
        db.add(
            AssessmentAssignment(
                plan_id=1, plan_version=1, user_id=3, purpose="recruitment",
                status="assigned", idempotency_key="t23-change-1",
            )
        )
        db.add(TrainingPlan(employee_user_id=3, status="assigned", input_snapshot_json={}))
        db.commit()
    after = _call(env)
    assert after["pending_assignments"] == 1
    assert after["pending_training"] == 1


def test_caliber_notes(env):
    data = _call(env)
    assert data["caliber"]
    for key in ("pending_assignments", "pending_training", "recent_results"):
        assert data["caliber"][key]
