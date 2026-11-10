"""A03 批次档案冻结（F03 / MASTER 执行表 A03 + B 契约 UPD-B-20261107-002845）。

逐条固化以下验收点：
1. 批次**开始时**冻结每位成员的评分输入值拷贝与 B 正式档案版本/内容绑定；
2. 排队期间人工修订、重新解析、字段变更都不影响本批结果输入；
3. retry / 断点补缺与历史回读不读取新版本；
4. 无 B 版本行的历史数据以 `a_baseline` 兜底，仍冻结值拷贝、不阻断创建；
5. 「软删/不存在跳过」「请求级幂等」「岗位/档案变更新建一轮」等既有语义不破坏；
6. 详情接口只回冻结摘要，不外泄全量成员输入；`config_snapshot_json` 不被污染。

假件策略沿用 T-24/T-25 的成熟夹具（LLM/Embedding/Milvus 全替身），造数与清理
自包含，避免依赖其它测试文件的 module-scope 状态。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

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
    TalentProfileVersion,
    TalentTag,
)
from app.service import match_service, screen_service
from app.tasks import match_worker, screen_worker
from app.tasks.match_worker import handle_match_batch
from app.tasks.screen_worker import handle_screen_batch

# 复用 T-24 的成熟假件与登录夹具（它们不造业务数据，跨模块安全）
from tests.test_screen_t24 import auth, client, fakes  # noqa: F401

pytestmark = pytest.mark.integration


DEFAULT_STRUCTURED = {
    "educations": [{"school": "原大学", "major": "计算机"}],
    "employments": [{"company": "原公司", "title": "后端工程师"}],
    "projects": [{"name": "原项目", "role": "开发"}],
    "skills": ["python", "mysql"],
}


# --------------------------------------------------------------------------- #
# 造数与清理
# --------------------------------------------------------------------------- #
@pytest.fixture()
def env():
    job_ids: list[int] = []
    profile_ids: list[int] = []
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
                tags_json=fields.pop("tags_json", ["互联网"]),
                ability_scores_json=fields.pop("ability_scores_json", {"编程能力": {"score": 80}}),
                manual_edited_fields=fields.pop("manual_edited_fields", []),
                current_title=fields.pop("current_title", "后端工程师"),
                expected_title=fields.pop("expected_title", "高级后端工程师"),
                vector_status="synced",
                source="resume",
                **fields,
            )
            db.add(row)
            db.commit()
            profile_ids.append(row.id)
            return row.id

    def make_parsed(profile_id: int, *, structured=None, batch_id: str | None = None) -> int:
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
                    structured_json=json.loads(json.dumps(structured if structured is not None else DEFAULT_STRUCTURED)),
                    raw_text="x",
                )
            )
            db.commit()
            file_ids.append(file_row.id)
            return file_row.id

    def make_version(profile_id: int, version: int = 3, **fields) -> int:
        with SessionLocal() as db:
            row = TalentProfileVersion(
                talent_profile_id=profile_id,
                version=version,
                change_type=fields.pop("change_type", "manual_edit"),
                changed_by=1,
                profile_snapshot_json=fields.pop(
                    "profile_snapshot_json",
                    {
                        "person_id": profile_id,
                        "profile_version": version,
                        "version_source": "persisted",
                        "snapshot_at": "2026-11-10T17:00:00+08:00",
                        "status": "ready",
                        "profile": {"name": "张*", "degree": "本科", "skills": ["python"]},
                        "parsed": {"educations": [{"school": "原大学"}], "projects": []},
                    },
                ),
                **fields,
            )
            db.add(row)
            db.commit()
            return row.id

    yield SimpleNamespace(
        make_job=make_job,
        make_profile=make_profile,
        make_parsed=make_parsed,
        make_version=make_version,
    )

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
            db.execute(
                sa_delete(TalentProfileVersion).where(
                    TalentProfileVersion.talent_profile_id.in_(profile_ids)
                )
            )
            db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
            db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
        if job_ids:
            db.execute(sa_delete(JobProfile).where(JobProfile.id.in_(job_ids)))
        db.commit()


def _scope_for(file_ids) -> dict:
    return {"type": "resume_file_ids", "ids": sorted(file_ids)}


def _match_scope(batch_id: str) -> dict:
    return {"type": "upload_batch", "upload_batch_id": batch_id}


def _screen_create(env, file_ids, *, key=None) -> tuple[int, int]:
    job_id = env.make_job()
    with SessionLocal() as db:
        data = screen_service.create_batch(
            db, job_id, _scope_for(file_ids), user_id=1, idempotency_key=key
        )
    return data["batch_id"], job_id


def _match_create(env, batch_tag: str, *, key=None) -> tuple[int, int]:
    job_id = env.make_job()
    with SessionLocal() as db:
        data = match_service.create_batch(
            db, job_id, _match_scope(batch_tag), user_id=1, idempotency_key=key
        )
    return data["batch_id"], job_id


def _frozen(batch_id: int, model=ScreenBatch) -> dict:
    with SessionLocal() as db:
        db.expire_all()
        return dict(db.get(model, batch_id).frozen_inputs_json or {})


def _config_snapshot(batch_id: int, model=ScreenBatch) -> dict:
    with SessionLocal() as db:
        db.expire_all()
        return dict(db.get(model, batch_id).config_snapshot_json or {})


def _result_inputs(batch_id: int, model=ScreenResult, fk="screen_batch_id") -> dict[int, dict]:
    with SessionLocal() as db:
        rows = db.execute(select(model).where(getattr(model, fk) == batch_id)).scalars().all()
        return {r.talent_profile_id: dict(r.profile_input_snapshot_json or {}) for r in rows}


def _set_frozen_none(batch_id: int, model=ScreenBatch) -> None:
    with SessionLocal() as db:
        db.get(model, batch_id).frozen_inputs_json = None
        db.commit()


# --------------------------------------------------------------------------- #
# A. 创建时冻结：结构、值拷贝、B 正式版本绑定
# --------------------------------------------------------------------------- #
def test_create_freezes_every_resolved_member(env):
    p1, p2 = env.make_profile(), env.make_profile()
    f1, f2 = env.make_parsed(p1), env.make_parsed(p2)
    batch_id, _ = _screen_create(env, [f1, f2])

    frozen = _frozen(batch_id)
    assert frozen["schema"] == "A03-v1"
    assert frozen["captured_at"]
    assert set(frozen["items"]) == {str(p1), str(p2)}
    for tid in (p1, p2):
        item = frozen["items"][str(tid)]
        pi = item["profile_input"]
        assert pi["talent_profile_id"] == tid
        assert pi["degree"] == "本科"
        assert pi["skills_json"] == ["python", "mysql"]
        assert pi["educations"] == DEFAULT_STRUCTURED["educations"]
        assert pi["employments"] == DEFAULT_STRUCTURED["employments"]
        assert pi["projects"] == DEFAULT_STRUCTURED["projects"]


def test_frozen_input_is_value_copy_after_source_edit(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])
    before = json.dumps(_frozen(batch_id), sort_keys=True, ensure_ascii=False)

    with SessionLocal() as db:
        row = db.get(TalentProfile, p)
        row.degree = "博士"
        row.skills_json = ["污染字段"]
        row.current_title = "已改动"
        parsed = db.execute(
            select(ParsedResume).where(ParsedResume.resume_file_id == f)
        ).scalar_one()
        parsed.structured_json = {"educations": [{"school": "改后大学"}]}
        db.commit()

    assert json.dumps(_frozen(batch_id), sort_keys=True, ensure_ascii=False) == before


def test_binding_uses_b_persisted_version(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    env.make_version(p, version=3)
    batch_id, _ = _screen_create(env, [f])

    item = _frozen(batch_id)["items"][str(p)]
    assert item["version_source"] == "b_persisted"
    assert item["profile_version"] == 3
    assert item["snapshot_at"] == "2026-11-10T17:00:00+08:00"
    assert len(item["content_sha256"]) == 64
    assert item["persisted_content"]["profile"]["degree"] == "本科"
    assert item["persisted_content"]["parsed"]["educations"] == [{"school": "原大学"}]


def test_no_b_version_marks_a_baseline_but_still_freezes(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])

    item = _frozen(batch_id)["items"][str(p)]
    assert item["version_source"] == "a_baseline"
    assert item["profile_version"] is None
    assert item["content_sha256"] is None
    assert item["profile_input"]["skills_json"] == ["python", "mysql"]


def test_freeze_hash_matches_persisted_content(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    env.make_version(p, version=7)
    batch_id, _ = _screen_create(env, [f])

    item = _frozen(batch_id)["items"][str(p)]
    expected = hashlib.sha256(
        json.dumps(item["persisted_content"], sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    assert item["content_sha256"] == expected


def test_freeze_helpers_reject_bad_schema_and_return_copy(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])
    frozen = _frozen(batch_id)

    assert screen_service.frozen_profile_input({"schema": "unknown"}, p) is None
    assert screen_service.frozen_profile_input(None, p) is None
    assert screen_service.frozen_profile_input(frozen, 999999) is None

    copy = screen_service.frozen_profile_input(frozen, p)
    copy["skills_json"] = ["篡改"]
    assert screen_service.frozen_profile_input(frozen, p)["skills_json"] == ["python", "mysql"]

    summary = screen_service.frozen_inputs_summary(frozen)
    assert summary["available"] is True
    assert summary["frozen_count"] == 1
    assert "items" not in summary and "profile_input" not in summary
    assert screen_service.frozen_inputs_summary(None)["available"] is False


# --------------------------------------------------------------------------- #
# B. worker 只读冻结输入（A03 核心）
# --------------------------------------------------------------------------- #
def test_queued_profile_edit_does_not_change_result_input(env, fakes):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])
    expected = _frozen(batch_id)["items"][str(p)]["profile_input"]

    with SessionLocal() as db:
        row = db.get(TalentProfile, p)
        row.degree = "博士"
        row.work_years = Decimal("20.0")
        row.skills_json = ["改后技能"]
        row.current_title = "改后职位"
        db.commit()

    handle_screen_batch(batch_id)
    written = _result_inputs(batch_id)
    assert set(written) == {p}
    assert written[p] == expected


def test_queued_reparse_does_not_change_evidence(env, fakes):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])

    # 排队期间「重新解析」：新上传一份原件并写入更新的解析结果。
    # `parsed_resumes.uk_parsed_resume_file` 是一文件一结果，不能同文件再插一行。
    env.make_parsed(
        p,
        structured={
            "educations": [{"school": "新大学"}],
            "employments": [{"company": "新公司"}],
            "projects": [{"name": "新项目"}],
            "skills": ["新技能"],
        },
    )

    handle_screen_batch(batch_id)
    written = _result_inputs(batch_id)[p]
    assert written["educations"] == DEFAULT_STRUCTURED["educations"]
    assert written["projects"] == DEFAULT_STRUCTURED["projects"]


def test_screen_worker_never_calls_legacy_current_input(env, fakes, monkeypatch):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])

    def _must_not_fallback(*args, **kwargs):
        pytest.fail("新批次不应回退读取当前档案（A03 冻结输入缺失）")

    monkeypatch.setattr(screen_worker, "_legacy_current_input", _must_not_fallback)
    handle_screen_batch(batch_id)
    assert set(_result_inputs(batch_id)) == {p}


def test_match_worker_uses_frozen_input(env, fakes):
    tag = f"A03M{uuid4().hex[:14].upper()}"
    p = env.make_profile()
    env.make_parsed(p, batch_id=tag)
    batch_id, _ = _match_create(env, tag)
    expected = _frozen(batch_id, MatchBatch)["items"][str(p)]["profile_input"]

    with SessionLocal() as db:
        row = db.get(TalentProfile, p)
        row.degree = "博士"
        row.skills_json = ["改后技能"]
        db.commit()

    handle_match_batch(batch_id)
    written = _result_inputs(batch_id, MatchResult, "match_batch_id")
    assert set(written) == {p}
    assert written[p] == expected


def test_match_worker_never_calls_legacy_current_input(env, fakes, monkeypatch):
    tag = f"A03N{uuid4().hex[:14].upper()}"
    p = env.make_profile()
    env.make_parsed(p, batch_id=tag)
    batch_id, _ = _match_create(env, tag)

    def _must_not_fallback(*args, **kwargs):
        pytest.fail("新批次不应回退读取当前档案（A03 冻结输入缺失）")

    monkeypatch.setattr(match_worker, "_legacy_current_input", _must_not_fallback)
    handle_match_batch(batch_id)
    assert set(_result_inputs(batch_id, MatchResult, "match_batch_id")) == {p}


def test_retry_fill_gap_uses_same_frozen_input(env, fakes):
    p_a, p_b = env.make_profile(), env.make_profile()
    f_a, f_b = env.make_parsed(p_a), env.make_parsed(p_b)
    batch_id, _ = _screen_create(env, [f_a, f_b])
    expected_a = _frozen(batch_id)["items"][str(p_a)]["profile_input"]

    # 第一轮：A 软删被跳过，只有 B 写结果
    with SessionLocal() as db:
        db.get(TalentProfile, p_a).deleted_at = datetime.now()
        db.commit()
    handle_screen_batch(batch_id)
    assert set(_result_inputs(batch_id)) == {p_b}

    # 取消软删 + 排队期改档案 + 复位为补缺状态，第二轮补 A
    with SessionLocal() as db:
        row = db.get(TalentProfile, p_a)
        row.deleted_at = None
        row.degree = "博士"
        row.skills_json = ["改后技能"]
        batch = db.get(ScreenBatch, batch_id)
        batch.status = "pending"
        db.commit()
    handle_screen_batch(batch_id)

    written = _result_inputs(batch_id)
    assert set(written) == {p_a, p_b}
    assert written[p_a] == expected_a  # 补缺不读新版本


def test_legacy_batch_without_freeze_falls_back_with_warning(env, fakes, caplog):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])
    _set_frozen_none(batch_id)

    with caplog.at_level(logging.WARNING):
        handle_screen_batch(batch_id)
    assert "历史批次兼容" in caplog.text
    assert set(_result_inputs(batch_id)) == {p}


def test_soft_delete_still_skips(env, fakes, client, auth):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])

    with SessionLocal() as db:
        db.get(TalentProfile, p).deleted_at = datetime.now()
        db.commit()
    handle_screen_batch(batch_id)

    with SessionLocal() as db:
        count = db.execute(
            select(func.count()).select_from(ScreenResult).where(ScreenResult.screen_batch_id == batch_id)
        ).scalar_one()
    assert count == 0
    detail = client.get(f"/api/screen/batches/{batch_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["skipped_deleted"] == 1


# --------------------------------------------------------------------------- #
# C. 幂等 / 新建一轮 / 详情摘要 / 快照隔离
# --------------------------------------------------------------------------- #
def test_idempotent_replay_keeps_original_freeze(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    job_id = env.make_job()
    with SessionLocal() as db:
        first = screen_service.create_batch(db, job_id, _scope_for([f]), user_id=1, idempotency_key="a03-repeat")
        first_id = first["batch_id"]

    with SessionLocal() as db:
        db.get(TalentProfile, p).degree = "博士"
        db.commit()
    with SessionLocal() as db:
        replay = screen_service.create_batch(db, job_id, _scope_for([f]), user_id=1, idempotency_key="a03-repeat")

    assert replay["batch_id"] == first_id
    assert _frozen(first_id)["items"][str(p)]["profile_input"]["degree"] == "本科"


def test_new_batch_after_profile_change_freezes_new_values(env):
    p = env.make_profile()
    f = env.make_parsed(p)
    job_id = env.make_job()
    with SessionLocal() as db:
        b1 = screen_service.create_batch(db, job_id, _scope_for([f]), user_id=1)["batch_id"]

    with SessionLocal() as db:
        db.get(TalentProfile, p).degree = "博士"
        db.commit()
    with SessionLocal() as db:
        b2 = screen_service.create_batch(db, job_id, _scope_for([f]), user_id=1)["batch_id"]

    assert b1 != b2
    assert _frozen(b1)["items"][str(p)]["profile_input"]["degree"] == "本科"
    assert _frozen(b2)["items"][str(p)]["profile_input"]["degree"] == "博士"


def test_screen_detail_returns_summary_only(env, client, auth):
    p = env.make_profile()
    f = env.make_parsed(p)
    env.make_version(p, version=2)
    batch_id, _ = _screen_create(env, [f])

    resp = client.get(f"/api/screen/batches/{batch_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["data"]["frozen_inputs"]
    assert summary["available"] is True
    assert summary["frozen_count"] == 1
    assert summary["version_sources"] == {"b_persisted": 1}
    assert "items" not in summary and "profile_input" not in summary
    assert "python" not in json.dumps(summary, ensure_ascii=False)


def test_match_detail_returns_summary_only(env, client, auth):
    tag = f"A03D{uuid4().hex[:14].upper()}"
    p = env.make_profile()
    env.make_parsed(p, batch_id=tag)
    batch_id, _ = _match_create(env, tag)

    resp = client.get(f"/api/match/batches/{batch_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["data"]["frozen_inputs"]
    assert summary["available"] is True and summary["frozen_count"] == 1
    assert "items" not in summary and "profile_input" not in summary


def test_config_snapshot_not_polluted_by_freeze(env, fakes):
    p = env.make_profile()
    f = env.make_parsed(p)
    batch_id, _ = _screen_create(env, [f])

    config = _config_snapshot(batch_id)
    assert "items" not in config and "frozen_inputs_json" not in config
    assert config["pass_threshold"] == 75.0
    assert config["title"] == "高级后端工程师"
    handle_screen_batch(batch_id)  # 引擎按白名单读快照，不被成员数据干扰
    assert set(_result_inputs(batch_id)) == {p}


def test_plain_normalizes_decimal_datetime_and_nested():
    value = {
        "a": Decimal("5.0"),
        "b": date(2026, 11, 10),
        "c": [Decimal("1.5"), {"d": datetime(2026, 11, 10, 17, 0, 0)}],
    }
    plain = screen_service._plain(value)
    assert plain == {
        "a": 5.0,
        "b": "2026-11-10",
        "c": [1.5, {"d": "2026-11-10T17:00:00"}],
    }
    assert not isinstance(plain["a"], Decimal)
