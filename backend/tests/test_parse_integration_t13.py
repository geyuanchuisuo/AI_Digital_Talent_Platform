"""T-13 L2：解析流水线编排（真实 MySQL + 真实 DOCX 文件；LLM/Embedding/Milvus 用假件）。

覆盖 todolist T-13 的验收点：
1. 10 份合成简历解析成功率 100%；
2. 标签生成失败 → `failed(stage=profile)`，重试可恢复；
3. 不存在 "success 但无画像"；
4. 同一份简历连续 retry 5 次后 `talent_profiles` / `parsed_resumes` 只有 1 行、
   `talent_duplicates` 至多 1 行，且不出现自判重；
5. `stage=vector` 的重试不产生新的 LLM 调用。

另外覆盖断点入口的三个分支（`profile` 未回填 → 从查重开始；`profile` 已回填 → 只补缺失
子步骤；`vector` → 只补向量）与查重挂起路径。

隔离：每个用例自建文件行 + 任务行 + 磁盘文件，结束时按 `resume_file_id` 反查画像并逆序删除。
"""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import docx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select

from app.config import BASE_DIR
from app.core import embedding_client, llm_client, milvus_store
from app.database import SessionLocal
from app.models import (
    ParsedResume,
    ParseTask,
    ResumeFile,
    TalentDuplicate,
    TalentProfile,
    TalentTag,
)
from app.schemas.parse import ResumeSchema
from app.schemas.profile import AbilityDim, AbilityScores, ProfileLLMOutput, TagItem
from app.tasks import parse_worker

pytestmark = pytest.mark.integration

PHONE_RE = re.compile(r"手机号：(\d{11})")
NAME_RE = re.compile(r"姓名：(\S+)")


# --------------------------------------------------------------------------- #
# 假件：Prompt A / Prompt B 按 `prompt_name` 路由，Milvus + Embedding 不走网络
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
    """Prompt A 从 user 文本里回读姓名/手机号，保证 10 份简历互不串号。"""

    def __init__(self) -> None:
        self.parse_calls = 0
        self.profile_calls = 0
        self.fail_profile = False
        self.fail_parse = False

    def __call__(self, prompt, schema=None, **kwargs):
        name = kwargs.get("prompt_name")
        if name == "parse":
            self.parse_calls += 1
            if self.fail_parse:
                raise llm_client.LLMError("模拟 Prompt A 输出无法解析")
            return self._resume(prompt)
        if name == "profile":
            self.profile_calls += 1
            if self.fail_profile:
                raise llm_client.LLMError("模拟 Prompt B 输出无法解析")
            return self._profile()
        raise AssertionError(f"未预期的 prompt_name={name!r}")

    @staticmethod
    def _resume(prompt: str) -> ResumeSchema:
        phone = PHONE_RE.search(prompt)
        name = NAME_RE.search(prompt)
        return ResumeSchema.model_validate(
            {
                "basic": {
                    "name": name.group(1) if name else None,
                    "gender": "男",
                    "birth_year": 1996,
                    "phone": phone.group(1) if phone else None,
                    "email": None,
                    "city": "深圳",
                },
                "educations": [
                    {
                        "school": "某大学",
                        "major": "计算机",
                        "degree": "本科",
                        "start": "2014-09",
                        "end": "2018-06",
                    }
                ],
                "employments": [
                    {
                        "company": "A 公司",
                        "title": "后端工程师",
                        "start": "2018-07",
                        "end": None,
                        "description": "订单系统重构",
                    }
                ],
                "projects": [],
                "skills": ["Python 3.11", "golang"],
                "certificates": [],
                "expected_title": "高级后端",
                "work_years": 6,
            }
        )

    @staticmethod
    def _profile() -> ProfileLLMOutput:
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
    counters = {"embed": 0}

    def fake_embed_one(_text: str):
        counters["embed"] += 1
        return [0.001] * 1024

    llm = FakeLLM()
    monkeypatch.setattr(milvus_store, "_default_store", store)
    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    monkeypatch.setattr(llm_client, "chat_json", llm)
    return SimpleNamespace(store=store, embed=counters, llm=llm)


# --------------------------------------------------------------------------- #
# 数据隔离
# --------------------------------------------------------------------------- #
def _write_docx(lines: list[str], tag: str):
    target = BASE_DIR / "data" / "uploads" / f"t13-{tag}.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(target)
    return target


@pytest.fixture()
def env():
    file_ids: list[int] = []
    task_ids: list[int] = []
    preset_profiles: list[int] = []
    paths: list = []

    def make(
        *,
        name: str = "张三",
        phone: str = "13800000001",
        lines: list[str] | None = None,
        exists: bool = True,
    ) -> tuple[int, int]:
        """建落盘文件 + `resume_files` 行 + `running` 任务行（模拟 runner 已认领）。"""
        tag = uuid4().hex
        target = _write_docx(
            lines
            if lines is not None
            else [
                f"姓名：{name}",
                f"手机号：{phone}",
                "求职意向：后端开发工程师",
                "工作经历：2018-07 至今 A 公司 后端工程师（订单系统重构）",
            ],
            tag,
        )
        paths.append(target)
        with SessionLocal() as db:
            file_row = ResumeFile(
                filename=f"t13-{tag[:8]}.docx",
                stored_path=target.relative_to(BASE_DIR).as_posix(),
                file_hash=tag * 2,
                file_ext="docx",
                file_size=target.stat().st_size,
                upload_batch_id=tag[:26].upper(),
                uploaded_by=1,
            )
            db.add(file_row)
            db.flush()
            task = ParseTask(
                resume_file_id=file_row.id,
                status="running",
                stage=None,
                retries=0,
                truncated=0,
                started_at=datetime.now(),
                heartbeat_at=datetime.now(),
            )
            db.add(task)
            db.commit()
            file_ids.append(file_row.id)
            task_ids.append(task.id)
            if not exists:
                target.unlink(missing_ok=True)
            return file_row.id, task.id

    def make_profile(*, name: str = "张三", phone: str = "13800000009") -> int:
        with SessionLocal() as db:
            row = TalentProfile(name=name, phone=phone, source="resume", vector_status="synced")
            db.add(row)
            db.commit()
            preset_profiles.append(row.id)
            return row.id

    def reclaim(task_id: int, *, stage: str | None = None) -> None:
        """模拟 retry：状态回 running（runner 认领后的样子）、stage 按用例指定。"""
        with SessionLocal() as db:
            task = db.get(ParseTask, task_id)
            task.status = "running"
            task.stage = stage
            task.error = None
            task.finished_at = None
            task.heartbeat_at = datetime.now()
            db.commit()

    def task_row(task_id: int) -> ParseTask:
        with SessionLocal() as db:
            return db.get(ParseTask, task_id)

    def parsed_row(file_id: int) -> ParsedResume | None:
        with SessionLocal() as db:
            return db.execute(
                select(ParsedResume).where(ParsedResume.resume_file_id == file_id)
            ).scalar_one_or_none()

    def profile_of(file_id: int) -> TalentProfile | None:
        with SessionLocal() as db:
            parsed = db.execute(
                select(ParsedResume).where(ParsedResume.resume_file_id == file_id)
            ).scalar_one_or_none()
            if parsed is None or not parsed.talent_profile_id:
                return None
            return db.get(TalentProfile, parsed.talent_profile_id)

    def counts(file_id: int) -> dict[str, int]:
        with SessionLocal() as db:
            parsed = db.execute(
                select(ParsedResume).where(ParsedResume.resume_file_id == file_id)
            ).scalar_one_or_none()
            profile_id = parsed.talent_profile_id if parsed else None
            return {
                "parsed": int(
                    db.execute(
                        select(func.count(ParsedResume.id)).where(
                            ParsedResume.resume_file_id == file_id
                        )
                    ).scalar_one()
                ),
                "profiles": int(
                    db.execute(
                        select(func.count(TalentProfile.id)).where(
                            TalentProfile.id == profile_id
                        )
                    ).scalar_one()
                )
                if profile_id
                else 0,
                "duplicates": int(
                    db.execute(
                        select(func.count(TalentDuplicate.id)).where(
                            TalentDuplicate.parsed_resume_id == (parsed.id if parsed else -1)
                        )
                    ).scalar_one()
                ),
                "tasks": int(
                    db.execute(
                        select(func.count(ParseTask.id)).where(
                            ParseTask.resume_file_id == file_id
                        )
                    ).scalar_one()
                ),
            }

    def tag_names(profile_id: int) -> list[str]:
        with SessionLocal() as db:
            return list(
                db.execute(
                    select(TalentTag.tag).where(TalentTag.talent_profile_id == profile_id)
                ).scalars()
            )

    yield SimpleNamespace(
        make=make,
        make_profile=make_profile,
        reclaim=reclaim,
        task=task_row,
        parsed=parsed_row,
        profile=profile_of,
        counts=counts,
        tags=tag_names,
    )

    with SessionLocal() as db:
        parsed_rows = (
            db.execute(
                select(ParsedResume).where(ParsedResume.resume_file_id.in_(file_ids))
            ).scalars().all()
            if file_ids
            else []
        )
        linked = {row.talent_profile_id for row in parsed_rows if row.talent_profile_id}
        all_profiles = set(preset_profiles) | linked
        if all_profiles:
            db.execute(delete(TalentTag).where(TalentTag.talent_profile_id.in_(all_profiles)))
            db.execute(
                delete(TalentDuplicate).where(
                    or_(
                        TalentDuplicate.profile_a_id.in_(all_profiles),
                        TalentDuplicate.profile_b_id.in_(all_profiles),
                    )
                )
            )
        if file_ids:
            db.execute(delete(ParsedResume).where(ParsedResume.resume_file_id.in_(file_ids)))
            db.execute(delete(ParseTask).where(ParseTask.resume_file_id.in_(file_ids)))
            db.execute(delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        if all_profiles:
            db.execute(delete(TalentProfile).where(TalentProfile.id.in_(all_profiles)))
        db.commit()
    for path in paths:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def test_full_pipeline_success(env, fakes):
    file_id, task_id = env.make(name="张三", phone="13800000001")

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "success"
    assert task.stage is None
    assert task.error is None
    assert task.profiled_at is not None and task.finished_at is not None
    assert task.truncated == 0

    parsed = env.parsed(file_id)
    assert parsed is not None and parsed.talent_profile_id
    assert parsed.structured_json["basic"]["name"] == "张三"
    assert parsed.raw_text.startswith("姓名：张三")
    assert parsed.llm_model  # 追溯用

    profile = env.profile(file_id)
    assert profile.name == "张三"
    assert profile.phone == "13800000001"
    assert profile.degree == "本科"
    assert profile.current_title == "后端工程师"
    assert float(profile.work_years) > 0
    assert profile.tags_json and profile.ability_scores_json
    assert profile.vector_status == "synced"
    assert profile.profile_text_hash

    assert fakes.llm.parse_calls == 1
    assert fakes.llm.profile_calls == 1
    assert len(fakes.store.upserts) == 1
    assert len(env.tags(profile.id)) == len(profile.tags_json)


def test_ten_resumes_all_success(env, fakes):
    """验收点 1：10 份合成简历成功率 100%，且每份都有画像（不存在 success 但无画像）。"""
    pairs = [env.make(name=f"候选人{i}", phone=f"139000000{i:02d}") for i in range(10)]

    for _file_id, task_id in pairs:
        parse_worker.handle_parse(task_id)

    statuses = [env.task(task_id).status for _file_id, task_id in pairs]
    assert statuses == ["success"] * 10
    for file_id, _task_id in pairs:
        profile = env.profile(file_id)
        assert profile is not None, "success 必须伴随画像"
        assert profile.vector_status == "synced"
    assert fakes.llm.parse_calls == 10
    assert fakes.llm.profile_calls == 10


def test_duplicate_resume_hangs_without_profile(env, fakes):
    """查重命中：任务挂起、不建画像、不写 profiled_at/finished_at。"""
    existing_id = env.make_profile(name="张三", phone="13800000009")
    file_id, task_id = env.make(name="张三", phone="13800000009")

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "duplicate_pending"
    assert task.stage == "profile"  # resolve 后从 profile 续跑
    assert task.profiled_at is None and task.finished_at is None

    parsed = env.parsed(file_id)
    assert parsed is not None
    assert parsed.talent_profile_id is None, "重复简历不得抢先建画像"

    with SessionLocal() as db:
        duplicate = db.execute(
            select(TalentDuplicate).where(TalentDuplicate.parsed_resume_id == parsed.id)
        ).scalar_one()
        assert duplicate.profile_a_id == existing_id
        assert duplicate.reason == "name_phone_match"
        assert duplicate.status == "pending"
        assert (
            db.execute(
                select(func.count(TalentProfile.id)).where(TalentProfile.phone == "13800000009")
            ).scalar_one()
            == 1
        ), "命中重复不得再建一张画像"
    assert fakes.llm.profile_calls == 0, "挂起的简历不调 Prompt B"


def test_profile_stage_without_profile_starts_from_dedup(env, fakes):
    """断点入口：`stage=profile` 但画像未回填 → 跳过 Prompt A，从查重开始。"""
    file_id, task_id = env.make(name="李四", phone="13800000002")
    with SessionLocal() as db:
        task = db.get(ParseTask, task_id)
        task.stage = "profile"
        db.add(
            ParsedResume(
                resume_file_id=file_id,
                talent_profile_id=None,
                structured_json={
                    "basic": {"name": "李四", "phone": "13800000002"},
                    "educations": [{"degree": "本科"}],
                    "employments": [{"company": "B 公司", "title": "后端", "start": "2019-01"}],
                    "skills": ["Python"],
                },
                raw_text="姓名：李四",
                llm_model="fake",
            )
        )
        db.commit()

    parse_worker.handle_parse(task_id)

    assert env.task(task_id).status == "success"
    assert fakes.llm.parse_calls == 0, "stage=profile 未回填时应跳过 Prompt A"
    assert fakes.llm.profile_calls == 1
    assert env.profile(file_id) is not None


def test_profile_stage_with_profile_only_fills_missing_ability(env, fakes):
    """断点入口：`stage=profile` 且已回填 → 只补缺失子步骤（标签已有则不重跑）。"""
    file_id, task_id = env.make(name="王五", phone="13800000003")
    parse_worker.handle_parse(task_id)
    profile = env.profile(file_id)
    tags_before = sorted(env.tags(profile.id))
    embed_before = fakes.embed["embed"]
    calls_before = fakes.llm.profile_calls

    # 模拟"标签成功、能力失败"的中间态：只清能力，标签保留
    with SessionLocal() as db:
        row = db.get(TalentProfile, profile.id)
        row.ability_scores_json = None
        db.commit()

    env.reclaim(task_id, stage="profile")
    parse_worker.handle_parse(task_id)

    assert env.task(task_id).status == "success"
    assert fakes.llm.profile_calls == calls_before + 1, "只应补一次能力评分"
    assert sorted(env.tags(profile.id)) == tags_before, "标签不该被重建"
    assert env.profile(file_id).ability_scores_json is not None
    assert fakes.embed["embed"] == embed_before, "能力不进画像文本，hash 未变 → 零 embedding"


# --------------------------------------------------------------------------- #
# 失败与恢复
# --------------------------------------------------------------------------- #
def test_profile_llm_failure_then_retry_recovers(env, fakes):
    """验收点 2：标签生成失败 → failed(stage=profile)；修好后重试成功。"""
    _file_id, task_id = env.make(name="赵六", phone="13800000004")
    fakes.llm.fail_profile = True

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "failed"
    assert task.stage == "profile"
    assert "Prompt B" in task.error
    assert task.finished_at is not None

    fakes.llm.fail_profile = False
    env.reclaim(task_id, stage=task.stage)
    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "success"
    assert task.stage is None and task.error is None
    assert task.profiled_at is not None


def test_vector_failure_marks_stage_vector_and_retry_has_zero_llm(env, fakes):
    """验收点 5：向量失败落 failed(stage=vector)；重试只补向量，零 LLM 调用。"""
    file_id, task_id = env.make(name="孙七", phone="13800000005")
    fakes.store.down = True

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "failed"
    assert task.stage == "vector"
    profile = env.profile(file_id)
    assert profile is not None, "画像与标签已写入，不因向量失败回滚"
    assert profile.vector_status == "failed"
    assert profile.profile_text_hash is None, "失败不写新 hash，重试必然重新嵌入"
    parse_calls, profile_calls = fakes.llm.parse_calls, fakes.llm.profile_calls

    fakes.store.down = False
    env.reclaim(task_id, stage="vector")
    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "success"
    assert task.stage is None
    assert env.profile(file_id).vector_status == "synced"
    assert fakes.llm.parse_calls == parse_calls, "stage=vector 不得再调 Prompt A"
    assert fakes.llm.profile_calls == profile_calls, "stage=vector 不得再调 Prompt B"


def test_extract_failure_marks_stage_extract(env, fakes):
    _file_id, task_id = env.make(name="周八", phone="13800000006", exists=False)

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "failed"
    assert task.stage == "extract"
    assert "不存在" in task.error
    assert task.profiled_at is None
    assert fakes.llm.parse_calls == 0


def test_blank_document_fails_without_llm(env, fakes):
    _file_id, task_id = env.make(name="空简历", phone="13800000007", lines=["   ", ""])

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "failed"
    assert task.stage == "extract"
    assert "未能提取到文本" in task.error
    assert fakes.llm.parse_calls == 0


def test_prompt_a_failure_marks_stage_extract(env, fakes):
    _file_id, task_id = env.make(name="吴九", phone="13800000008")
    fakes.llm.fail_parse = True

    parse_worker.handle_parse(task_id)

    task = env.task(task_id)
    assert task.status == "failed"
    assert task.stage == "extract"
    assert "Prompt A" in task.error


# --------------------------------------------------------------------------- #
# 幂等
# --------------------------------------------------------------------------- #
def test_five_retries_keep_single_rows_and_no_self_duplicate(env, fakes):
    """验收点 4：连续 retry 5 次后三类行各至多 1 行，且不把自己判成重复人。"""
    file_id, task_id = env.make(name="郑十", phone="13800000010")
    parse_worker.handle_parse(task_id)
    profile_id = env.profile(file_id).id
    assert env.task(task_id).status == "success"

    for _ in range(5):
        env.reclaim(task_id, stage=None)  # 全流程重跑
        parse_worker.handle_parse(task_id)
        assert env.task(task_id).status == "success"

    counts = env.counts(file_id)
    assert counts["parsed"] == 1, "parsed_resumes 必须按 resume_file_id 整体更新"
    assert counts["profiles"] == 1, "不得重复建画像（含自判重导致的第二张）"
    assert counts["duplicates"] == 0, "自己指向的画像必须被排除（D-I）"
    assert counts["tasks"] == 1
    assert env.profile(file_id).id == profile_id

    with SessionLocal() as db:
        rows = db.execute(
            select(func.count(TalentProfile.id)).where(TalentProfile.phone == "13800000010")
        ).scalar_one()
        assert rows == 1


def test_handle_parse_ignores_task_not_running(env, fakes):
    """runner 派发与 retry/删除竞争时，worker 不覆盖调用方的状态。"""
    file_id, task_id = env.make(name="钱十一", phone="13800000011")
    with SessionLocal() as db:
        task = db.get(ParseTask, task_id)
        task.status = "cancelled"
        db.commit()

    parse_worker.handle_parse(task_id)

    assert env.task(task_id).status == "cancelled"
    assert env.parsed(file_id) is None
    assert fakes.llm.parse_calls == 0


# --------------------------------------------------------------------------- #
# 与 runner 的接线
# --------------------------------------------------------------------------- #
def test_lifespan_registers_parse_handler():
    """lifespan 必须把 `parse` 挂给 runner——否则上传后永远不解析。"""
    from app.main import app
    from app.tasks.runner import runner as singleton

    with TestClient(app):
        assert "parse" in singleton._handlers
