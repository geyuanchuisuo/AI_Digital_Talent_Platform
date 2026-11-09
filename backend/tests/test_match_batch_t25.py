"""T-25 L2：匹配批次服务 + worker + API（真实 MySQL；LLM/Embedding/Milvus 假件）。

覆盖 todolist T-25 验收点：
1. POST 202（filter/upload_batch/all 范围 + 成员快照 + estimated_llm_calls）；
2. worker use_vector=True：岗位向量批次内只算一次（embed_one 恰 1 次）；逐人调 LLM；
3. 硬淘汰 score=0、hard_failed 汇总、名次置底；rank 查询时计算（offset+行号）；
4. **Milvus 停机 → 批次照常 done、similarity 全 null、vector_degraded=1、名次稳定**；
5. retry：结果行数与名次不变、retry_count 递增；
6. 结果详情（四维分 + vector_similarity + hits/misses + reason）；
7. Milvus 正常路径：相似度写入 dimension_scores_json 并参与 experience 合成。
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
from app.models import JobProfile, MatchBatch, MatchResult, ParsedResume, ParseTask, ResumeFile, TalentProfile, TalentTag
from app.schemas.match import MatchScoreOutput, ScoreDimension
from app.service import match_engine as engine
from app.service import match_service
from app.tasks.match_worker import handle_match_batch

pytestmark = pytest.mark.integration


def make_llm(skills=80, experience=70, education=75, others=60, reason="技能高度重合"):
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
    store = SimpleNamespace(
        down=False, embed_down=False, job_vector=[1.0] + [0.0] * 1023, profile_vector=[1.0] + [0.0] * 1023
    )

    def fake_llm(prompt, schema, **kwargs):
        calls["llm"] += 1
        return make_llm()

    def fake_embed_one(text):
        calls["embed_one"] += 1
        if store.embed_down:
            raise RuntimeError("Embedding 不可用（测试模拟）")
        return store.job_vector

    def fake_embed_query(text):
        calls["embed_query"] += 1
        return store.job_vector

    def fake_get_vector(profile_id):
        calls["get_vector"] += 1
        if store.down:
            raise milvus_store.MilvusStoreError("Milvus 不可用（测试模拟）")
        return store.profile_vector

    monkeypatch.setattr(llm_client, "chat_json", fake_llm)
    monkeypatch.setattr(engine, "chat_json", fake_llm)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    monkeypatch.setattr(embedding_client, "embed_query", fake_embed_query)
    monkeypatch.setattr(milvus_store, "get_profile_vector", fake_get_vector)
    return SimpleNamespace(calls=calls, store=store)


@pytest.fixture()
def env():
    job_ids: list[int] = []
    profile_ids: list[int] = []
    parsed_ids: list[int] = []
    task_ids: list[int] = []
    file_ids: list[int] = []
    batch_ids: list[int] = []
    result_ids: list[int] = []
    tag_ids: list[int] = []

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

    def make_profile(**fields) -> tuple[int, str]:
        name = fields.pop("name", f"候选人{uuid4().hex[:5]}")
        with SessionLocal() as db:
            row = TalentProfile(
                name=name,
                degree=fields.pop("degree", "本科"),
                work_years=fields.pop("work_years", Decimal("5.0")),
                skills_json=fields.pop("skills_json", ["python", "mysql", "redis"]),
                vector_status=fields.pop("vector_status", "synced"),
                source="resume",
                **fields,
            )
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id, name

    def make_tag(profile_id: int, tag: str) -> None:
        with SessionLocal() as db:
            row = TalentTag(talent_profile_id=profile_id, tag=tag, tag_type="skill", source="llm")
            db.add(row)
            db.commit()
            tag_ids.append(row.id)

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
            task_ids.append(file_row.id)
            parsed_ids.append(file_row.id)
            return file_row.id

    yield SimpleNamespace(
        make_job=make_job,
        make_profile=make_profile,
        make_tag=make_tag,
        make_parsed=make_parsed,
        track_batch=batch_ids.append,
        track_result=result_ids.append,
    )

    with SessionLocal() as db:
        db.execute(sa_delete(MatchResult).where(MatchResult.id.in_(result_ids)))
        db.execute(sa_delete(MatchResult).where(MatchResult.match_batch_id.in_(batch_ids)))
        db.execute(sa_delete(MatchBatch).where(MatchBatch.id.in_(batch_ids)))
        db.execute(sa_delete(ParsedResume).where(ParsedResume.resume_file_id.in_(parsed_ids)))
        db.execute(sa_delete(ParseTask).where(ParseTask.resume_file_id.in_(task_ids)))
        db.execute(sa_delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        db.execute(sa_delete(TalentTag).where(TalentTag.id.in_(tag_ids)))
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


def _batch_row(batch_id: int) -> MatchBatch:
    with SessionLocal() as db:
        db.expire_all()
        return db.get(MatchBatch, batch_id)


def test_create_filter_scope_and_worker(env, fakes, client, auth):
    """filter 范围（tags 精确圈定，唯一标签隔离）+ 正常向量路径：岗位向量 1 次、相似度 1.0 参与合成。"""
    job_id = env.make_job()
    unique_tag = f"专属标签{uuid4().hex[:8]}"  # 中文标签原样存储，天然唯一（真实遗留画像没有）
    p1, name1 = env.make_profile(work_years=Decimal("5.0"))  # 达标
    p2, _ = env.make_profile(work_years=Decimal("1.0"))  # 硬淘汰样本（不进圈定也不影响）
    env.make_parsed(p1)
    env.make_parsed(p2)
    env.make_tag(p1, unique_tag)
    env.make_tag(p1, "python")

    # 多标签 AND：p1 同时有两个标签，p2 只有 python → [unique_tag, python] 只剩 p1
    resp = client.post(
        "/api/match/batches",
        json={
            "job_profile_id": job_id,
            "scope": {"type": "filter", "tags": [unique_tag, "python"]},
        },
        headers=auth,
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    env.track_batch(data["batch_id"])
    assert data["total"] == 1  # AND 语义：两个标签都必须命中
    assert data["estimated_llm_calls"] == 1

    handle_match_batch(data["batch_id"])
    batch = _batch_row(data["batch_id"])
    assert batch.status == "done"
    assert batch.vector_degraded == 0
    assert batch.hard_failed == 0
    assert fakes.calls["embed_one"] == 1  # 岗位向量批次内只算一次
    assert fakes.calls["get_vector"] == 1
    assert fakes.calls["llm"] == 1

    with SessionLocal() as db:
        row = db.query(MatchResult).filter(MatchResult.match_batch_id == data["batch_id"]).one()
    # sim=1.0 → experience = 0.7×70 + 0.3×100 = 79 → score = 32 + 23.7 + 11.25 + 9 = 75.95
    assert float(row.score) == 75.95
    assert row.hard_fail == 0
    dims = row.dimension_scores_json
    assert dims["experience"] == 79.0 and dims["vector_similarity"] == pytest.approx(1.0)

    # 名次与详情
    resp = client.get(f"/api/match/batches/{data['batch_id']}/results", headers=auth)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 1 and items[0]["rank"] == 1 and items[0]["name"] == name1

    resp = client.get(f"/api/match/results/{row.id}", headers=auth)
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["rank"] == 1
    assert detail["dimension_scores"]["skills"] == 80
    assert detail["vector_similarity"] == pytest.approx(1.0)
    assert detail["reason"] == "技能高度重合"


def test_hard_fail_zero_and_ranking(env, fakes, client, auth):
    """硬淘汰 score=0 置底 + hard_failed 汇总 + rank 计算含 offset。"""
    job_id = env.make_job()
    p_low, name_low = env.make_profile(degree="大专")  # 硬淘汰
    p_mid, name_mid = env.make_profile(work_years=Decimal("4.0"))  # 通过，分数较低
    p_top, name_top = env.make_profile(work_years=Decimal("9.0"), skills_json=["python", "mysql", "redis", "milvus"])
    upload_batch = uuid4().hex[:26].upper()
    env.make_parsed(p_low, batch_id=upload_batch)
    env.make_parsed(p_mid, batch_id=upload_batch)
    env.make_parsed(p_top, batch_id=upload_batch)

    # upload_batch 范围 + 自造批次号 = 完全隔离（不圈库中真实遗留画像）
    with SessionLocal() as db:
        batch = match_service.create_batch(
            db, job_id, {"type": "upload_batch", "upload_batch_id": upload_batch}, user_id=1
        )
        batch_id = batch["batch_id"]
        env.track_batch(batch_id)

    handle_match_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done" and batch.hard_failed == 1

    resp = client.get(f"/api/match/batches/{batch_id}/results", headers=auth)
    items = resp.json()["data"]["items"]
    assert [item["rank"] for item in items] == [1, 2, 3]
    # p_top 与 p_mid 的 LLM 分相同、相似度都 1.0 → 并列 75.95，按 talent_profile_id ASC
    assert items[0]["name"] == name_mid and items[0]["score"] == 75.95
    assert items[1]["name"] == name_top and items[1]["score"] == 75.95
    assert items[2]["name"] == name_low and items[2]["score"] == 0.0  # 硬淘汰 score=0 置底
    assert items[2]["hard_fail"] is True and "学历" in items[2]["hard_fail_reason"]

    # offset 分页名次：第二页（page=2, size=2）的行号 = 3
    resp = client.get(
        f"/api/match/batches/{batch_id}/results", params={"page": 2, "page_size": 2}, headers=auth
    )
    assert resp.json()["data"]["items"][0]["rank"] == 3

    # 详情 404 / 存在
    resp = client.get("/api/match/results/999999", headers=auth)
    assert resp.status_code == 404


def test_milvus_down_degrades_and_rank_stable(env, fakes, client, auth):
    """Milvus 停机：批次 done、similarity 全 null、vector_degraded=1、名次稳定；retry 不变。"""
    job_id = env.make_job()
    p1, _ = env.make_profile()
    p2, _ = env.make_profile(work_years=Decimal("6.0"))
    upload_batch = uuid4().hex[:26].upper()
    env.make_parsed(p1, batch_id=upload_batch)
    env.make_parsed(p2, batch_id=upload_batch)
    with SessionLocal() as db:
        batch = match_service.create_batch(
            db, job_id, {"type": "upload_batch", "upload_batch_id": upload_batch}, user_id=1
        )
        batch_id = batch["batch_id"]
        env.track_batch(batch_id)

    fakes.store.down = True  # get_profile_vector 挂；embed_one 仍可用（岗位向量算得出）
    handle_match_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done"
    assert batch.vector_degraded == 1
    assert batch.hard_failed == 0

    with SessionLocal() as db:
        rows = (
            db.query(MatchResult)
            .filter(MatchResult.match_batch_id == batch_id)
            .order_by(MatchResult.talent_profile_id)
            .all()
        )
    for row in rows:
        assert row.dimension_scores_json["vector_similarity"] is None
        assert float(row.score) == 73.25  # 降级 → experience=llm（70）

    # retry（注入 failed）后名次与结果行数不变
    with SessionLocal() as db:
        b = db.get(MatchBatch, batch_id)
        b.status = "failed"
        b.error = "测试注入"
        db.commit()
    resp = client.post(f"/api/match/batches/{batch_id}/retry", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["retry_count"] == 1
    fakes.store.down = True
    handle_match_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done" and batch.retry_count == 1
    with SessionLocal() as db:
        assert db.query(MatchResult).filter(MatchResult.match_batch_id == batch_id).count() == 2


def test_embedding_down_full_degrade(env, fakes):
    """Embedding 挂（岗位向量算不出）→ 全批降级且不失败（不 503）。"""
    job_id = env.make_job()
    p1, _ = env.make_profile()
    upload_batch = uuid4().hex[:26].upper()
    env.make_parsed(p1, batch_id=upload_batch)
    with SessionLocal() as db:
        batch = match_service.create_batch(
            db, job_id, {"type": "upload_batch", "upload_batch_id": upload_batch}, user_id=1
        )
        batch_id = batch["batch_id"]
        env.track_batch(batch_id)

    fakes.store.embed_down = True
    handle_match_batch(batch_id)
    batch = _batch_row(batch_id)
    assert batch.status == "done"
    assert batch.vector_degraded == 1
    with SessionLocal() as db:
        row = db.query(MatchResult).filter(MatchResult.match_batch_id == batch_id).one()
    assert row.dimension_scores_json["vector_similarity"] is None
    assert float(row.score) == 73.25


def test_requires_token(env, client):
    assert client.post("/api/match/batches", json={}).status_code == 401
    assert client.get("/api/match/batches").status_code == 401


def test_manual_verdict_set_revoke_invalid_and_auth(env, fakes, client, auth):
    """T-A41：人工推进标记——设置/撤销/非法值 400/404/无 token 401（对齐 F1-10）。"""
    job_id = env.make_job()
    p1, _ = env.make_profile(work_years=Decimal("5.0"))
    env.make_parsed(p1)
    resp = client.post(
        "/api/match/batches",
        json={"job_profile_id": job_id, "scope": {"type": "all"}},
        headers=auth,
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["data"]["batch_id"]
    env.track_batch(batch_id)
    handle_match_batch(batch_id)

    with SessionLocal() as db:
        row = db.query(MatchResult).filter(
            MatchResult.match_batch_id == batch_id,
            MatchResult.talent_profile_id == p1,
        ).one()
    result_id = row.id

    # 无 token → 401（统一错误体，路由挂 CurrentUser）
    assert client.patch(f"/api/match/results/{result_id}", json={"manual_verdict": "passed"}).status_code == 401

    # 非法值 → 422（Pydantic Literal 由 T-02 处理器归一为统一错误体；screen 同口径）
    resp = client.patch(
        f"/api/match/results/{result_id}", json={"manual_verdict": "hire"}, headers=auth
    )
    assert resp.status_code == 422, resp.text

    # 404
    resp = client.patch("/api/match/results/999999", json={"manual_verdict": "passed"}, headers=auth)
    assert resp.status_code == 404

    # 设置 → 详情可见 + 落库字段
    resp = client.patch(
        f"/api/match/results/{result_id}", json={"manual_verdict": "passed"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["manual_verdict"] == "passed"
    assert detail["manual_verdict_by"] is not None and detail["manual_verdict_at"]

    with SessionLocal() as db:
        row = db.get(MatchResult, result_id)
        assert row.manual_verdict == "passed" and row.manual_verdict_by is not None
        assert row.score is not None  # 改判不改 score（对齐 screen 口径）

    # 撤销 → 三字段全空
    resp = client.patch(
        f"/api/match/results/{result_id}", json={"manual_verdict": None}, headers=auth
    )
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["manual_verdict"] is None
    assert detail["manual_verdict_by"] is None and detail["manual_verdict_at"] is None
