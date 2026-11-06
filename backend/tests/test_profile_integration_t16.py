"""T-16 L2：画像生成集成测试（真实 MySQL；LLM/Embedding/Milvus 全用假件）。

覆盖 todolist T-16 五个验收点：
1. 解析成功后画像 / 标签行 / 向量三处齐全；
2. 改 `manual_edited_fields` 中字段后重跑解析不被覆盖，其余字段正常更新；
3. 只改 city 等非标签字段 → `profile_text_hash` 变化并触发重新 upsert；
4. hash 未变 → 零 embedding 调用；
5. Milvus 停机 → 画像行与标签仍写入成功、`vector_status=failed`。

隔离：每个用例建独立的 `resume_files`/`parse_tasks`/`parsed_resumes` 行（哈希带随机 tag），
结束按 id 逆序删除，库里不留垃圾。
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core import embedding_client, llm_client, milvus_store
from app.database import SessionLocal
from app.models import ParseTask, ParsedResume, ResumeFile, TalentProfile, TalentTag
from app.schemas.profile import AbilityDim, AbilityScores, ProfileLLMOutput, TagItem
from app.service import profile_service as svc

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 假件
# --------------------------------------------------------------------------- #
class FakeStore:
    """只实现 upsert/delete；记录写入次数，可选模拟 Milvus 停机。"""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.upserts: list[tuple[int, str]] = []

    def upsert(self, profile_id: int, vector, text_hash: str) -> None:
        if self.down:
            raise RuntimeError("Milvus 不可用（测试模拟）")
        assert len(vector) == 1024
        self.upserts.append((profile_id, text_hash))

    def delete(self, profile_id: int) -> bool:
        return True


class FakeLLM:
    """可编排的 Prompt B 假实现：按顺序返回预设输出，记录调用次数。"""

    def __init__(self, outputs: list[ProfileLLMOutput]) -> None:
        self.outputs = outputs
        self.calls = 0

    def __call__(self, *args, **kwargs):
        index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[index]


def make_output(tag_count: int = 12) -> ProfileLLMOutput:
    tags = [TagItem(name="Python 3.11", type="skill"), TagItem(name="FastAPI", type="skill")]
    tags += [TagItem(name=f"标签{i}", type="general") for i in range(max(0, tag_count - 2))]
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


def make_structured(*, city: str = "深圳", title: str = "后端工程师") -> dict:
    return {
        "basic": {
            "name": "张三",
            "gender": "男",
            "birth_year": 1996,
            "phone": "+86 138-0000-0000",
            "email": "Zhang@Example.com",
            "city": city,
        },
        "educations": [{"school": "某大学", "major": "计算机", "degree": "本科", "start": "2014-09", "end": "2018-06"}],
        "employments": [
            {"company": "A 公司", "title": "后端工程师", "start": "2018-07", "end": "2021-06", "description": "订单系统"},
            {"company": "B 公司", "title": title, "start": "2021-07", "end": "至今", "description": "中台建设"},
        ],
        "projects": [{"name": "中台", "role": "负责人", "start": "2022-01", "end": "2023-01", "description": "统一网关"}],
        "skills": ["Python 3.11", "golang", "FastAPI"],
        "certificates": [],
        "expected_title": "高级后端",
        "work_years": 6.0,
    }


# --------------------------------------------------------------------------- #
# 假件装配与数据隔离
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fakes(monkeypatch):
    store = FakeStore()
    calls = {"embed": 0}

    def fake_embed_one(text: str):
        calls["embed"] += 1
        return [0.001] * 1024

    monkeypatch.setattr(milvus_store, "_default_store", store)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)

    llm = FakeLLM([make_output()])
    monkeypatch.setattr(llm_client, "chat_json", llm)

    return {"store": store, "embed_calls": calls, "llm": llm}


@pytest.fixture()
def resume_factory():
    """建 resume_files + parse_tasks + parsed_resumes 三行，返回 (file_id, parsed_id)。"""
    created: list[tuple[int, int, int]] = []

    def _make(structured: dict) -> tuple[int, int]:
        tag = uuid4().hex
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename=f"简历-{tag[:8]}.pdf",
                stored_path=f"data/uploads/{tag}.pdf",
                file_hash=tag * 2,
                file_ext="pdf",
                file_size=1024,
                upload_batch_id=uuid4().hex[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            task = ParseTask(resume_file_id=file_row.id, status="pending", stage=None, retries=0, truncated=0)
            db.add(task)
            db.flush()
            parsed = ParsedResume(
                resume_file_id=file_row.id,
                talent_profile_id=None,
                structured_json=structured,
                raw_text="张三 后端工程师",
                llm_model="fake",
            )
            db.add(parsed)
            db.commit()
            created.append((file_row.id, task.id, parsed.id))
            return file_row.id, parsed.id

    yield _make

    with SessionLocal() as db:
        parsed_ids = [item[2] for item in created]
        file_ids = [item[0] for item in created]
        task_ids = [item[1] for item in created]
        if parsed_ids:
            profile_ids = [
                row for row in db.execute(
                    select(ParsedResume.talent_profile_id).where(ParsedResume.id.in_(parsed_ids))
                ).scalars().all() if row
            ]
            if profile_ids:
                db.execute(delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
                db.execute(delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
            db.execute(delete(ParsedResume).where(ParsedResume.id.in_(parsed_ids)))
        if task_ids:
            db.execute(delete(ParseTask).where(ParseTask.id.in_(task_ids)))
        if file_ids:
            db.execute(delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        db.commit()


def load_parsed(parsed_id: int) -> ParsedResume:
    with SessionLocal() as db:
        return db.get(ParsedResume, parsed_id)


def load_profile(profile_id: int) -> TalentProfile:
    with SessionLocal() as db:
        return db.get(TalentProfile, profile_id)


def count_tags(profile_id: int) -> int:
    with SessionLocal() as db:
        return int(
            db.execute(
                select(func.count(TalentTag.id)).where(TalentTag.talent_profile_id == profile_id)
            ).scalar_one()
        )


# --------------------------------------------------------------------------- #
# 验收点 1：画像 / 标签行 / 向量三处齐全
# --------------------------------------------------------------------------- #
def test_first_build_writes_profile_tags_and_vector(fakes, resume_factory):
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        parsed = db.get(ParsedResume, parsed_id)
        result = svc.build_profile(db, parsed)

    profile = result.profile
    assert result.created is True
    assert result.llm_called is True
    assert result.ok and result.vector_status == "synced"

    fresh = load_profile(profile.id)
    assert fresh.name == "张三"
    assert fresh.degree == "本科"
    assert fresh.phone == "13800000000"  # 规范化后入库
    assert fresh.email == "zhang@example.com"
    assert fresh.current_title == "后端工程师"
    assert fresh.work_years >= Decimal("4.0")
    assert fresh.skills_json == ["fastapi", "go", "python"]
    assert fresh.vector_status == "synced"
    assert fresh.profile_text_hash  # 已写 hash
    assert len(fresh.tags_json) == 12
    assert count_tags(profile.id) == 12  # tags_json 与 talent_tags 一致
    assert fakes["store"].upserts == [(profile.id, fresh.profile_text_hash)]
    assert load_parsed(parsed_id).talent_profile_id == profile.id  # 同事务回填

    with SessionLocal() as db:
        skill_tags = db.execute(
            select(TalentTag.tag).where(
                TalentTag.talent_profile_id == profile.id, TalentTag.tag_type == "skill"
            )
        ).scalars().all()
    assert sorted(skill_tags) == ["fastapi", "python"]  # 技能标签已归一化


# --------------------------------------------------------------------------- #
# 验收点 4：hash 未变零 embedding；幂等不建第二行、不再调 LLM
# --------------------------------------------------------------------------- #
def test_second_build_is_idempotent(fakes, resume_factory):
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        first = svc.build_profile(db, db.get(ParsedResume, parsed_id))
    embed_after_first = fakes["embed_calls"]["embed"]
    assert embed_after_first == 1

    fakes["llm"].calls = 0
    with SessionLocal() as db:
        second = svc.build_profile(db, db.get(ParsedResume, parsed_id))

    assert second.created is False
    assert second.profile.id == first.profile.id
    assert second.llm_called is False  # tags_json / ability_scores_json 已落库
    assert second.vector_skipped is True  # hash 未变
    assert fakes["embed_calls"]["embed"] == embed_after_first  # 零新增 embedding
    assert fakes["llm"].calls == 0

    with SessionLocal() as db:
        total = db.execute(
            select(func.count(TalentProfile.id)).where(TalentProfile.id == first.profile.id)
        ).scalar_one()
    assert total == 1


# --------------------------------------------------------------------------- #
# 验收点 2：manual_edited_fields 保护
# --------------------------------------------------------------------------- #
def test_manual_edited_fields_protected_on_reparse(fakes, resume_factory):
    _, parsed_id = resume_factory(make_structured(city="深圳"))
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))
        profile_id = result.profile.id

    with SessionLocal() as db:
        profile = db.get(TalentProfile, profile_id)
        profile.city = "上海"  # 人工改成上海
        profile.manual_edited_fields = ["city"]
        db.commit()

    # 重跑解析：新结果里 city=北京、title 也变了
    new_structured = make_structured(city="北京", title="技术专家")
    with SessionLocal() as db:
        parsed = db.get(ParsedResume, parsed_id)
        parsed.structured_json = new_structured
        db.commit()
        svc.build_profile(db, parsed)

    fresh = load_profile(profile_id)
    assert fresh.city == "上海"  # 人工字段不被覆盖
    assert fresh.current_title == "技术专家"  # 其余字段正常更新


# --------------------------------------------------------------------------- #
# 验收点 3：只改 city 也触发重新 upsert
# --------------------------------------------------------------------------- #
def test_city_change_triggers_reembed(fakes, resume_factory):
    _, parsed_id = resume_factory(make_structured(city="深圳"))
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))
    old_hash = load_profile(result.profile.id).profile_text_hash
    assert fakes["embed_calls"]["embed"] == 1

    with SessionLocal() as db:
        parsed = db.get(ParsedResume, parsed_id)
        parsed.structured_json = make_structured(city="北京")
        db.commit()

    with SessionLocal() as db:
        second = svc.build_profile(db, db.get(ParsedResume, parsed_id))

    fresh = load_profile(result.profile.id)
    assert second.llm_called is False  # 标签/能力都在，不重跑 LLM
    assert second.vector_skipped is False
    assert fresh.city == "北京"
    assert fresh.profile_text_hash != old_hash  # hash 变了
    assert fakes["embed_calls"]["embed"] == 2
    assert len(fakes["store"].upserts) == 2


# --------------------------------------------------------------------------- #
# 验收点 5：Milvus 停机 → 画像与标签照常写入，vector_status=failed
# --------------------------------------------------------------------------- #
def test_milvus_down_keeps_mysql_writes(fakes, resume_factory):
    fakes["store"].down = True
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))

    assert result.ok is False
    assert result.vector_status == "failed"

    fresh = load_profile(result.profile.id)
    assert fresh.vector_status == "failed"
    assert fresh.profile_text_hash is None  # 失败不写 hash → 下次重试必然重嵌
    assert len(fresh.tags_json) == 12  # 标签照常落库
    assert count_tags(result.profile.id) == 12

    # 恢复后重试：hash 仍为 None → 重新 embedding 并转 synced
    fakes["store"].down = False
    with SessionLocal() as db:
        retry = svc.build_profile(db, db.get(ParsedResume, parsed_id))
    assert retry.llm_called is False
    assert retry.vector_status == "synced"
    assert load_profile(result.profile.id).profile_text_hash


# --------------------------------------------------------------------------- #
# 只补缺失子步骤（断点续跑 / keep 后台补齐的公共路径）
# --------------------------------------------------------------------------- #
def test_only_missing_ability_is_backfilled(fakes, resume_factory):
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))
    profile_id = result.profile.id
    original_tags = load_profile(profile_id).tags_json

    # 模拟「标签成功、能力失败」：只清空 ability_scores_json
    with SessionLocal() as db:
        profile = db.get(TalentProfile, profile_id)
        profile.ability_scores_json = None
        db.commit()

    fakes["llm"].calls = 0
    with SessionLocal() as db:
        second = svc.build_profile(db, db.get(ParsedResume, parsed_id))

    fresh = load_profile(profile_id)
    assert fakes["llm"].calls == 1
    assert second.tags_written is False  # 不重写已有标签
    assert second.ability_written is True
    assert fresh.tags_json == original_tags
    assert fresh.ability_scores_json["professional"]["score"] == 88
    assert count_tags(profile_id) == 12  # 标签行没被清空重建


def test_allow_llm_false_only_resyncs_vector(fakes, resume_factory):
    """stage=vector 续跑：全齐时只补向量，绝不调 LLM。"""
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))
    profile_id = result.profile.id

    with SessionLocal() as db:
        profile = db.get(TalentProfile, profile_id)
        profile.vector_status = "failed"
        profile.profile_text_hash = None
        db.commit()

    fakes["llm"].calls = 0
    with SessionLocal() as db:
        retry = svc.build_profile(db, db.get(ParsedResume, parsed_id), allow_llm=False)

    assert retry.llm_called is False
    assert fakes["llm"].calls == 0
    assert retry.vector_status == "synced"


# --------------------------------------------------------------------------- #
# 标签不足 10 个：重试 1 次后接受（不判失败，算法 §6.1）
# --------------------------------------------------------------------------- #
def test_short_tag_list_accepted_after_retry(fakes, resume_factory):
    fakes["llm"].outputs = [make_output(5)]  # 两次都只回 5 个
    _, parsed_id = resume_factory(make_structured())
    with SessionLocal() as db:
        result = svc.build_profile(db, db.get(ParsedResume, parsed_id))

    assert fakes["llm"].calls == 2  # 首次 + 重试 1 次
    assert result.ok
    assert len(load_profile(result.profile.id).tags_json) == 5
    assert count_tags(result.profile.id) == 5
