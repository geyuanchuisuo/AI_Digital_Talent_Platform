"""T-17 L2：身份查重与 merge / keep 集成测试（真实 MySQL；LLM/Embedding/Milvus 用假件）。

覆盖 todolist T-17 四个验收点：
1. 同一人换手机号格式（`138xxxx` 与 `+86 138xxxx`）重传触发判重；
2. merge 后单画像、解析结果指向 master、`ability_scores` 为 master 原值、向量已重算、任务 success；
3. keep 后两画像独立且后台可补齐标签/向量；
4. 重复调用 resolve 400、master 传非 profile_a 400；
5. merge 期间 Milvus 停机仍返回 200 且数据完整、`vector_status=failed`。

隔离：每个用例建独立的 res/parsed/画像行，结束按 id 逆序删除（含 merge/keep 新建的画像）。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core import embedding_client, llm_client, milvus_store
from app.core.errors import BizError
from app.database import SessionLocal
from app.models import (
    ParsedResume,
    ParseTask,
    ResumeFile,
    TalentDuplicate,
    TalentProfile,
    TalentTag,
)
from app.schemas.profile import AbilityDim, AbilityScores, ProfileLLMOutput, TagItem
from app.service import dedup_service as svc
from app.service import profile_service

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 假件
# --------------------------------------------------------------------------- #
class FakeStore:
    def __init__(self) -> None:
        self.down = False
        self.upserts: list[tuple[int, str]] = []

    def upsert(self, profile_id: int, vector, text_hash: str) -> None:
        if self.down:
            raise RuntimeError("Milvus 不可用（测试模拟）")
        assert len(vector) == 1024
        self.upserts.append((profile_id, text_hash))

    def delete(self, profile_id: int) -> bool:
        return True


class FakeLLM:
    """Prompt B 假实现：只统计调用次数，返回固定 12 标签 + 五维。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        tags = [TagItem(name="Python", type="skill"), TagItem(name="FastAPI", type="skill")]
        tags += [TagItem(name=f"通用标签{i}", type="general") for i in range(10)]
        return ProfileLLMOutput(
            tags=tags,
            ability_scores=AbilityScores(
                professional=AbilityDim(score=88, basis="主导重构"),
                project_experience=AbilityDim(score=80, basis="核心项目"),
                education=AbilityDim(score=70, basis="本科"),
                stability=AbilityDim(score=76, basis="两段各 3 年"),
                growth_potential=AbilityDim(score=82, basis="持续学习"),
            ),
        )


@pytest.fixture()
def fakes(monkeypatch):
    store = FakeStore()
    calls = {"embed": 0}

    def fake_embed_one(text: str):
        calls["embed"] += 1
        return [0.001] * 1024

    llm = FakeLLM()
    monkeypatch.setattr(milvus_store, "_default_store", store)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    monkeypatch.setattr(llm_client, "chat_json", llm)
    return SimpleNamespace(store=store, embed_calls=calls, llm=llm)


def make_structured(
    *,
    name: str = "张三",
    phone: str | None = "+86 138-0000-0000",
    email: str | None = None,
    city: str = "深圳",
    degree: str = "本科",
    title: str = "后端工程师",
    skills: list[str] | None = None,
    employments: list[dict] | None = None,
) -> dict:
    return {
        "basic": {"name": name, "phone": phone, "email": email, "city": city, "gender": "男"},
        "educations": [
            {"school": "某大学", "major": "计算机", "degree": degree, "start": "2014-09", "end": "2018-06"}
        ],
        "employments": employments
        if employments is not None
        else [{"company": "A 公司", "title": title, "start": "2018-07", "end": "2021-06", "description": "订单系统"}],
        "projects": [],
        "skills": ["Python 3.11", "golang"] if skills is None else skills,
        "certificates": [],
        "expected_title": "高级后端",
        "work_years": 6,
    }


# --------------------------------------------------------------------------- #
# 数据隔离
# --------------------------------------------------------------------------- #
@pytest.fixture()
def env():
    profile_ids: list[int] = []
    parsed_ids: list[int] = []
    task_ids: list[int] = []
    file_ids: list[int] = []

    def make_profile(**fields) -> int:
        with SessionLocal() as db:
            row = TalentProfile(
                source=fields.pop("source", "resume"),
                vector_status=fields.pop("vector_status", "synced"),
                **fields,
            )
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id

    def make_parsed(structured: dict, *, profile_id: int | None = None) -> tuple[int, int, int]:
        tag = uuid4().hex
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename=f"dup-{tag[:8]}.pdf",
                stored_path=f"data/uploads/{tag}.pdf",
                file_hash=tag * 2,
                file_ext="pdf",
                file_size=1024,
                upload_batch_id=tag[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            task = ParseTask(resume_file_id=file_row.id, status="pending", retries=0, truncated=0)
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

    def detect(parsed_id: int):
        with SessionLocal() as db:
            return svc.detect(db, db.get(ParsedResume, parsed_id))

    def flag(parsed_id: int) -> int:
        with SessionLocal() as db:
            parsed = db.get(ParsedResume, parsed_id)
            match = svc.detect(db, parsed)
            assert match is not None, "用例预期命中查重但 detect 返回 None"
            return svc.flag(db, parsed, match).id

    def resolve(duplicate_id: int, action: str, **kwargs):
        with SessionLocal() as db:
            return svc.resolve(db, duplicate_id, action=action, user_id=1, **kwargs)

    def load_parsed(parsed_id: int) -> ParsedResume:
        with SessionLocal() as db:
            return db.get(ParsedResume, parsed_id)

    def load_profile(profile_id: int) -> TalentProfile:
        with SessionLocal() as db:
            return db.get(TalentProfile, profile_id)

    def load_task(task_id: int) -> ParseTask:
        with SessionLocal() as db:
            return db.get(ParseTask, task_id)

    def load_duplicate(duplicate_id: int) -> TalentDuplicate:
        with SessionLocal() as db:
            return db.get(TalentDuplicate, duplicate_id)

    def tags_of(profile_id: int) -> dict[str, tuple[str, str]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(TalentTag).where(TalentTag.talent_profile_id == profile_id)
            ).scalars().all()
            return {row.tag: (row.tag_type, row.source) for row in rows}

    namespace = SimpleNamespace(
        make_profile=make_profile,
        make_parsed=make_parsed,
        detect=detect,
        flag=flag,
        resolve=resolve,
        load_parsed=load_parsed,
        load_profile=load_profile,
        load_task=load_task,
        load_duplicate=load_duplicate,
        tags_of=tags_of,
        parsed_ids=parsed_ids,
        profile_ids=profile_ids,
    )
    yield namespace

    with SessionLocal() as db:
        # merge/keep 会把 parsed 改指到新画像，必须按当前值收集
        linked = [
            pid
            for pid in db.execute(
                select(ParsedResume.talent_profile_id).where(ParsedResume.id.in_(parsed_ids))
            ).scalars().all()
            if pid
        ]
        all_profile_ids = {*profile_ids, *linked}
        if parsed_ids:
            db.execute(delete(TalentDuplicate).where(TalentDuplicate.parsed_resume_id.in_(parsed_ids)))
        if all_profile_ids:
            db.execute(delete(TalentTag).where(TalentTag.talent_profile_id.in_(all_profile_ids)))
            db.execute(delete(TalentProfile).where(TalentProfile.id.in_(all_profile_ids)))
        if parsed_ids:
            db.execute(delete(ParsedResume).where(ParsedResume.id.in_(parsed_ids)))
        if task_ids:
            db.execute(delete(ParseTask).where(ParseTask.id.in_(task_ids)))
        if file_ids:
            db.execute(delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        db.commit()


# --------------------------------------------------------------------------- #
# 1. 三路判重
# --------------------------------------------------------------------------- #
def test_phone_with_different_format_hits(env, fakes):
    """验收点 1：`+86 138-0000-0000` 与 `13800000000` 必须判为同一人。"""
    master_id = env.make_profile(name="张三", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(phone="+86 138-0000-0000"))

    match = env.detect(parsed_id)

    assert match is not None
    assert match.profile_id == master_id
    # 姓名与手机号都命中 → name_phone_match；仅手机号命中才是 phone_match（见开发日志 Layer 3）
    assert match.reason == "name_phone_match"
    assert match.matched_value == "138****0000"


def test_phone_hit_with_other_name_is_phone_match(env, fakes):
    """手机号相同但姓名不同 → phone_match（可能是共用号码，仍挂起交 HR 判断）。"""
    master_id = env.make_profile(name="李四", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(name="张三", phone="13800000000"))

    match = env.detect(parsed_id)

    assert match is not None
    assert (match.profile_id, match.reason) == (master_id, "phone_match")


def test_email_match_is_case_insensitive(env, fakes):
    master_id = env.make_profile(name="张三", phone=None, email="alice@example.com")
    _, _, parsed_id = env.make_parsed(make_structured(phone=None, email=" Alice@Example.COM "))

    match = env.detect(parsed_id)

    assert match is not None
    assert (match.profile_id, match.reason) == (master_id, "email_match")
    assert match.matched_value == "a***e@example.com"


def test_same_name_alone_does_not_hit(env, fakes):
    """姓名单独相同不判重（F2-2）——避免同名误合并。"""
    env.make_profile(name="张三", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(phone="13900000000", email=None))

    assert env.detect(parsed_id) is None


def test_excludes_profile_already_linked(env, fakes):
    """D-I：排除本解析结果已指向的画像，避免「画像已建+向量失败」重试自判重。"""
    master_id = env.make_profile(name="张三", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"), profile_id=master_id)

    assert env.detect(parsed_id) is None


def test_deleted_profile_does_not_hit(env, fakes):
    master_id = env.make_profile(name="张三", phone="13800000000")
    with SessionLocal() as db:
        db.get(TalentProfile, master_id).deleted_at = datetime.now()
        db.commit()

    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))

    assert env.detect(parsed_id) is None


# --------------------------------------------------------------------------- #
# 2. flag 幂等 + 任务挂起
# --------------------------------------------------------------------------- #
def test_flag_is_idempotent_and_suspends_task(env, fakes):
    env.make_profile(name="张三", phone="13800000000")
    _, task_id, parsed_id = env.make_parsed(make_structured(phone="13800000000"))

    first = env.flag(parsed_id)
    second = env.flag(parsed_id)

    assert first == second  # uk_parsed_resume：重试不新增行
    with SessionLocal() as db:
        count = db.execute(
            select(func.count(TalentDuplicate.id)).where(TalentDuplicate.parsed_resume_id == parsed_id)
        ).scalar_one()
    assert count == 1

    task = env.load_task(task_id)
    assert (task.status, task.stage) == ("duplicate_pending", "profile")
    assert task.profiled_at is None and task.finished_at is None


def test_flag_resets_previously_resolved_record(env, fakes):
    """requeue 全流程重跑后再次命中 → 原记录重置为 pending，不新增行。"""
    env.make_profile(name="张三", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    with SessionLocal() as db:
        row = db.get(TalentDuplicate, duplicate_id)
        row.status = "merged"
        row.resolved_by = 1
        row.resolved_at = datetime.now()
        db.commit()

    assert env.flag(parsed_id) == duplicate_id
    row = env.load_duplicate(duplicate_id)
    assert row.status == "pending"
    assert row.resolved_by is None and row.resolved_at is None


# --------------------------------------------------------------------------- #
# 3. merge
# --------------------------------------------------------------------------- #
def _merge_master(env) -> int:
    """master：city 受人工保护、ability 为原值、标签含人工标签、skills 只有 python。"""
    return env.make_profile(
        name="张三",
        phone="13800000000",
        email=None,
        city="北京",
        degree="大专",
        work_years=1,
        current_title="初级后端",
        skills_json=["python"],
        tags_json=["python", "团队管理"],
        ability_scores_json={
            "professional": {"score": 66, "basis": "master 原值"},
            "project_experience": {"score": 60, "basis": "master 原值"},
            "education": {"score": 55, "basis": "master 原值"},
            "stability": {"score": 70, "basis": "master 原值"},
            "growth_potential": {"score": 65, "basis": "master 原值"},
        },
        manual_edited_fields=["city"],
        vector_status="pending",
        profile_text_hash=None,
    )


def _seed_master_tags(master_id: int) -> None:
    with SessionLocal() as db:
        db.add_all(
            [
                TalentTag(talent_profile_id=master_id, tag="python", tag_type="skill", source="llm"),
                TalentTag(
                    talent_profile_id=master_id, tag="团队管理", tag_type="general", source="manual"
                ),
            ]
        )
        db.commit()


def test_merge_combines_into_master(env, fakes):
    """验收点 2：单画像、指向 master、ability 原值、向量重算、任务 success。"""
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, task_id, parsed_id = env.make_parsed(make_structured(phone="+86 138-0000-0000", city="深圳"))
    duplicate_id = env.flag(parsed_id)

    result = env.resolve(duplicate_id, "merge")

    assert result.action == "merge"
    assert result.vector_status == "synced"
    assert fakes.llm.calls == 0  # merge 不调 LLM
    assert len(fakes.store.upserts) == 1

    master = env.load_profile(master_id)
    assert master.city == "北京"  # manual_edited_fields 保护
    assert master.degree == "本科"  # 非保护字段被新简历覆盖
    assert master.work_years is not None and float(master.work_years) == 3.0
    assert master.current_title == "后端工程师"
    assert set(master.skills_json) == {"python", "go"}  # 并集
    assert master.ability_scores_json["professional"]["basis"] == "master 原值"  # 不重算
    assert master.vector_status == "synced"
    assert master.profile_text_hash is not None

    tags = env.tags_of(master_id)
    assert tags["python"] == ("skill", "llm")
    assert tags["团队管理"] == ("general", "manual")  # 人工标签来源不被覆盖
    assert tags["go"] == ("skill", "llm")

    parsed = env.load_parsed(parsed_id)
    assert parsed.talent_profile_id == master_id  # 解析结果改指 master

    task = env.load_task(task_id)
    assert task.status == "success" and task.stage is None
    assert task.profiled_at is not None and task.finished_at is not None

    duplicate = env.load_duplicate(duplicate_id)
    assert duplicate.status == "merged"
    assert duplicate.resolved_by == 1 and duplicate.resolved_at is not None
    assert duplicate.profile_b_id is None  # merge 不产生第二个画像


def test_merge_aggregates_experience_without_duplicates(env, fakes):
    """经历按 `company+title+start` 去重，保留 `end` 更晚的那条（§5.3.1 第 4 条）。"""
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    env.make_parsed(
        make_structured(
            phone="13800000000",
            employments=[
                {"company": "A 公司", "title": "后端", "start": "2018-07", "end": "2021-06", "description": "旧"},
                {"company": "B 公司", "title": "后端", "start": "2021-07", "end": None, "description": "在岗"},
            ],
        ),
        profile_id=master_id,
    )
    _, _, parsed_id = env.make_parsed(
        make_structured(
            phone="13800000000",
            employments=[
                {"company": "A 公司", "title": "后端", "start": "2018-07", "end": "2022-03", "description": "补全"},
                {"company": "C 公司", "title": "架构师", "start": "2022-04", "end": "2024-01", "description": "新"},
            ],
        )
    )
    duplicate_id = env.flag(parsed_id)

    env.resolve(duplicate_id, "merge")

    with SessionLocal() as db:
        aggregated = profile_service.aggregate_structured(db, master_id)
    keyed = {(item["company"], item["start"]): item for item in aggregated["employments"]}
    assert set(keyed) == {("A 公司", "2018-07"), ("B 公司", "2021-07"), ("C 公司", "2022-04")}
    assert keyed[("A 公司", "2018-07")]["end"] == "2022-03"  # end 更晚者胜
    assert keyed[("A 公司", "2018-07")]["description"] == "补全"


def test_merge_milvus_down_keeps_data_and_reports_failed(env, fakes):
    """验收点 5：Milvus 停机 → 数据完整、vector_status=failed、不回滚。"""
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, task_id, parsed_id = env.make_parsed(make_structured(phone="13800000000", city="深圳"))
    duplicate_id = env.flag(parsed_id)

    fakes.store.down = True
    result = env.resolve(duplicate_id, "merge")

    assert result.vector_status == "failed"
    master = env.load_profile(master_id)
    assert master.degree == "本科"  # 业务字段已提交
    assert master.vector_status == "failed"
    assert master.profile_text_hash is None  # 失败不写新 hash，下次重试必然重嵌
    assert env.load_duplicate(duplicate_id).status == "merged"
    assert env.load_task(task_id).status == "success"


def test_merge_rejects_non_pending_and_bad_master(env, fakes):
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    with pytest.raises(BizError) as bad_master:
        env.resolve(duplicate_id, "merge", master_profile_id=master_id + 999)
    assert bad_master.value.code == 400

    env.resolve(duplicate_id, "merge")

    with pytest.raises(BizError) as again:
        env.resolve(duplicate_id, "merge")
    assert again.value.code == 400  # 已处理不能再动

    with pytest.raises(BizError) as missing:
        env.resolve(duplicate_id + 999_999, "merge")
    assert missing.value.code == 404


def test_merge_rejects_deleted_master(env, fakes):
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    with SessionLocal() as db:
        db.get(TalentProfile, master_id).deleted_at = datetime.now()
        db.commit()

    with pytest.raises(BizError) as exc:
        env.resolve(duplicate_id, "merge")
    assert exc.value.code == 400


# --------------------------------------------------------------------------- #
# 4. keep
# --------------------------------------------------------------------------- #
def test_keep_creates_independent_profile(env, fakes):
    """验收点 3（同步阶段）：两画像独立、任务转 pending(stage=profile)、后台可补齐。"""
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, task_id, parsed_id = env.make_parsed(make_structured(phone="13800000000", city="深圳"))
    duplicate_id = env.flag(parsed_id)

    result = env.resolve(duplicate_id, "keep")

    assert result.action == "keep"
    new_id = result.profile.id
    assert new_id != master_id

    kept = env.load_profile(new_id)
    assert (kept.source, kept.vector_status) == ("resume", "pending")
    assert kept.tags_json is None and kept.ability_scores_json is None
    assert kept.city == "深圳" and kept.degree == "本科"
    assert kept.profile_text_hash is None

    assert env.load_parsed(parsed_id).talent_profile_id == new_id
    duplicate = env.load_duplicate(duplicate_id)
    assert duplicate.status == "kept"
    assert duplicate.profile_b_id == new_id

    task = env.load_task(task_id)
    assert (task.status, task.stage) == ("pending", "profile")
    assert task.profiled_at is None and task.finished_at is None

    # master 完全没动
    master = env.load_profile(master_id)
    assert master.city == "北京" and master.degree == "大专"

    # 模拟 runner 从 stage=profile 续跑：补齐标签/能力/向量
    with SessionLocal() as db:
        parsed = db.get(ParsedResume, parsed_id)
        profile_service.build_profile(db, parsed)
    filled = env.load_profile(new_id)
    assert filled.tags_json and filled.ability_scores_json
    assert filled.vector_status == "synced"
    assert fakes.llm.calls == 1


def test_keep_rejects_parsed_already_on_master(env, fakes):
    master_id = _merge_master(env)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"), profile_id=master_id)
    # 人为造一条 pending 记录（正常流程下 detect 会排除自己，此处只验证 keep 的防护）
    with SessionLocal() as db:
        row = TalentDuplicate(
            profile_a_id=master_id,
            parsed_resume_id=parsed_id,
            reason="phone_match",
            matched_value="138****0000",
            status="pending",
        )
        db.add(row)
        db.commit()
        duplicate_id = row.id

    with pytest.raises(BizError) as exc:
        env.resolve(duplicate_id, "keep")
    assert exc.value.code == 400


# --------------------------------------------------------------------------- #
# 5. API
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


def test_api_list_masks_contact(env, fakes, client, auth):
    env.make_profile(name="张三", phone="13800000000")
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    resp = client.get("/api/talents/duplicates", params={"status": "pending"}, headers=auth)

    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    item = next(row for row in items if row["id"] == duplicate_id)
    assert item["matched_value"] == "138****0000"
    assert item["parsed_resume"]["phone"] == "138****0000"
    assert item["profile_a"]["name"] == "张三"
    assert item["profile_b"] is None


def test_api_resolve_merge_then_400(env, fakes, client, auth):
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    resp = client.post(
        f"/api/talents/duplicates/{duplicate_id}/resolve", json={"action": "merge"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "merged"
    assert data["master_profile_id"] == master_id
    assert data["task_status"] == "success"

    again = client.post(
        f"/api/talents/duplicates/{duplicate_id}/resolve", json={"action": "merge"}, headers=auth
    )
    assert again.status_code == 400
    assert again.json()["code"] == 400


def test_api_resolve_keep_returns_202(env, fakes, client, auth):
    master_id = _merge_master(env)
    _seed_master_tags(master_id)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    resp = client.post(
        f"/api/talents/duplicates/{duplicate_id}/resolve", json={"action": "keep"}, headers=auth
    )

    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["status"] == "kept"
    assert data["talent_profile_id"] != master_id
    assert (data["task_status"], data["stage"]) == ("pending", "profile")


def test_api_resolve_master_mismatch_is_400(env, fakes, client, auth):
    master_id = _merge_master(env)
    _, _, parsed_id = env.make_parsed(make_structured(phone="13800000000"))
    duplicate_id = env.flag(parsed_id)

    resp = client.post(
        f"/api/talents/duplicates/{duplicate_id}/resolve",
        json={"action": "merge", "master_profile_id": master_id + 999},
        headers=auth,
    )
    assert resp.status_code == 400


def test_api_resolve_missing_is_404(env, client, auth):
    resp = client.post(
        "/api/talents/duplicates/99999999/resolve", json={"action": "keep"}, headers=auth
    )
    assert resp.status_code == 404


def test_api_requires_auth(client):
    assert client.get("/api/talents/duplicates").status_code == 401
