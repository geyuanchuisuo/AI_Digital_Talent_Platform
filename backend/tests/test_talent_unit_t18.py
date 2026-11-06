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
