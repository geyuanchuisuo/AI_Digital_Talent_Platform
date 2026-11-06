"""T-18 L1：画像 CRUD 纯逻辑单元测试（不触任何外部服务）。

覆盖：PATCH 入参归一化与 400 语义（联系方式/学历枚举/年限/能力五维/tag_type）、
标签重建合并模型（tags ∪ skills、source 保留、技能行强制 skill）、tags 变化判定、
有效结论、只读字段的 fields_set 判定。列表过滤 / 事务 / 向量行为在集成测试覆盖。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import BizError
from app.schemas.profile import ABILITY_KEYS, BASIS_MAX_CHARS
from app.schemas.talent import AbilityDimIn, TagEntry, TalentPatchRequest
from app.service import talent_service as svc


def make_request(**kwargs) -> TalentPatchRequest:
    return TalentPatchRequest(**kwargs)


def ability_payload(**overrides) -> dict[str, AbilityDimIn]:
    dims = {key: AbilityDimIn(score=80, basis="ok") for key in ABILITY_KEYS}
    dims.update(overrides)
    return dims


def make_tag_row(tag: str, tag_type: str, source: str) -> SimpleNamespace:
    return SimpleNamespace(tag=tag, tag_type=tag_type, source=source)


# --------------------------------------------------------------------------- #
# PATCH 入参归一化
# --------------------------------------------------------------------------- #
class TestBuildPatchValues:
    def test_name_none_rejected(self):
        payload = make_request(name=None)
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400

    def test_phone_normalized_and_garbage_rejected(self):
        payload = make_request(phone="+86 138-0000-0000")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["phone"] == "13800000000"

        payload = make_request(phone="abc")
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400

        payload = make_request(phone="   ")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["phone"] is None  # 空串 = 清空

    def test_email_normalized_and_garbage_rejected(self):
        payload = make_request(email="Zhang.San@Example.COM")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["email"] == "zhang.san@example.com"

        payload = make_request(email="not-an-email")
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400

    def test_degree_enum_enforced(self):
        payload = make_request(degree=" 本科 ")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["degree"] == "本科"

        payload = make_request(degree="学士")
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400
        assert "硕士" in exc.value.message  # 提示里列出合法值

        payload = make_request(degree=None)
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["degree"] is None

    def test_work_years_quantized(self):
        payload = make_request(work_years=4)
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["work_years"] == Decimal("4.0")

        payload = make_request(work_years=None)
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["work_years"] is None

    def test_optional_text_empty_means_clear(self):
        payload = make_request(city="  ", current_title=" 后端工程师 ")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["city"] is None
        assert values["current_title"] == "后端工程师"

    def test_skills_normalized_sorted_dedup(self):
        payload = make_request(skills=["Python 3.11", "golang", "Golang", " "])
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert values["skills"] == ["go", "python"]

    def test_tags_type_whitelist(self):
        payload = make_request(tags=[TagEntry(tag="x", tag_type="bogus")])
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400

    def test_ability_full_five_dims_clamped(self):
        dims = ability_payload(professional=AbilityDimIn(score=130, basis="x" * 100))
        payload = make_request(ability_scores=dims)
        values = svc._build_patch_values(payload, payload.model_fields_set)
        result = values["ability_scores"]
        assert result["professional"]["score"] == 100
        assert len(result["professional"]["basis"]) == BASIS_MAX_CHARS
        assert set(result) == set(ABILITY_KEYS)

    def test_ability_missing_or_unknown_dim_rejected(self):
        dims = ability_payload()
        dims.pop("stability")
        payload = make_request(ability_scores=dims)
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400
        assert "stability" in exc.value.message

        dims = ability_payload(extra_dim=AbilityDimIn(score=1, basis=""))
        payload = make_request(ability_scores=dims)
        with pytest.raises(BizError) as exc:
            svc._build_patch_values(payload, payload.model_fields_set)
        assert exc.value.code == 400
        assert "extra_dim" in exc.value.message

    def test_only_provided_fields_collected(self):
        payload = make_request(city="深圳")
        values = svc._build_patch_values(payload, payload.model_fields_set)
        assert set(values) == {"city"}


def test_readonly_experience_fields_detected_via_fields_set():
    """经历类字段出现即可被 controller 识别（D-J），且不会泄进 model_dump。"""
    payload = make_request(city="深圳", educations=[{"school": "某大学"}])
    assert "educations" in payload.model_fields_set
    assert "educations" not in payload.model_dump()
    assert set(svc.READONLY_EXPERIENCE_FIELDS) == {
        "educations",
        "employments",
        "projects",
        "certificates",
    }


# --------------------------------------------------------------------------- #
# 标签重建合并模型（tags ∪ skills）
# --------------------------------------------------------------------------- #
class TestBuildTagRows:
    def test_skills_only_edit_keeps_rows_and_syncs_skill_rows(self):
        existing = [
            make_tag_row("python", "skill", "llm"),
            make_tag_row("go", "skill", "manual"),
            make_tag_row("团队协作", "general", "llm"),
        ]
        rows = svc.build_tag_rows(existing, requested=None, skills_final=["python", "rust"])
        assert ("python", "skill", "llm") in rows
        assert ("go", "skill", "manual") not in rows  # 技能被移除 → 技能行删除
        assert ("rust", "skill", "manual") in rows  # 新技能 → 人工来源
        assert ("团队协作", "general", "llm") in rows  # 非技能行保留

    def test_requested_tags_replace_and_keep_source(self):
        existing = [
            make_tag_row("python", "skill", "llm"),
            make_tag_row("团队协作", "general", "manual"),
        ]
        requested = [TagEntry(tag="Python"), TagEntry(tag="沟通")]
        rows = svc.build_tag_rows(existing, requested=requested, skills_final=["python"])
        # 请求名单为准：团队协作被删；python 保留 llm 来源；沟通新增 manual
        assert ("python", "skill", "llm") in rows
        assert ("沟通", "general", "manual") in rows
        assert all(row[0] != "团队协作" for row in rows)

    def test_explicit_type_override_keeps_source(self):
        existing = [make_tag_row("团队协作", "general", "llm")]
        requested = [TagEntry(tag="团队协作", tag_type="industry")]
        rows = svc.build_tag_rows(existing, requested=requested, skills_final=[])
        assert rows == [("团队协作", "industry", "llm")]

    def test_skill_name_forces_skill_type(self):
        existing = [make_tag_row("rust", "general", "llm")]
        requested = [TagEntry(tag="rust")]
        rows = svc.build_tag_rows(existing, requested=requested, skills_final=["Rust"])
        assert rows == [("rust", "skill", "llm")]  # 归一化命中技能 → type 强制 skill，source 保留

    def test_tags_union_skills_when_both_given(self):
        existing: list = []
        requested = [TagEntry(tag="行业洞察")]
        rows = svc.build_tag_rows(existing, requested=requested, skills_final=["golang"])
        assert ("行业洞察", "general", "manual") in rows
        assert ("go", "skill", "manual") in rows

    def test_duplicate_requested_tags_dedup(self):
        rows = svc.build_tag_rows(
            [], requested=[TagEntry(tag="Python", tag_type="skill"), TagEntry(tag="python")],
            skills_final=[],
        )
        assert len(rows) == 1
        assert rows[0] == ("python", "skill", "manual")


class TestTagsChanged:
    def test_same_set_not_changed(self):
        existing = [make_tag_row("python", "skill", "llm")]
        values = {"tags": [TagEntry(tag="Python")]}
        assert svc._tags_changed(existing, values) is False

    def test_different_set_changed(self):
        existing = [make_tag_row("python", "skill", "llm")]
        values = {"tags": [TagEntry(tag="go")]}
        assert svc._tags_changed(existing, values) is True

    def test_explicit_type_change_detected(self):
        existing = [make_tag_row("python", "skill", "llm")]
        values = {"tags": [TagEntry(tag="python", tag_type="general")]}
        assert svc._tags_changed(existing, values) is True

    def test_no_tags_key_not_changed(self):
        assert svc._tags_changed([], {}) is False


# --------------------------------------------------------------------------- #
# 其他纯逻辑
# --------------------------------------------------------------------------- #
def test_effective_verdict_manual_overrides():
    assert svc.effective_verdict("passed", None) == "passed"
    assert svc.effective_verdict("passed", "rejected") == "rejected"


def test_profile_out_shape():
    profile = SimpleNamespace(
        id=1,
        name="张三",
        gender="男",
        birth_year=1996,
        phone="13800000000",
        email="z@x.com",
        city="深圳",
        degree="本科",
        work_years=Decimal("4.0"),
        current_title="后端工程师",
        expected_title="高级后端",
        skills_json=["python"],
        ability_scores_json={"professional": {"score": 85, "basis": "x"}},
        manual_edited_fields=["phone"],
        vector_status="synced",
        created_at=datetime(2026, 11, 5, 12, 0, 0),
        updated_at=datetime(2026, 11, 5, 13, 0, 0),
    )
    out = svc._profile_out(profile, [make_tag_row("python", "skill", "llm")])
    assert out["work_years"] == 4.0
    assert out["tags"] == [{"tag": "python", "tag_type": "skill", "source": "llm"}]
    assert out["manual_edited_fields"] == ["phone"]
    assert out["created_at"] == datetime(2026, 11, 5, 12, 0, 0)


# --------------------------------------------------------------------------- #
# 只读人员快照（B 契约 §5，阶段 1 无版本表/证据表）
# --------------------------------------------------------------------------- #
def make_snapshot_profile(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        name="张三",
        gender="男",
        birth_year=1996,
        phone="13800000000",
        email="zhang.san@example.com",
        city="深圳",
        degree="本科",
        work_years=Decimal("4.0"),
        current_title="后端工程师",
        expected_title="高级后端",
        skills_json=["python"],
        ability_scores_json={"professional": {"score": 85, "basis": "x"}},
        vector_status="synced",
        manual_edited_fields=[],
        source="upload",
        created_at=datetime(2026, 11, 5, 12, 0, 0),
        updated_at=datetime(2026, 11, 5, 13, 0, 0),
        deleted_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_parsed(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        resume_file_id=11,
        talent_profile_id=1,
        structured_json={
            "basic": {"name": "张三", "phone": "13800000000", "email": "zhang.san@example.com"},
            "educations": [{"school": "某大学"}],
            "employments": [{"company": "某公司"}],
            "projects": [{"name": "项目A"}],
            "skills": ["python"],
            "certificates": [],
            "expected_title": "高级后端",
            "work_years": 4,
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_parse_task(**overrides) -> SimpleNamespace:
    base = dict(status="success", stage="vector", truncated=False)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSnapshotMasking:
    def test_name_masked_keeps_first_char(self):
        assert svc._mask_name("张三") == "张*"
        assert svc._mask_name("欧阳修") == "欧**"
        assert svc._mask_name(None) is None
        assert svc._mask_name("") is None
        assert svc._mask_name("张*") == "张*"  # 已脱敏不重复处理

    def test_phone_masked_keeps_prefix_and_suffix(self):
        assert svc._mask_phone("13800000000") == "138****0000"
        assert svc._mask_phone("+86 138-0000-0000") == "138****0000"
        assert svc._mask_phone("12345") == "****"
        assert svc._mask_phone(None) is None
        assert svc._mask_phone("138****0000") == "138****0000"

    def test_email_masked_keeps_domain(self):
        assert svc._mask_email("zhang.san@example.com") == "zh***@example.com"
        assert svc._mask_email("a@b.com") == "a***@b.com"
        assert svc._mask_email("not-an-email") == "***"
        assert svc._mask_email(None) is None

    def test_mask_basic_only_touches_known_fields(self):
        raw = {"name": "张三", "phone": "13800000000", "email": "z@x.com", "city": "深圳"}
        masked = svc._mask_basic(raw)
        assert masked == {"name": "张*", "phone": "138****0000", "email": "z***@x.com", "city": "深圳"}


class TestSnapshotValidation:
    def test_person_ids_dedup_and_positive(self):
        assert svc._normalize_person_ids([3, 1, 3, 2], 3) == [3, 1, 2]
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_person_ids([0], 10)
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_person_ids([True], 10)  # bool 是 int 子类，必须拒绝
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_person_ids("12", 10)

    def test_person_ids_batch_limit(self):
        assert len(svc._normalize_person_ids(list(range(1, 101)), svc.SNAPSHOT_MAX_BATCH)) == 100
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_person_ids(list(range(1, 102)), svc.SNAPSHOT_MAX_BATCH)

    def test_limit_bounds(self):
        assert svc._validate_limit(1, 50) == 1
        assert svc._validate_limit(50, 50) == 50
        for bad in (0, 51, True):
            with pytest.raises(svc.SnapshotValidationError):
                svc._validate_limit(bad, 50)

    def test_cursor_roundtrip_and_invalid(self):
        assert svc._decode_cursor(None) is None
        assert svc._decode_cursor("") is None
        assert svc._decode_cursor(svc._encode_cursor(7)) == 7
        for bad in ("7", "p:abc", "p:0", "p:-1"):
            with pytest.raises(svc.SnapshotValidationError):
                svc._decode_cursor(bad)

    def test_scope_normalization(self):
        assert svc._normalize_scope(None) == svc.AccountScope()
        assert svc._normalize_scope({"role": "hr"}) == svc.AccountScope(role="hr")
        assert svc._normalize_scope({"role": "personal", "person_id": 3}) == svc.AccountScope(
            "personal", 3
        )
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_scope({"role": "personal"})
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_scope({"role": "guest"})
        with pytest.raises(svc.SnapshotValidationError):
            svc._normalize_scope({"role": "hr", "person_id": "3"})

    def test_personal_scope_only_reads_self(self):
        scope = svc.AccountScope(role="personal", person_id=3)
        svc._ensure_scope(scope, [3])
        with pytest.raises(svc.SnapshotScopeError):
            svc._ensure_scope(scope, [3, 4])
        svc._ensure_scope(svc.AccountScope(role="hr"), [1, 2])  # HR 可读全部

    def test_batch_entrypoints_validate_before_db(self):
        # 校验失败应在触库之前抛出；fake db 只记录是否被调用
        calls: list[object] = []

        class FakeDB:
            def execute(self, stmt):  # pragma: no cover - 不应被调用
                calls.append(stmt)
                raise AssertionError("校验失败时不应查库")

        with pytest.raises(svc.SnapshotValidationError):
            svc.get_person_snapshots(FakeDB(), list(range(1, 102)))
        with pytest.raises(svc.SnapshotValidationError):
            svc.get_person_snapshots(FakeDB(), [1], limit=0)
        with pytest.raises(svc.SnapshotScopeError):
            svc.get_person_snapshots(
                FakeDB(), [1, 2], scope=svc.AccountScope(role="personal", person_id=1)
            )
        with pytest.raises(svc.SnapshotValidationError):
            svc.get_person_evidence(FakeDB(), [1], field_paths=["unknown"])
        assert calls == []


class TestSnapshotContent:
    def test_parsed_snapshot_masks_basic(self):
        parsed = make_parsed()
        file_row = SimpleNamespace(id=11, filename="张三简历.pdf")
        out = svc._parsed_snapshot(parsed, file_row)
        assert out is not None
        assert out["filename"] == "张三简历.pdf"
        assert out["basic"]["name"] == "张*"
        assert out["basic"]["phone"] == "138****0000"
        assert out["educations"] == [{"school": "某大学"}]

    def test_parsed_snapshot_none_without_parsed(self):
        assert svc._parsed_snapshot(None, None) is None

    def test_missing_fields_complete_profile(self):
        assert svc._missing_fields(make_snapshot_profile(), make_parsed()) == []

    def test_missing_fields_lists_empty_values_and_parsed(self):
        profile = make_snapshot_profile(
            phone=None, email="", birth_year=None, city=None, degree=None,
            work_years=None, current_title=None, expected_title=None, skills_json=[],
        )
        missing = svc._missing_fields(profile, None)
        assert set(missing) >= {
            "phone", "email", "birth_year", "city", "degree", "work_years",
            "current_title", "expected_title", "skills",
            "educations", "employments", "projects", "parsed",
        }

    def test_field_status_marks_evidence_unavailable(self):
        profile = make_snapshot_profile()
        parsed = make_parsed()
        status = svc._field_status(profile, parsed, [])
        assert all(status[field] == "present" for field in svc.SNAPSHOT_FIELDS)
        assert status["evidence"] == "unavailable"

        status = svc._field_status(profile, None, ["parsed"])
        assert status["educations"] == "missing"
        assert status["phone"] == "present"

    def test_build_snapshot_ready_complete(self):
        snapshot = svc._build_snapshot(
            person_id=1,
            profile=make_snapshot_profile(),
            tag_rows=[make_tag_row("python", "skill", "llm")],
            parsed=make_parsed(),
            resume_file=SimpleNamespace(id=11, filename="张三简历.pdf"),
            parse_task=make_parse_task(),
            requested_version=None,
            snapshot_at="2026-11-07T10:00:00+08:00",
        )
        assert snapshot["status"] == "ready"
        assert snapshot["profile_version"] == 1
        assert snapshot["version_source"] == "baseline"
        assert snapshot["coverage"] == "complete"
        assert snapshot["profile"]["name"] == "张*"
        assert snapshot["profile"]["tags"] == [{"tag": "python", "tag_type": "skill", "source": "llm"}]
        assert snapshot["parse_status"] == "success"
        assert snapshot["evidence_status"] == "unavailable"
        assert snapshot["evidence"] == []
        assert snapshot["missing_fields"] == []

    def test_build_snapshot_truncated_is_partial(self):
        snapshot = svc._build_snapshot(
            person_id=1,
            profile=make_snapshot_profile(),
            tag_rows=[],
            parsed=make_parsed(),
            resume_file=None,
            parse_task=make_parse_task(truncated=True),
            requested_version=1,
            snapshot_at="2026-11-07T10:00:00+08:00",
        )
        assert snapshot["status"] == "ready"
        assert snapshot["coverage"] == "partial"
        assert snapshot["truncated"] is True

    def test_build_snapshot_without_parsed(self):
        snapshot = svc._build_snapshot(
            person_id=1,
            profile=make_snapshot_profile(),
            tag_rows=[],
            parsed=None,
            resume_file=None,
            parse_task=None,
            requested_version=None,
            snapshot_at="2026-11-07T10:00:00+08:00",
        )
        assert snapshot["status"] == "ready"
        assert snapshot["coverage"] == "partial"
        assert snapshot["parsed"] is None
        assert "parsed" in snapshot["missing_fields"]

    def test_build_snapshot_status_branches(self):
        common = dict(
            person_id=1,
            tag_rows=[],
            parsed=None,
            resume_file=None,
            parse_task=None,
            snapshot_at="2026-11-07T10:00:00+08:00",
        )
        missing = svc._build_snapshot(profile=None, requested_version=None, **common)
        assert missing["status"] == "not_found"
        assert missing["profile"] is None

        deleted = svc._build_snapshot(
            profile=make_snapshot_profile(deleted_at=datetime(2026, 11, 6, 12, 0, 0)),
            requested_version=None,
            **common,
        )
        assert deleted["status"] == "deleted"
        assert deleted["reason"] == "档案已删除"

        # 版本不存在优先于画像不存在：A 请求历史版本时不应误报 404 语义
        version = svc._build_snapshot(profile=None, requested_version=2, **common)
        assert version["status"] == "version_not_found"
        assert version["profile_version"] is None
        assert "版本 2 不存在" in version["reason"]
