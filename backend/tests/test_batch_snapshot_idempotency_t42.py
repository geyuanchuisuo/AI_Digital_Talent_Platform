"""T-A42 批次快照与幂等（需求 §105 / §201 / §280 + 接口 §7/§8）。

逐条固化以下验收点，缺一不可：
1. 批量开始时保存岗位版本、分数线、本批人员名单、档案输入快照、模型/Prompt 版本；
2. 快照是值拷贝：岗位/档案变更不修改历史批次的输入快照；
3. 需要使用修改后的岗位/档案时**新建一轮**（不复用旧批次）；
4. retry 只补失败项、不重圈人、快照与 total 不变；
5. 重复请求不重复建结果（请求级幂等键 + 组合唯一约束并发兜底）；
6. 历史人工意见与已写结果行不被覆盖。

假件策略沿用 T-24/T-25（`fakes` / `client` / `auth`），造数与清理自包含，
避免依赖其它测试文件的 module-scope 状态。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.config import settings
from app.core.errors import BizError
from app.database import SessionLocal
from app.models import (
    JobProfile,
    MatchBatch,
    MatchResult,
    ParsedResume,
    ParseTask,
    ResumeFile,
    ScreenBatch,
    ScreenResult,
    TalentProfile,
    TalentTag,
)
from app.service import screen_service, match_service
from app.tasks.match_worker import handle_match_batch
from app.tasks.screen_worker import handle_screen_batch

# 复用 T-24 的成熟假件与登录夹具（它们不造业务数据，跨模块安全）
from tests.test_screen_t24 import auth, client, fakes  # noqa: F401

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 造数与清理
# --------------------------------------------------------------------------- #
@pytest.fixture()
def env():
    job_ids: list[int] = []
    profile_ids: list[int] = []
    parsed_ids: list[int] = []
    file_ids: list[int] = []

    def make_job(**fields) -> int:
        with SessionLocal() as db:
            row = JobProfile(
                title=fields.pop("title", "高级后端工程师"),
                jd_text="负责核心服务",
                requirements_text="本科 3 年",
                min_degree=fields.pop("min_degree", "本科"),
                min_work_years=Decimal("3"),
                must_skills_json=["python", "mysql"],
                bonus_skills_json=["milvus"],
                hard_rule_switches_json={"degree": True, "work_years": True, "must_skills": True},
                pass_threshold=Decimal("75"),
                pending_threshold=Decimal("60"),
                weights_json={"skills": 40, "experience": 30, "education": 15, "others": 15},
                version=1,
                is_active=1,
                created_by=1,
                **fields,
            )
            db.add(row)
            db.commit()
            job_ids.append(row.id)
            return row.id

    def make_profile(**fields) -> int:
        with SessionLocal() as db:
            row = TalentProfile(
                name=fields.pop("name", f"候选人{uuid4().hex[:6]}"),
                degree=fields.pop("degree", "本科"),
                work_years=fields.pop("work_years", Decimal("5.0")),
                skills_json=fields.pop("skills_json", ["python", "mysql"]),
                vector_status="synced",
                source="resume",
                **fields,
            )
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id

    def make_parsed(profile_id: int, *, batch_id: str | None = None) -> int:
        tag = uuid4().hex
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename=f"简历{tag[:6]}.pdf",
                stored_path=f"data/uploads/{tag}.pdf",
                file_hash=tag * 2,
                file_ext="pdf",
                file_size=1,
                upload_batch_id=batch_id or tag[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            db.add(ParseTask(resume_file_id=file_row.id, status="success", truncated=0))
            db.flush()
            db.add(
                ParsedResume(
                    resume_file_id=file_row.id,
                    talent_profile_id=profile_id,
                    structured_json={},
                    raw_text="x",
                )
            )
            db.commit()
            file_ids.append(file_row.id)
            return file_row.id

    yield SimpleNamespace(
        make_job=make_job,
        make_profile=make_profile,
        make_parsed=make_parsed,
    )

    # 按本测试创建的岗位反查批次，保证 HTTP 创建的批次也被清理
    with SessionLocal() as db:
        screen_ids: list[int] = []
        match_ids: list[int] = []
        if job_ids:
            screen_ids = list(
                db.execute(
                    select(ScreenBatch.id).where(ScreenBatch.job_profile_id.in_(job_ids))
                ).scalars().all()
            )
            match_ids = list(
                db.execute(
                    select(MatchBatch.id).where(MatchBatch.job_profile_id.in_(job_ids))
                ).scalars().all()
            )
        if screen_ids:
            db.execute(sa_delete(ScreenResult).where(ScreenResult.screen_batch_id.in_(screen_ids)))
        if match_ids:
            db.execute(sa_delete(MatchResult).where(MatchResult.match_batch_id.in_(match_ids)))
        if screen_ids:
            db.execute(sa_delete(ScreenBatch).where(ScreenBatch.id.in_(screen_ids)))
        if match_ids:
            db.execute(sa_delete(MatchBatch).where(MatchBatch.id.in_(match_ids)))
        if file_ids:
            db.execute(sa_delete(ParsedResume).where(ParsedResume.resume_file_id.in_(file_ids)))
            db.execute(sa_delete(ParseTask).where(ParseTask.resume_file_id.in_(file_ids)))
            db.execute(sa_delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        if profile_ids:
            db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
            db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
        if job_ids:
            db.execute(sa_delete(JobProfile).where(JobProfile.id.in_(job_ids)))
        db.commit()


def _post_screen(client, auth, job_id, scope, key=None):
    payload = {"job_profile_id": job_id, "scope": scope}
    if key is not None:
        payload["idempotency_key"] = key
    return client.post("/api/screen/batches", json=payload, headers=auth)


def _post_match(client, auth, job_id, scope, key=None):
    payload = {"job_profile_id": job_id, "scope": scope}
    if key is not None:
        payload["idempotency_key"] = key
    return client.post("/api/match/batches", json=payload, headers=auth)


def _scope_for(file_ids) -> dict:
    return {"type": "resume_file_ids", "ids": sorted(file_ids)}


def _batch(batch_id: int) -> ScreenBatch:
    with SessionLocal() as db:
        db.expire_all()
        return db.get(ScreenBatch, batch_id)


def _create(env, file_ids, *, key=None) -> int:
    job_id = env.make_job()
    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, _scope_for(file_ids), user_id=1, idempotency_key=key
        )
        return data["batch_id"]


# --------------------------------------------------------------------------- #
# A. 快照固定性
# --------------------------------------------------------------------------- #
def test_snapshot_pins_model_prompt_versions_and_config(env):
    job_id = env.make_job()
    with SessionLocal() as db:
        job = db.get(JobProfile, job_id)
        snap = screen_service.build_config_snapshot(job)

    assert snap["model_version"] == settings.llm_model
    assert snap["prompt_version"] == settings.prompt_version
    for key in (
        "title", "jd_text", "requirements_text", "min_degree", "min_work_years",
        "must_skills", "bonus_skills", "hard_rule_switches",
        "pass_threshold", "pending_threshold", "weights",
    ):
        assert key in snap, f"批次快照缺固定字段 {key}"
    assert snap["pass_threshold"] == 75.0 and snap["pending_threshold"] == 60.0


def test_snapshot_is_value_copy_when_job_changes(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    resp = _post_screen(client, auth, job_id, _scope_for([f]))
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["data"]["batch_id"]
    original = dict(_batch(batch_id).config_snapshot_json)

    with SessionLocal() as db:
        job = db.get(JobProfile, job_id)
        job.title = "改后的岗位名"
        job.pass_threshold = Decimal("90")
        job.weights_json = {"skills": 100}
        job.version = 2
        db.commit()

    after = dict(_batch(batch_id).config_snapshot_json)
    assert after == original, "岗位变更不得修改历史批次的输入快照"
    assert after["title"] == "高级后端工程师"
    assert after["pass_threshold"] == 75.0


def test_modified_job_creates_a_new_round(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    first = _post_screen(client, auth, job_id, _scope_for([f]))
    assert first.status_code == 202, first.text
    batch1 = first.json()["data"]["batch_id"]

    with SessionLocal() as db:
        job = db.get(JobProfile, job_id)
        job.title = "第二轮岗位"
        job.version = 2
        db.commit()

    second = _post_screen(client, auth, job_id, _scope_for([f]))
    assert second.status_code == 202, second.text
    batch2 = second.json()["data"]["batch_id"]
    assert batch2 != batch1, "岗位变更后必须新建一轮，不能复用旧批次"
    assert _batch(batch1).job_profile_version == 1
    assert _batch(batch2).job_profile_version == 2
    assert _batch(batch2).config_snapshot_json["title"] == "第二轮岗位"


def test_retry_keeps_snapshot_scope_and_total(env, fakes):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id = _create(env, [f])
    before = _batch(batch_id)
    snapshot = dict(before.config_snapshot_json)
    scope = dict(before.scope_json)

    with SessionLocal() as db:
        db.get(ScreenBatch, batch_id).status = "failed"
        db.commit()
        result = screen_service.retry_batch(db, batch_id)

    assert result["retry_count"] == 1 and result["status"] == "pending"
    after = _batch(batch_id)
    assert dict(after.config_snapshot_json) == snapshot
    assert dict(after.scope_json) == scope
    assert after.total == before.total


def test_resolve_batch_model_versions_prefers_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "runtime-changed-model")
    monkeypatch.setattr(settings, "prompt_version", "runtime-changed-prompt")
    model, prompt = screen_service.resolve_batch_model_versions(
        {"model_version": "pinned-model", "prompt_version": "pinned-prompt"}
    )
    assert (model, prompt) == ("pinned-model", "pinned-prompt")


def test_resolve_batch_model_versions_legacy_fallback_warns(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_model", "current-model")
    monkeypatch.setattr(settings, "prompt_version", "current-prompt")
    with caplog.at_level(logging.WARNING):
        model, prompt = screen_service.resolve_batch_model_versions({}, logger=logging.getLogger("t42"))
    assert (model, prompt) == ("current-model", "current-prompt")
    assert any("历史批次无法追溯" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- #
# B. worker 只读快照版本
# --------------------------------------------------------------------------- #
def test_screen_worker_writes_snapshot_versions_not_runtime_settings(env, fakes, monkeypatch):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id = _create(env, [f])
    snap = dict(_batch(batch_id).config_snapshot_json)

    monkeypatch.setattr(settings, "llm_model", "runtime-changed-model")
    monkeypatch.setattr(settings, "prompt_version", "runtime-changed-prompt")
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        row = db.execute(
            select(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalar_one()
        assert row.model_version == snap["model_version"]
        assert row.prompt_version == snap["prompt_version"]
        assert row.model_version != "runtime-changed-model"


def test_retry_refill_keeps_versions_consistent_across_batch(env, fakes, monkeypatch):
    p1, p2 = env.make_profile(), env.make_profile()
    f1, f2 = env.make_parsed(p1), env.make_parsed(p2)
    batch_id = _create(env, [f1, f2])
    snap = dict(_batch(batch_id).config_snapshot_json)
    handle_screen_batch(batch_id)

    # 模拟「一人失败未写行」：删一行 + 置 failed，retry 只补缺
    with SessionLocal() as db:
        row = db.execute(
            select(ScreenResult).where(
                ScreenResult.screen_batch_id == batch_id,
                ScreenResult.talent_profile_id == p2,
            )
        ).scalar_one()
        db.delete(row)
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "failed"
        batch.error = "注入：一人失败"
        db.commit()

    monkeypatch.setattr(settings, "llm_model", "runtime-changed-model")
    monkeypatch.setattr(settings, "prompt_version", "runtime-changed-prompt")
    with SessionLocal() as db:
        screen_service.retry_batch(db, batch_id)
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        rows = db.execute(
            select(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalars().all()
    assert len(rows) == 2, "retry 只补缺，结果行数不增不减"
    assert {r.model_version for r in rows} == {snap["model_version"]}
    assert {r.prompt_version for r in rows} == {snap["prompt_version"]}


def test_match_worker_writes_snapshot_versions(env, fakes, monkeypatch):
    job_id = env.make_job()
    p = env.make_profile()
    tag = uuid4().hex[:26].upper()
    env.make_parsed(p, batch_id=tag)
    # 匹配侧 scope 只有 all / filter / upload_batch（无 resume_file_ids）
    scope = {"type": "upload_batch", "upload_batch_id": tag}
    with SessionLocal() as db:
        data = match_service.create_batch(db, job_id, scope, user_id=1)
    batch_id = data["batch_id"]
    with SessionLocal() as db:
        snap = dict(db.get(MatchBatch, batch_id).config_snapshot_json)

    monkeypatch.setattr(settings, "llm_model", "runtime-changed-model")
    monkeypatch.setattr(settings, "prompt_version", "runtime-changed-prompt")
    handle_match_batch(batch_id)

    with SessionLocal() as db:
        row = db.execute(
            select(MatchResult).where(MatchResult.match_batch_id == batch_id)
        ).scalar_one()
        assert row.model_version == snap["model_version"]
        assert row.prompt_version == snap["prompt_version"]


# --------------------------------------------------------------------------- #
# C. 请求级幂等
# --------------------------------------------------------------------------- #
def test_duplicate_request_returns_same_batch_and_no_duplicate_results(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    scope = _scope_for([f])
    first = _post_screen(client, auth, job_id, scope, key="round-001")
    assert first.status_code == 202, first.text
    batch_id = first.json()["data"]["batch_id"]
    second = _post_screen(client, auth, job_id, scope, key="round-001")
    assert second.status_code == 202, second.text
    assert second.json()["data"]["batch_id"] == batch_id

    with SessionLocal() as db:
        count = db.execute(
            select(ScreenBatch.id).where(ScreenBatch.job_profile_id == job_id)
        ).scalars().all()
        assert count == [batch_id]
        assert db.get(ScreenBatch, batch_id).idempotency_key == "round-001"

    handle_screen_batch(batch_id)
    with SessionLocal() as db:
        results = db.execute(
            select(ScreenResult.id).where(ScreenResult.screen_batch_id == batch_id)
        ).scalars().all()
    assert len(results) == 1, "重复请求不得重复建结果"


def test_replay_never_re_resolves_new_members(env, fakes, client, auth):
    job_id = env.make_job()
    tag = uuid4().hex[:26].upper()
    p1 = env.make_profile()
    env.make_parsed(p1, batch_id=tag)
    scope = {"type": "upload_batch", "upload_batch_id": tag}
    first = _post_screen(client, auth, job_id, scope, key="round-upload")
    assert first.status_code == 202, first.text
    batch_id = first.json()["data"]["batch_id"]

    # replay 之前新增一名同批次候选人：若重圈人，total 会变成 2
    p2 = env.make_profile()
    env.make_parsed(p2, batch_id=tag)
    replay = _post_screen(client, auth, job_id, scope, key="round-upload")
    assert replay.status_code == 202, replay.text
    data = replay.json()["data"]
    assert data["batch_id"] == batch_id
    assert data["total"] == 1
    row = _batch(batch_id)
    assert row.scope_json["resolved_ids"] == [p1]
    assert p2 not in row.scope_json["resolved_ids"]


def test_different_key_and_no_key_start_new_rounds(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    scope = _scope_for([f])
    ids = []
    for key in ("key-a", "key-b", None):
        resp = _post_screen(client, auth, job_id, scope, key=key)
        assert resp.status_code == 202, resp.text
        ids.append(resp.json()["data"]["batch_id"])
    assert len(set(ids)) == 3, "不同键 / 不传键均应新建一轮"
    assert _batch(ids[2]).idempotency_key is None


def test_same_key_on_different_jobs_is_independent(env, fakes, client, auth):
    j1, j2 = env.make_job(), env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    scope = _scope_for([f])
    r1 = _post_screen(client, auth, j1, scope, key="shared-key")
    r2 = _post_screen(client, auth, j2, scope, key="shared-key")
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["data"]["batch_id"] != r2.json()["data"]["batch_id"], "幂等键限定在岗位内"


def test_normalize_idempotency_key_rules():
    assert screen_service.normalize_idempotency_key(None) is None
    assert screen_service.normalize_idempotency_key("") is None
    assert screen_service.normalize_idempotency_key("   ") is None
    assert screen_service.normalize_idempotency_key("  k1  ") == "k1"
    with pytest.raises(BizError) as exc:
        screen_service.normalize_idempotency_key("x" * 65)
    assert exc.value.status_code == 400


def test_too_long_key_returns_400(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    resp = _post_screen(client, auth, job_id, _scope_for([f]), key="x" * 65)
    assert resp.status_code == 400, resp.text


def test_concurrent_unique_constraint_falls_back_to_existing_batch(env, fakes, client, auth, monkeypatch):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    scope = _scope_for([f])
    first = _post_screen(client, auth, job_id, scope, key="race-001")
    assert first.status_code == 202, first.text
    batch_id = first.json()["data"]["batch_id"]

    # 模拟查询竞态：第一次查不到（另一个事务尚未提交），插入时撞唯一约束
    real_find = screen_service._find_batch_by_key
    state = {"calls": 0}

    def flaky_find(db, job_profile_id, key):
        state["calls"] += 1
        if state["calls"] == 1:
            return None
        return real_find(db, job_profile_id, key)

    monkeypatch.setattr(screen_service, "_find_batch_by_key", flaky_find)
    with SessionLocal() as db:
        replay = screen_service.create_batch(
            db, job_id, scope, user_id=1, idempotency_key="race-001"
        )
    assert replay["batch_id"] == batch_id, "IntegrityError 兜底必须回查并返回原批次"
    assert state["calls"] == 2
    with SessionLocal() as db:
        rows = db.execute(
            select(ScreenBatch.id).where(ScreenBatch.job_profile_id == job_id)
        ).scalars().all()
    assert rows == [batch_id]


def test_match_duplicate_request_returns_same_batch(env, fakes, client, auth):
    job_id = env.make_job()
    p = env.make_profile()
    f = env.make_parsed(p)
    scope = {"type": "upload_batch", "upload_batch_id": uuid4().hex[:26].upper()}
    env.make_parsed(p, batch_id=scope["upload_batch_id"])
    first = _post_match(client, auth, job_id, scope, key="match-001")
    assert first.status_code == 202, first.text
    batch_id = first.json()["data"]["batch_id"]
    second = _post_match(client, auth, job_id, scope, key="match-001")
    assert second.status_code == 202, second.text
    assert second.json()["data"]["batch_id"] == batch_id
    with SessionLocal() as db:
        rows = db.execute(
            select(MatchBatch.id).where(MatchBatch.job_profile_id == job_id)
        ).scalars().all()
        assert rows == [batch_id]


# --------------------------------------------------------------------------- #
# D. 档案输入快照与人工意见不被覆盖
# --------------------------------------------------------------------------- #
def test_profile_input_snapshot_and_score_immutable_on_retry(env, fakes):
    p = env.make_profile(name="原始姓名", degree="本科", skills_json=["python"])
    f = env.make_parsed(p)
    batch_id = _create(env, [f])
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        row = db.execute(
            select(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalar_one()
        original_snapshot = dict(row.profile_input_snapshot_json or {})
        original_score = float(row.score)

    with SessionLocal() as db:
        profile = db.get(TalentProfile, p)
        profile.name = "改后姓名"
        profile.degree = "硕士"
        profile.skills_json = ["java"]
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "failed"
        batch.error = "注入失败"
        db.commit()
        screen_service.retry_batch(db, batch_id)
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        rows = db.execute(
            select(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalars().all()
    assert len(rows) == 1
    assert dict(rows[0].profile_input_snapshot_json or {}) == original_snapshot
    assert rows[0].profile_input_snapshot_json["name"] == "原始姓名"
    assert float(rows[0].score) == original_score


def test_manual_verdict_survives_retry(env, fakes):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id = _create(env, [f])
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        row = db.execute(
            select(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalar_one()
        result_id = row.id
        screen_service.set_manual_verdict(db, result_id, "passed", user_id=1)
    with SessionLocal() as db:
        before = db.get(ScreenResult, result_id)
        assert before.manual_verdict == "passed"
        marker = (before.manual_verdict_by, before.manual_verdict_at)

    with SessionLocal() as db:
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "failed"
        batch.error = "注入失败"
        db.commit()
        screen_service.retry_batch(db, batch_id)
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        after = db.get(ScreenResult, result_id)
        batch = db.get(ScreenBatch, batch_id)
    assert after.manual_verdict == "passed", "历史人工意见不得被 retry 覆盖"
    assert (after.manual_verdict_by, after.manual_verdict_at) == marker
    assert batch.passed == 1 and batch.pending_count == 0 and batch.rejected == 0
