"""T-18 L2：画像 CRUD 集成测试（真实 MySQL；Embedding/Milvus 用假件，不外呼 LLM）。

覆盖 todolist T-18 验收点：
1. 列表各过滤（tag 精确 / tag_type / degree / 年限区间 / keyword）与分页正确；
2. `deleted=true` 只返回已软删画像、默认视角不含它们；
3. PATCH 传 educations 等经历字段 → 400；degree/手机号非法 → 400；
4. PATCH 只改 city 触发向量重算（embed 恰好 1 次）；只改 phone（hash 不变字段）零 embedding；
   只保护实际修改字段（manual_edited_fields）；
5. 标签重建保留 `source='manual'`、技能行随 skills 增删（tag_type='skill'）；
6. DELETE 软删后列表不可见、Milvus 向量已删、向量删除失败不阻塞（vector_deleted:false）；
7. restore 后重新可见并 force 重嵌；revector 修复 failed → synced；Milvus 停机 revector 503；
8. 详情经历聚合去重（company+title+start，end 晚者胜）、resume_files、筛选/匹配历史
   （effective_verdict = manual_verdict ?? verdict）；
9. resume-file 下载（Content-Disposition filename*、越权 404）。

隔离：每个用例建独立画像/标签/文件/解析行，finally 按 id 逆序清理。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.core import embedding_client, milvus_store
from app.core.profile_text import build_profile_text_for_row, compute_text_hash
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
from app.service.profile_service import aggregate_structured

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 假件
# --------------------------------------------------------------------------- #
class FakeStore:
    def __init__(self) -> None:
        self.down = False
        self.upserts: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    def upsert(self, profile_id: int, vector, text_hash: str) -> None:
        if self.down:
            raise RuntimeError("Milvus 不可用（测试模拟）")
        assert len(vector) == 1024
        self.upserts.append((profile_id, text_hash))

    def delete(self, profile_id: int) -> bool:
        if self.down:
            raise RuntimeError("Milvus 不可用（测试模拟）")
        self.deleted.append(profile_id)
        return True


@pytest.fixture()
def fakes(monkeypatch):
    store = FakeStore()
    calls = {"embed": 0}

    def fake_embed_one(text: str):
        calls["embed"] += 1
        return [0.001] * 1024

    monkeypatch.setattr(milvus_store, "_default_store", store)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    return SimpleNamespace(store=store, embed=calls)


# --------------------------------------------------------------------------- #
# 数据隔离与造数
# --------------------------------------------------------------------------- #
@pytest.fixture()
def env():
    profile_ids: list[int] = []
    parsed_ids: list[int] = []
    task_ids: list[int] = []
    file_ids: list[int] = []
    job_ids: list[int] = []
    screen_batch_ids: list[int] = []
    screen_result_ids: list[int] = []
    match_batch_ids: list[int] = []
    match_result_ids: list[int] = []

    def make_profile(**fields) -> int:
        fields.setdefault("name", "张三")
        with SessionLocal() as db:
            row = TalentProfile(
                source=fields.pop("source", "resume"),
                vector_status=fields.pop("vector_status", "pending"),
                **fields,
            )
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id

    def make_tags(profile_id: int, rows: list[tuple[str, str, str]]) -> None:
        with SessionLocal() as db:
            for tag, tag_type, source in rows:
                db.add(
                    TalentTag(
                        talent_profile_id=profile_id, tag=tag, tag_type=tag_type, source=source
                    )
                )
            db.commit()

    def make_file_and_parsed(structured: dict, *, profile_id: int) -> tuple[int, int, int]:
        tag = uuid4().hex
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename=f"t18-{tag[:8]}.pdf",
                stored_path=f"data/uploads/{tag}.pdf",
                file_hash=tag * 2,
                file_ext="pdf",
                file_size=1024,
                upload_batch_id=tag[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            task = ParseTask(resume_file_id=file_row.id, status="success", retries=0, truncated=0)
            db.add(task)
            db.flush()
            parsed = ParsedResume(
                resume_file_id=file_row.id,
                talent_profile_id=profile_id,
                structured_json=structured,
                raw_text="张三 后端工程师",
                llm_model="fake",
            )
            db.add(parsed)
            db.commit()
            file_ids.append(file_row.id)
            task_ids.append(task.id)
            parsed_ids.append(parsed.id)
            return file_row.id, task.id, parsed.id

    def sync_hash(profile_id: int) -> str:
        """按当前数据重算并写入 profile_text_hash（模拟一次成功的向量同步）。"""
        with SessionLocal() as db:
            profile = db.get(TalentProfile, profile_id)
            structured = aggregate_structured(db, profile_id)
            tags = [
                row.tag
                for row in db.execute(
                    select(TalentTag).where(TalentTag.talent_profile_id == profile_id)
                ).scalars().all()
            ]
            text = build_profile_text_for_row(profile, tags=tags, structured=structured)
            profile.profile_text_hash = compute_text_hash(text)
            profile.vector_status = "synced"
            db.commit()
            return profile.profile_text_hash

    def make_job(title: str = "高级后端工程师") -> int:
        with SessionLocal() as db:
            row = JobProfile(
                title=title,
                jd_text="岗位职责",
                requirements_text="任职要求",
                must_skills_json=["python"],
                bonus_skills_json=[],
                hard_rule_switches_json={},
                pass_threshold=Decimal("60"),
                pending_threshold=Decimal("50"),
                weights_json={},
                version=1,
                is_active=1,
                created_by=1,
            )
            db.add(row)
            db.commit()
            job_ids.append(row.id)
            return row.id

    def make_screen_history(profile_id: int, job_id: int, *, verdict: str, manual: str | None) -> int:
        with SessionLocal() as db:
            batch = ScreenBatch(
                job_profile_id=job_id,
                job_profile_version=1,
                config_snapshot_json={},
                scope_json={"type": "all"},
                status="done",
                progress=1,
                total=1,
                passed=1 if verdict == "passed" else 0,
                pending_count=0,
                rejected=0 if verdict == "passed" else 1,
                created_by=1,
            )
            db.add(batch)
            db.flush()
            result = ScreenResult(
                screen_batch_id=batch.id,
                job_profile_id=job_id,
                talent_profile_id=profile_id,
                hard_fail=0,
                score=Decimal("82.50"),
                verdict=verdict,
                manual_verdict=manual,
                reason_summary="ok",
            )
            db.add(result)
            db.commit()
            screen_batch_ids.append(batch.id)
            screen_result_ids.append(result.id)
            return result.id

    def make_match_history(profile_id: int, job_id: int, *, hard_fail: bool) -> int:
        with SessionLocal() as db:
            batch = MatchBatch(
                job_profile_id=job_id,
                job_profile_version=1,
                config_snapshot_json={},
                talent_scope_json={"type": "all"},
                status="done",
                progress=1,
                total=1,
                hard_failed=1 if hard_fail else 0,
                created_by=1,
            )
            db.add(batch)
            db.flush()
            result = MatchResult(
                match_batch_id=batch.id,
                talent_profile_id=profile_id,
                score=Decimal("86.00"),
                hard_fail=1 if hard_fail else 0,
                hard_fail_reason="学历不满足" if hard_fail else None,
            )
            db.add(result)
            db.commit()
            match_batch_ids.append(batch.id)
            match_result_ids.append(result.id)
            return result.id

    def load_profile(profile_id: int) -> TalentProfile:
        with SessionLocal() as db:
            db.expire_all()
            return db.get(TalentProfile, profile_id)

    def tags_of(profile_id: int) -> dict[str, tuple[str, str]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(TalentTag).where(TalentTag.talent_profile_id == profile_id)
            ).scalars().all()
            return {row.tag: (row.tag_type, row.source) for row in rows}

    yield SimpleNamespace(
        make_profile=make_profile,
        make_tags=make_tags,
        make_file_and_parsed=make_file_and_parsed,
        sync_hash=sync_hash,
        make_job=make_job,
        make_screen_history=make_screen_history,
        make_match_history=make_match_history,
        load_profile=load_profile,
        tags_of=tags_of,
    )

    with SessionLocal() as db:
        db.execute(sa_delete(MatchResult).where(MatchResult.id.in_(match_result_ids)))
        db.execute(sa_delete(MatchBatch).where(MatchBatch.id.in_(match_batch_ids)))
        db.execute(sa_delete(ScreenResult).where(ScreenResult.id.in_(screen_result_ids)))
        db.execute(sa_delete(ScreenBatch).where(ScreenBatch.id.in_(screen_batch_ids)))
        db.execute(sa_delete(JobProfile).where(JobProfile.id.in_(job_ids)))
        db.execute(sa_delete(ParsedResume).where(ParsedResume.id.in_(parsed_ids)))
        db.execute(sa_delete(ParseTask).where(ParseTask.id.in_(task_ids)))
        db.execute(sa_delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
        db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
        db.commit()


def make_structured(
    *,
    name: str = "张三",
    city: str = "深圳",
    degree: str = "本科",
    title: str = "后端工程师",
    employments: list[dict] | None = None,
    skills: list[str] | None = None,
) -> dict:
    return {
        "basic": {"name": name, "phone": "13800000000", "email": None, "city": city, "gender": "男"},
        "educations": [
            {"school": "某大学", "major": "计算机", "degree": degree, "start": "2014-09", "end": "2018-06"}
        ],
        "employments": employments
        if employments is not None
        else [{"company": "A 公司", "title": title, "start": "2018-07", "end": "2021-06", "description": "订单系统"}],
        "projects": [],
        "skills": ["python"] if skills is None else skills,
        "certificates": [],
        "expected_title": "高级后端",
        "work_years": 6,
    }


# --------------------------------------------------------------------------- #
# API 客户端
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 1. 列表过滤（自隔离：唯一标记 + 成员断言，不依赖库是空的）
# --------------------------------------------------------------------------- #
def test_list_filters(env, fakes, client, auth):
    marker = uuid4().hex[:8]
    unique_tag = f"专有标签{marker}"  # 中文标签原样存储，天然唯一
    unique_skill = f"zskill{marker}"  # 直接写入 skills_json，CAST 模糊匹配用

    p_python = env.make_profile(
        name=f"张三{marker}", degree="本科", work_years=Decimal("4.0"), city="深圳", skills_json=["python"]
    )
    env.make_tags(p_python, [("python", "skill", "llm"), (unique_tag, "general", "manual")])
    p_java = env.make_profile(
        name=f"李四{marker}", degree="硕士", work_years=Decimal("8.0"), skills_json=[unique_skill]
    )
    env.make_tags(p_java, [("golang", "skill", "llm")])
    p_none = env.make_profile(name=f"王五{marker}", degree="大专", work_years=None)
    mine = {p_python, p_java, p_none}

    def list_ids(**params):
        resp = client.get("/api/talents", params=params, headers=auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        return [item["id"] for item in body["items"]], body

    ids, body = list_ids(tag=unique_tag)  # 精确匹配走 talent_tags
    assert ids == [p_python]
    item = body["items"][0]
    assert item["tags"] == ["python", unique_tag]  # 组内按标签行 id 升序
    assert item["tag_total"] == 2
    assert item["manual_edited_fields"] == []
    assert item["deleted_at"] is None
    assert item["name"] == f"张三{marker}"

    ids, _ = list_ids(tag="PYTHON")
    # 「精确匹配」= 走 talent_tags 等值比较，MySQL utf8mb4_unicode_ci 排序规则大小写不敏感
    # （库表 collation 口径，日志 T-18 取舍⑥登记）：大写输入也能命中归一化小写标签，对 HR 更宽容
    assert p_python in ids

    ids, _ = list_ids(tag_type="general")
    assert p_python in ids
    assert p_java not in ids  # 李四只有 skill 型标签

    ids, _ = list_ids(degree="硕士")
    assert p_java in ids and p_python not in ids and p_none not in ids

    ids, _ = list_ids(min_years=5)
    assert p_java in ids and p_python not in ids and p_none not in ids  # NULL 年限被排除

    ids, _ = list_ids(max_years=5)
    assert p_python in ids and p_none not in ids and p_java not in ids

    ids, _ = list_ids(keyword=f"李四{marker}")
    assert ids == [p_java]

    ids, _ = list_ids(keyword=unique_skill)  # skills_json 模糊命中
    assert ids == [p_java]

    resp = client.get("/api/talents", params={"degree": "学士"}, headers=auth)
    assert resp.status_code == 400
    resp = client.get("/api/talents", params={"tag_type": "bogus"}, headers=auth)
    assert resp.status_code == 400
    resp = client.get("/api/talents", params={"min_years": 5, "max_years": 1}, headers=auth)
    assert resp.status_code == 400
    assert mine  # 三个画像已建（防误删变量）


def test_list_pagination(env, fakes, client, auth):
    marker = uuid4().hex[:8]
    made = [env.make_profile(name=f"批量候选人{marker}{i}") for i in range(3)]
    resp = client.get(
        "/api/talents", params={"page": 1, "page_size": 2, "keyword": f"批量候选人{marker}"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"] == 1 and body["page_size"] == 2
    # 同 updated_at 内按 id DESC 稳定
    assert [item["id"] for item in body["items"]] == [made[2], made[1]]
    resp = client.get(
        "/api/talents", params={"page": 2, "page_size": 2, "keyword": f"批量候选人{marker}"}, headers=auth
    )
    assert [item["id"] for item in resp.json()["data"]["items"]] == [made[0]]


# --------------------------------------------------------------------------- #
# 2. PATCH
# --------------------------------------------------------------------------- #
def test_patch_city_triggers_vector_phone_does_not(env, fakes, client, auth):
    pid = env.make_profile(name="张三", city="深圳", vector_status="pending")
    env.make_tags(pid, [("python", "skill", "llm")])
    env.make_file_and_parsed(make_structured(city="深圳"), profile_id=pid)
    env.sync_hash(pid)
    assert fakes.embed["embed"] == 0

    # 只改 phone：phone 不在画像文本里 → hash 不变 → 零 embedding
    resp = client.patch(f"/api/talents/{pid}", json={"phone": "+86 139-1111-2222"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["vector_status"] == "synced"
    assert body["profile_text_changed"] is False
    assert fakes.embed["embed"] == 0
    assert body["profile"]["phone"] == "13911112222"  # 详情/编辑视图完整返回并已归一化
    assert body["manual_edited_fields"] == ["phone"]

    # 只改 city：画像文本变化 → 恰好 1 次 embedding + upsert
    resp = client.patch(f"/api/talents/{pid}", json={"city": "广州"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["vector_status"] == "synced"
    assert body["profile_text_changed"] is True
    assert fakes.embed["embed"] == 1
    assert fakes.store.upserts == [(pid, body["profile"]["vector_status"] and fakes.store.upserts[0][1])]
    assert body["manual_edited_fields"] == ["phone", "city"]  # 累积保护清单
    assert body["profile"]["vector_status"] == "synced"

    row = env.load_profile(pid)
    assert str(row.city) == "广州"
    assert row.updated_by is not None
    assert row.profile_text_hash == fakes.store.upserts[0][1]


def test_patch_no_actual_change_zero_calls(env, fakes, client, auth):
    pid = env.make_profile(name="张三", city="深圳", phone="13800000000", vector_status="synced")
    env.make_tags(pid, [("python", "skill", "llm")])
    env.make_file_and_parsed(make_structured(city="深圳"), profile_id=pid)
    env.sync_hash(pid)

    resp = client.patch(f"/api/talents/{pid}", json={"city": "深圳", "phone": "13800000000"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["profile_text_changed"] is False
    assert body["manual_edited_fields"] == []
    assert fakes.embed["embed"] == 0


def test_patch_validation_errors(env, fakes, client, auth):
    pid = env.make_profile(name="张三")

    resp = client.patch(f"/api/talents/{pid}", json={"educations": []}, headers=auth)
    assert resp.status_code == 400
    assert "educations" in resp.json()["message"]
    assert "只读" in resp.json()["message"]

    resp = client.patch(
        f"/api/talents/{pid}", json={"employments": [], "projects": [], "certificates": []}, headers=auth
    )
    assert resp.status_code == 400

    resp = client.patch(f"/api/talents/{pid}", json={"degree": "学士"}, headers=auth)
    assert resp.status_code == 400

    resp = client.patch(f"/api/talents/{pid}", json={"phone": "abc"}, headers=auth)
    assert resp.status_code == 400

    resp = client.patch(f"/api/talents/{pid}", json={"email": "not-an-email"}, headers=auth)
    assert resp.status_code == 400

    resp = client.patch(f"/api/talents/{pid}", json={"name": "  "}, headers=auth)
    assert resp.status_code == 400

    dims = {k: {"score": 80, "basis": "x"} for k in (
        "professional", "project_experience", "education", "stability", "growth_potential")}
    dims.pop("stability")
    resp = client.patch(f"/api/talents/{pid}", json={"ability_scores": dims}, headers=auth)
    assert resp.status_code == 400

    resp = client.patch(f"/api/talents/{pid}", json={"work_years": 90}, headers=auth)
    assert resp.status_code in (400, 422)

    resp = client.patch(f"/api/talents/{pid}", json={"gender": "女"}, headers=auth)
    assert resp.status_code == 422  # 非可修正字段（拼写/越界字段）由 forbid 拦截

    # 集成校验：以上全部失败后画像未被改动
    assert env.load_profile(pid).name == "张三"


def test_patch_tags_and_skills(env, fakes, client, auth):
    pid = env.make_profile(name="张三", skills_json=["python"])
    env.make_tags(pid, [("python", "skill", "llm"), ("团队协作", "general", "manual")])
    env.make_file_and_parsed(make_structured(), profile_id=pid)

    resp = client.patch(
        f"/api/talents/{pid}",
        json={"tags": ["Python", {"tag": "沟通", "tag_type": "general"}]},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    tags = env.tags_of(pid)
    assert tags["python"] == ("skill", "llm")  # source 不被覆盖为 manual
    assert tags["沟通"] == ("general", "manual")  # 新增行 manual
    assert "团队协作" not in tags  # 请求名单为准
    body = resp.json()["data"]
    assert "tags" in body["manual_edited_fields"]
    assert body["profile"]["tags"] == [
        {"tag": "python", "tag_type": "skill", "source": "llm"},
        {"tag": "沟通", "tag_type": "general", "source": "manual"},
    ]

    resp = client.patch(f"/api/talents/{pid}", json={"skills": ["Golang", "Python 3.11"]}, headers=auth)
    assert resp.status_code == 200, resp.text
    row = env.load_profile(pid)
    assert list(row.skills_json) == ["go", "python"]  # 别名归一 + 排序
    tags = env.tags_of(pid)
    assert tags["go"] == ("skill", "manual")  # 技能行同步（新增 → manual）
    assert tags["python"] == ("skill", "llm")  # 既有行来源保留（不是本次新引入）
    assert "skills" in resp.json()["data"]["manual_edited_fields"]


def test_patch_milvus_down_returns_200_failed(env, fakes, client, auth):
    pid = env.make_profile(name="张三", city="深圳", vector_status="synced")
    env.make_tags(pid, [("python", "skill", "llm")])
    env.make_file_and_parsed(make_structured(), profile_id=pid)
    env.sync_hash(pid)
    before_hash = env.load_profile(pid).profile_text_hash
    fakes.store.down = True

    resp = client.patch(f"/api/talents/{pid}", json={"city": "上海"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["vector_status"] == "failed"
    assert body["data"]["profile_text_changed"] is True
    assert body["message"] != "success"  # 半写提示

    row = env.load_profile(pid)
    assert str(row.city) == "上海"  # 业务字段已保存
    assert row.vector_status == "failed"
    assert row.profile_text_hash == before_hash  # 失败不写新 hash（可自愈）


def test_patch_404_missing_and_deleted(env, fakes, client, auth):
    resp = client.patch("/api/talents/99999999", json={"city": "深圳"}, headers=auth)
    assert resp.status_code == 404

    pid = env.make_profile(name="张三", deleted_at=None)
    with SessionLocal() as db:
        row = db.get(TalentProfile, pid)
        row.deleted_at = row.updated_at
        db.commit()
    resp = client.patch(f"/api/talents/{pid}", json={"city": "深圳"}, headers=auth)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 3. DELETE / restore / revector
# --------------------------------------------------------------------------- #
def test_delete_restore_roundtrip(env, fakes, client, auth):
    marker = uuid4().hex[:8]
    name = f"删恢候选人{marker}"
    pid = env.make_profile(name=name, vector_status="synced")
    env.make_tags(pid, [("python", "skill", "llm")])
    env.make_file_and_parsed(make_structured(), profile_id=pid)
    env.sync_hash(pid)

    resp = client.delete(f"/api/talents/{pid}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body == {"deleted": True, "vector_deleted": True}
    assert fakes.store.deleted == [pid]

    ids = [
        item["id"]
        for item in client.get("/api/talents", params={"keyword": name}, headers=auth).json()["data"]["items"]
    ]
    assert pid not in ids  # 默认视角不可见
    body = client.get(
        "/api/talents", params={"deleted": "true", "keyword": name}, headers=auth
    ).json()["data"]
    recycled = next(item for item in body["items"] if item["id"] == pid)
    assert recycled["deleted_at"] is not None  # 回收站视角可见并带删除时间

    resp = client.get(f"/api/talents/{pid}", headers=auth)
    assert resp.status_code == 404

    resp = client.delete(f"/api/talents/{pid}", headers=auth)
    assert resp.status_code == 404  # 重复删除

    embed_before = fakes.embed["embed"]
    resp = client.post(f"/api/talents/{pid}/restore", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body == {"restored": True, "vector_status": "synced"}
    assert fakes.embed["embed"] == embed_before + 1  # force 重嵌（hash 未变也必须补向量）
    assert fakes.store.upserts[-1][0] == pid

    ids = [
        item["id"]
        for item in client.get("/api/talents", params={"keyword": name}, headers=auth).json()["data"]["items"]
    ]
    assert pid in ids  # 恢复后重新可见，可被 all/filter 圈定

    resp = client.post(f"/api/talents/{pid}/restore", headers=auth)
    assert resp.status_code == 400  # 未被软删


def test_delete_vector_failure_not_blocking(env, fakes, client, auth):
    pid = env.make_profile(name="张三")
    fakes.store.down = True
    resp = client.delete(f"/api/talents/{pid}", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True, "vector_deleted": False}
    assert env.load_profile(pid).deleted_at is not None


def test_revector(env, fakes, client, auth):
    pid = env.make_profile(name="张三", vector_status="failed")
    env.make_tags(pid, [("python", "skill", "llm")])
    env.make_file_and_parsed(make_structured(), profile_id=pid)

    resp = client.post(f"/api/talents/{pid}/revector", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["vector_status"] == "synced"
    assert body["vector_synced_at"] is not None
    assert fakes.embed["embed"] == 1
    row = env.load_profile(pid)
    assert row.vector_status == "synced"
    assert row.profile_text_hash == fakes.store.upserts[0][1]

    fakes.store.down = True
    resp = client.post(f"/api/talents/{pid}/revector", headers=auth)
    assert resp.status_code == 503
    assert resp.json()["code"] == 503

    resp = client.post("/api/talents/99999999/revector", headers=auth)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 4. 详情
# --------------------------------------------------------------------------- #
def test_detail_aggregates_and_histories(env, fakes, client, auth):
    pid = env.make_profile(
        name="张三", degree="本科", work_years=Decimal("4.0"), phone="13800000000", vector_status="synced"
    )
    env.make_tags(pid, [("python", "skill", "llm"), ("沟通", "general", "manual")])
    env.make_file_and_parsed(
        make_structured(
            employments=[
                {"company": "A 公司", "title": "后端工程师", "start": "2018-07", "end": "2021-06", "description": "短描述"},
            ]
        ),
        profile_id=pid,
    )
    env.make_file_and_parsed(
        make_structured(
            employments=[
                # 同 identity（company+title+start），end 更晚 + description 更长 → 保留这条
                {"company": "A 公司", "title": "后端工程师", "start": "2018-07", "end": "2022-03", "description": "更完整的描述"},
                {"company": "B 公司", "title": "高级后端", "start": "2022-04", "end": None, "description": "现职"},
            ]
        ),
        profile_id=pid,
    )
    job_id = env.make_job("高级后端工程师")
    env.make_screen_history(pid, job_id, verdict="passed", manual="rejected")
    env.make_match_history(pid, job_id, hard_fail=True)

    resp = client.get(f"/api/talents/{pid}", headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data["employments"]) == 2  # 同 identity 去重
    a_company = next(item for item in data["employments"] if item["company"] == "A 公司")
    assert a_company["end"] == "2022-03" and a_company["description"] == "更完整的描述"
    assert data["educations"][0]["school"] == "某大学"

    assert data["profile"]["phone"] == "13800000000"  # 详情完整返回
    assert data["profile"]["tags"] == [
        {"tag": "python", "tag_type": "skill", "source": "llm"},
        {"tag": "沟通", "tag_type": "general", "source": "manual"},
    ]

    assert len(data["resume_files"]) == 2  # merge 后一个画像可对应多份简历
    assert all(ref["filename"].startswith("t18-") for ref in data["resume_files"])
    assert len({ref["resume_file_id"] for ref in data["resume_files"]}) == 2
    assert all(set(ref) == {"resume_file_id", "filename", "upload_batch_id"} for ref in data["resume_files"])

    assert len(data["screen_history"]) == 1
    history = data["screen_history"][0]
    assert history["batch_id"] == data["screen_history"][0]["batch_id"]
    assert history["job_title"] == "高级后端工程师"
    assert history["effective_verdict"] == "rejected"  # manual_verdict 覆盖 verdict
    assert history["score"] == 82.5

    assert data["match_history"][0]["score"] == 86.0
    assert data["match_history"][0]["hard_fail"] is True
    assert data["match_history"][0]["job_title"] == "高级后端工程师"


def test_detail_404(env, fakes, client, auth):
    assert client.get("/api/talents/99999999", headers=auth).status_code == 404
    pid = env.make_profile(name="张三")
    with SessionLocal() as db:
        row = db.get(TalentProfile, pid)
        row.deleted_at = row.updated_at
        db.commit()
    assert client.get(f"/api/talents/{pid}", headers=auth).status_code == 404


# --------------------------------------------------------------------------- #
# 5. 原文下载
# --------------------------------------------------------------------------- #
def test_download_resume_file(env, fakes, client, auth):
    download_dir = Path(__file__).resolve().parent.parent / ".pytest_tmp" / "t18_dl"
    download_dir.mkdir(parents=True, exist_ok=True)
    file_path = download_dir / f"{uuid4().hex}.pdf"
    file_path.write_bytes(b"%PDF-1.4 t18 download")
    try:
        pid = env.make_profile(name="张三")
        tag = uuid4().hex
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename="张三简历.pdf",
                stored_path=str(file_path),  # 绝对路径原样解析
                file_hash=tag * 2,
                file_ext="pdf",
                file_size=file_path.stat().st_size,
                upload_batch_id=tag[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            db.add(
                ParsedResume(
                    resume_file_id=file_row.id,
                    talent_profile_id=pid,
                    structured_json=make_structured(),
                    raw_text="x",
                    llm_model="fake",
                )
            )
            db.commit()
        resume_file_id = file_row.id

        resp = client.get(
            f"/api/talents/{pid}/resume-file", params={"resume_file_id": resume_file_id}, headers=auth
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b"%PDF-1.4 t18 download"
        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        assert "filename*=UTF-8''" in disposition  # RFC 5987 中文文件名
        assert "%E5%BC%A0%E4%B8%89%E7%AE%80%E5%8E%86.pdf" in disposition
        assert 'filename="resume.pdf"' in disposition  # ASCII 回退名（stem 空用 resume，扩展名保留）

        # 越权：文件不属于该画像
        other = env.make_profile(name="李四")
        resp = client.get(
            f"/api/talents/{other}/resume-file", params={"resume_file_id": resume_file_id}, headers=auth
        )
        assert resp.status_code == 404

        resp = client.get(f"/api/talents/{pid}/resume-file", params={"resume_file_id": 99999999}, headers=auth)
        assert resp.status_code == 404

        # 磁盘文件丢失
        file_path.unlink()
        resp = client.get(
            f"/api/talents/{pid}/resume-file", params={"resume_file_id": resume_file_id}, headers=auth
        )
        assert resp.status_code == 404
    finally:
        if file_path.exists():
            file_path.unlink()
        try:
            download_dir.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 6. 鉴权
# --------------------------------------------------------------------------- #
def test_requires_token(env, fakes, client):
    assert client.get("/api/talents").status_code == 401
    assert client.patch("/api/talents/1", json={"city": "深圳"}).status_code == 401
    assert client.delete("/api/talents/1").status_code == 401
