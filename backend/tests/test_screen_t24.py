"""T-24 L2：筛选批次服务 + worker + API（真实 MySQL；LLM/Embedding/Milvus 全假件）。

覆盖 todolist T-24 验收点：
1. POST 202（同步圈人 + 成员快照 + estimated_llm_calls=快照内有效画像数，A02-1 起不折减）；
2. 批次运行中改岗位条件 → 本批次结果不变（快照隔离）；
3. 批次创建后再上传新简历/删除画像 → total 与结果集不变（新画像不补进、软删跳过计 skipped_deleted）；
4. worker 断点续跑（杀进程模拟：重置 running→pending 保留进度，只补缺）；
5. retry：结果行数不变、retry_count 递增、快照与 total 不变；
6. 改判（manual_verdict）后计数同步、撤销恢复；
7. Milvus 停机时筛选照常 done 且分数与正常时完全一致（use_vector=False 零向量依赖）；
8. 三档计数 done 时按结果表重算 + 有效结论过滤；A02-1 起不再有硬淘汰置底。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete

from app.core import embedding_client, llm_client, milvus_store
from app.database import SessionLocal
from app.models import (
    JobProfile,
    MatchBatch,
    ParsedResume,
    ParseTask,
    ResumeFile,
    ScreenBatch,
    ScreenResult,
    TalentProfile,
    TalentTag,
)
from app.schemas.match import MatchScoreOutput, ScoreDimension
from app.service import match_engine as engine
from app.service import screen_service
from app.tasks.screen_worker import handle_screen_batch

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 假件与造数
# --------------------------------------------------------------------------- #
def make_llm(skills=80, experience=70, education=75, others=60, reason="匹配良好"):
    return MatchScoreOutput(
        skills=ScoreDimension(score=skills, hits=["python"], misses=[]),
        experience=ScoreDimension(score=experience, hits=["订单系统"], misses=[]),
        education=ScoreDimension(score=education, hits=["本科"], misses=[]),
        others=ScoreDimension(score=others, hits=[], misses=[]),
        reason=reason,
    )


@pytest.fixture()
def fakes(monkeypatch):
    calls = {"embed_one": 0, "embed_query": 0, "get_vector": 0, "llm": 0}
    llm_config = {"skills": 80, "experience": 70, "education": 75, "others": 60}

    def fake_llm(prompt, schema, **kwargs):
        calls["llm"] += 1
        return make_llm(**llm_config)

    def fake_embed_one(text):
        calls["embed_one"] += 1
        return [0.001] * 1024

    def fake_embed_query(text):
        calls["embed_query"] += 1
        return [0.001] * 1024

    def fake_get_vector(profile_id):
        calls["get_vector"] += 1
        raise milvus_store.MilvusStoreError("Milvus 不可用（测试模拟）")

    monkeypatch.setattr(llm_client, "chat_json", fake_llm)
    monkeypatch.setattr(engine, "chat_json", fake_llm)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    monkeypatch.setattr(embedding_client, "embed_query", fake_embed_query)
    monkeypatch.setattr(milvus_store, "get_profile_vector", fake_get_vector)
    return SimpleNamespace(calls=calls, llm_config=llm_config)


@pytest.fixture()
def env():
    job_ids: list[int] = []
    profile_ids: list[int] = []
    parsed_ids: list[int] = []
    task_ids: list[int] = []
    file_ids: list[int] = []
    batch_ids: list[int] = []
    result_ids: list[int] = []

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
        name = fields.pop("name", f"候选人{uuid4().hex[:5]}")
        with SessionLocal() as db:
            row = TalentProfile(
                name=name,
                degree=fields.pop("degree", "本科"),
                work_years=fields.pop("work_years", Decimal("5.0")),
                skills_json=fields.pop("skills_json", ["python", "mysql", "redis"]),
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
            parsed = ParsedResume(
                resume_file_id=file_row.id,
                talent_profile_id=profile_id,
                structured_json={},
                raw_text="x",
            )
            db.add(parsed)
            db.commit()
            file_ids.append(file_row.id)
            task_ids.append(file_row.id)
            parsed_ids.append(parsed.id)
            return file_row.id

    yield SimpleNamespace(
        make_job=make_job,
        make_profile=make_profile,
        make_parsed=make_parsed,
        track_batch=lambda batch_id: batch_ids.append(batch_id),
        track_result=lambda result_id: result_ids.append(result_id),
    )

    with SessionLocal() as db:
        db.execute(sa_delete(ScreenResult).where(ScreenResult.id.in_(result_ids)))
        db.execute(sa_delete(ScreenResult).where(ScreenResult.screen_batch_id.in_(batch_ids)))
        db.execute(sa_delete(ScreenBatch).where(ScreenBatch.id.in_(batch_ids)))
        db.execute(sa_delete(ParsedResume).where(ParsedResume.id.in_(parsed_ids)))
        db.execute(sa_delete(ParseTask).where(ParseTask.resume_file_id.in_(task_ids)))
        db.execute(sa_delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
        db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
        db.execute(sa_delete(JobProfile).where(JobProfile.id.in_(job_ids)))
        db.commit()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def _batch_row(batch_id: int) -> ScreenBatch:
    with SessionLocal() as db:
        db.expire_all()
        return db.get(ScreenBatch, batch_id)


# --------------------------------------------------------------------------- #
# 创建批次
# --------------------------------------------------------------------------- #
def test_create_batch_snapshot_and_estimated(env, fakes, client, auth):
    # 用 resume_file_ids 范围做**完全隔离**（all 范围会圈进库中既有的真实画像）
    job_id = env.make_job()
    ok1 = env.make_profile()  # 达标
    ok2 = env.make_profile()
    bad = env.make_profile(degree="大专")  # 学历不符：A02-1 起仍计入 estimated，不再被预检排除
    f1 = env.make_parsed(ok1)
    f2 = env.make_parsed(ok2)
    f3 = env.make_parsed(bad)
    upload_batch = uuid4().hex[:26].upper()
    env.make_parsed(ok1, batch_id=upload_batch)

    resp = client.post(
        "/api/screen/batches",
        json={
            "job_profile_id": job_id,
            "scope": {"type": "resume_file_ids", "ids": sorted([f1, f2, f3])},
        },
        headers=auth,
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    env.track_batch(data["batch_id"])
    assert data["total"] == 3  # 三个画像都进快照
    assert data["status"] == "pending"
    assert data["estimated_llm_calls"] == 3  # A02-1：所有人都进 Prompt C，不按预检折减

    batch = _batch_row(data["batch_id"])
    assert batch.scope_json["resolved_ids"] == sorted([ok1, ok2, bad])
    assert batch.config_snapshot_json["title"] == "高级后端工程师"
    assert batch.config_snapshot_json["must_skills"] == ["python", "mysql"]

    # upload_batch scope：只有该批次的画像
    resp = client.post(
        "/api/screen/batches",
        json={"job_profile_id": job_id, "scope": {"type": "upload_batch", "upload_batch_id": upload_batch}},
        headers=auth,
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    env.track_batch(data["batch_id"])
    assert data["total"] == 1

    # all_unscreened：该岗位无任何结果记录前能圈到库中画像（≥ 我的 3 个，不精确断言——库中有真实遗留画像）
    resp = client.post(
        "/api/screen/batches",
        json={"job_profile_id": job_id, "scope": {"type": "all_unscreened"}},
        headers=auth,
    )
    assert resp.status_code == 202
    data = resp.json()["data"]
    env.track_batch(data["batch_id"])
    assert data["total"] >= 3

    # 空范围 400 / 岗位不存在 404
    empty_batch = uuid4().hex[:26].upper()
    resp = client.post(
        "/api/screen/batches",
        json={"job_profile_id": job_id, "scope": {"type": "upload_batch", "upload_batch_id": empty_batch}},
        headers=auth,
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/screen/batches", json={"job_profile_id": 999999, "scope": {"type": "all"}}, headers=auth
    )
    assert resp.status_code == 404


def test_worker_executes_snapshot_and_counts(env, fakes):
    """A02-1：硬条件不符者照样评分，不再 score NULL / rejected / hard_fail=1。"""
    job_id = env.make_job()
    p1 = env.make_profile()  # 达标 → LLM 80/70/75/60 → 73.25 → pending（60≤73.25<75）
    p2 = env.make_profile(degree="大专")  # 学历不符 → 仍进 Prompt C，不符项只落在 misses
    f1 = env.make_parsed(p1)
    f2 = env.make_parsed(p2)
    with SessionLocal() as db:
        batch = screen_service.create_batch(
            db, job_id, {"type": "resume_file_ids", "ids": [f1, f2]}, user_id=1
        )
        batch_id = batch["batch_id"]
        env.track_batch(batch_id)

    handle_screen_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done"
    assert batch.progress == 2
    assert batch.total == 2
    assert batch.passed == 0 and batch.pending_count == 2 and batch.rejected == 0
    assert batch.vector_degraded == 0  # 筛选恒 0
    assert fakes.calls["get_vector"] == 0 and fakes.calls["embed_one"] == 0  # 零向量调用
    assert fakes.calls["llm"] == 2  # A02-1：两名候选人都进 Prompt C

    with SessionLocal() as db:
        rows = {
            row.talent_profile_id: row
            for row in db.query(ScreenResult).filter(ScreenResult.screen_batch_id == batch_id).all()
        }
    assert rows[p1].verdict == "pending" and float(rows[p1].score) == 73.25
    assert rows[p1].hard_fail == 0
    assert rows[p2].verdict == "pending" and float(rows[p2].score) == 73.25
    assert rows[p2].hard_fail == 0 and rows[p2].hard_fail_reason is None
    assert any(
        "不符项" in m and "学历不满足" in m for m in (rows[p2].misses_json or [])
    )  # 不符项仍可见，但只是展示项


def test_snapshot_isolation_profile_change_and_soft_delete(env, fakes):
    """改岗位条件/删画像不影响已建批次：结果按快照；软删者跳过计 skipped_deleted。"""
    job_id = env.make_job()
    p1 = env.make_profile()
    p2 = env.make_profile()
    p3 = env.make_profile()
    f1 = env.make_parsed(p1)
    f2 = env.make_parsed(p2)
    f3 = env.make_parsed(p3)

    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, {"type": "resume_file_ids", "ids": [f1, f2, f3]}, user_id=1
        )
        batch_id = data["batch_id"]
        env.track_batch(batch_id)

    # 创建后：改岗位（min_degree→博士、version+1）+ 删除 p3 + 新增 p4
    with SessionLocal() as db:
        job = db.get(JobProfile, job_id)
        job.min_degree = "博士"
        job.version += 1
        profile_to_delete = db.get(TalentProfile, p3)
        profile_to_delete.deleted_at = profile_to_delete.updated_at
        db.commit()
    p4 = env.make_profile()  # 创建后的新画像 → 不进快照

    handle_screen_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.job_profile_version == 1  # 快照版本
    assert batch.total == 3  # 快照值不变（新画像不补进）
    assert batch.progress == 2  # p3 被跳过
    assert batch.status == "done"

    with SessionLocal() as db:
        scored = db.query(ScreenResult).filter(
            ScreenResult.screen_batch_id == batch_id,
            ScreenResult.talent_profile_id == p1,
        ).one()
    # 若按新条件（博士）p1 会被硬淘汰，但快照隔离 → 按旧条件评分
    assert scored.hard_fail == 0 and float(scored.score) == 73.25

    with SessionLocal() as db:
        detail = screen_service.get_batch_detail(db, batch_id)
    assert detail["skipped_deleted"] == 1
    assert detail["processed"] == 2
    assert detail["scope"]["resolved_ids_count"] == 3
    assert "resolved_ids" not in detail["scope"]  # 详情不回传全量清单
    assert detail["vector_degraded"] is False


def test_worker_resume_after_crash(env, fakes):
    """杀进程重启：running→pending 保留进度；worker 只补缺（结果行数不增不减）。"""
    job_id = env.make_job()
    p1 = env.make_profile()
    p2 = env.make_profile()
    f1 = env.make_parsed(p1)
    f2 = env.make_parsed(p2)
    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, {"type": "resume_file_ids", "ids": [f1, f2]}, user_id=1
        )
        batch_id = data["batch_id"]
        env.track_batch(batch_id)

    # 模拟崩溃前已处理 p1：手工写 p1 的结果行 + progress=1（快照语义）
    with SessionLocal() as db:
        screen_service.screen_dao.create_result(
            db,
            screen_batch_id=batch_id,
            job_profile_id=job_id,
            talent_profile_id=p1,
            hard_fail=0,
            score=Decimal("73.25"),
            verdict="pending",
            reason_summary="崩溃前已写",
        )
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "running"
        batch.progress = 1
        db.commit()

    # 重启恢复（runner 启动逻辑）+ 重跑 worker：只补 p2
    with SessionLocal() as db:
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "pending"  # runner 启动恢复：running→pending 保留 stage/progress
        db.commit()
    handle_screen_batch(batch_id)

    batch = _batch_row(batch_id)
    assert batch.status == "done"
    assert batch.progress == 2
    with SessionLocal() as db:
        row1 = db.query(ScreenResult).filter(
            ScreenResult.screen_batch_id == batch_id, ScreenResult.talent_profile_id == p1
        ).one()
    assert row1.reason_summary == "崩溃前已写"  # 已写成员不重跑（快照语义）


def test_retry_and_manual_verdict(env, fakes, client, auth):
    job_id = env.make_job()
    p1 = env.make_profile()
    f1 = env.make_parsed(p1)

    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, {"type": "resume_file_ids", "ids": [f1]}, user_id=1
        )
        batch_id = data["batch_id"]
        env.track_batch(batch_id)
    handle_screen_batch(batch_id)

    # 非 failed 不可重试
    with SessionLocal() as db:
        assert db.get(ScreenBatch, batch_id).status == "done"
    resp = client.post(f"/api/screen/batches/{batch_id}/retry", headers=auth)
    assert resp.status_code == 400

    # 人工改判 pending → passed；计数同步
    with SessionLocal() as db:
        result = db.query(ScreenResult).filter(ScreenResult.screen_batch_id == batch_id).one()
        result_id = result.id
    resp = client.patch(
        f"/api/screen/results/{result_id}", json={"manual_verdict": "passed"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["effective_verdict"] == "passed"
    assert body["manual_verdict_by"] is not None
    batch = _batch_row(batch_id)
    assert batch.passed == 1 and batch.pending_count == 0

    # 撤销改判
    resp = client.patch(f"/api/screen/results/{result_id}", json={"manual_verdict": None}, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["data"]["effective_verdict"] == "pending"
    batch = _batch_row(batch_id)
    assert batch.passed == 0 and batch.pending_count == 1

    # 强制 failed → retry：复位 + retry_count+1 + 结果行数不变 + 已写分数不变
    with SessionLocal() as db:
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "failed"
        batch.error = "测试注入失败"
        db.commit()
    resp = client.post(f"/api/screen/batches/{batch_id}/retry", headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["retry_count"] == 1 and data["status"] == "pending" and data["progress"] == 1
    handle_screen_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done" and batch.retry_count == 1
    with SessionLocal() as db:
        count = (
            db.query(ScreenResult)
            .filter(ScreenResult.screen_batch_id == batch_id)
            .count()
        )
        row1 = db.query(ScreenResult).filter(
            ScreenResult.screen_batch_id == batch_id, ScreenResult.talent_profile_id == p1
        ).one()
    assert count == 1  # 只补缺不重跑
    assert float(row1.score) == 73.25


def test_results_listing_and_filters(env, fakes, client, auth):
    # A02-1：学历不符者不再硬淘汰置底，仍出现在结果列表且有分数
    job_id = env.make_job(min_degree="博士")
    p1 = env.make_profile()
    f1 = env.make_parsed(p1)
    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, {"type": "resume_file_ids", "ids": [f1]}, user_id=1
        )
        batch_id = data["batch_id"]
        env.track_batch(batch_id)
    handle_screen_batch(batch_id)

    resp = client.get(f"/api/screen/batches/{batch_id}/results", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["total"] == 1
    item = body["items"][0]
    assert item["hard_fail"] is False and float(item["score"]) == 73.25
    assert item["hard_fail_reason"] is None
    assert item["effective_verdict"] == "pending"
    assert any("不符项" in m and "学历不满足" in m for m in item["misses"])

    # verdict 过滤（有效结论口径）；三档仍为内部参考标签，不自动改申请状态
    resp = client.get(
        f"/api/screen/batches/{batch_id}/results", params={"verdict": "passed"}, headers=auth
    )
    assert resp.json()["data"]["total"] == 0
    resp = client.get(
        f"/api/screen/batches/{batch_id}/results", params={"verdict": "pending"}, headers=auth
    )
    assert resp.json()["data"]["total"] == 1
    resp = client.get(
        f"/api/screen/batches/{batch_id}/results", params={"verdict": "bogus"}, headers=auth
    )
    assert resp.status_code == 400

    # 不存在的批次 404
    resp = client.get("/api/screen/batches/999999/results", headers=auth)
    assert resp.status_code == 404


def test_requires_token(env, client):
    assert client.post("/api/screen/batches", json={}).status_code == 401
    assert client.get("/api/screen/batches").status_code == 401
