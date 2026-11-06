"""T-16 L1：画像生成服务单测（假 LLM + 假向量，不连 MySQL/Milvus）。

真实 MySQL 上的幂等/事务/降级覆盖在 `test_profile_integration_t16.py`（L2）。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core import llm_client, milvus_store
from app.core.profile_text import build_profile_text_for_row, compute_text_hash
from app.schemas.profile import AbilityDim, AbilityScores, ProfileLLMOutput, TagItem
from app.service import profile_service as svc


# --------------------------------------------------------------------------- #
# 假件
# --------------------------------------------------------------------------- #
def make_output(tag_count: int = 12, *, skill: str = "python") -> ProfileLLMOutput:
    tags = [TagItem(name=skill, type="skill")]
    tags += [TagItem(name=f"标签{i}", type="general") for i in range(tag_count - 1)]
    return ProfileLLMOutput(
        tags=tags,
        ability_scores=AbilityScores(
            professional=AbilityDim(score=88, basis="主导重构，性能提升 5x"),
            project_experience=AbilityDim(score=80, basis="负责核心项目"),
            education=AbilityDim(score=70, basis="本科计算机"),
            stability=AbilityDim(score=75, basis="两段各 3 年"),
            growth_potential=AbilityDim(score=82, basis="持续跟进新技术"),
        ),
    )


def make_profile(**overrides) -> SimpleNamespace:
    base = dict(
        id=101,
        name="张三",
        current_title="后端工程师",
        expected_title="高级后端",
        city="深圳",
        degree="本科",
        work_years=Decimal("4.0"),
        skills_json=["python", "fastapi"],
        tags_json=["python", "fastapi"],
        profile_text_hash=None,
        vector_status="pending",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# 标量字段
# --------------------------------------------------------------------------- #
def test_build_scalar_fields_normalizes_everything():
    data = {
        "basic": {
            "name": "张三",
            "gender": "男",
            "birth_year": 1996,
            "phone": "+86 138-0000-0000",
            "email": " A@B.COM ",
            "city": "深圳",
        },
        "educations": [{"degree": "本科"}, {"degree": "硕士研究生"}],
        "employments": [
            {"title": "后端工程师", "start": "2020-03", "end": "2023-06"},
            {"title": "架构师", "start": "2023-07", "end": "至今"},
        ],
        "skills": ["Python 3.11", "golang", "Python", " "],
        "expected_title": "高级后端",
    }
    values = svc.build_scalar_fields(data)

    assert values["phone"] == "13800000000"
    assert values["email"] == "a@b.com"
    assert values["degree"] == "硕士"  # 取最高学历，且是六档字面值
    assert values["current_title"] == "架构师"  # 进行中优先
    assert values["skills_json"] == ["go", "python"]  # 归一化 + 去重 + 排序
    assert values["work_years"] > Decimal("3.0")
    assert "source" not in values  # source 只在建行时给，不参与自动更新


def test_build_scalar_fields_falls_back_to_llm_work_years():
    """employments 算不出年限时用 LLM 的 work_years 兜底（算法 §2.4）。"""
    values = svc.build_scalar_fields({"basic": {"name": "李四"}, "work_years": 6.25})
    assert values["work_years"] == Decimal("6.2")  # 保留 1 位（银行家舍入由 quantize 处理）


def test_build_scalar_fields_placeholder_name_and_invalid_values():
    values = svc.build_scalar_fields(
        {
            "basic": {"name": None, "gender": "未知", "birth_year": 1800, "phone": "无"},
            "educations": [],
            "employments": [],
            "skills": ["x" * 300],
        }
    )
    assert values["name"] == svc.DEFAULT_NAME
    assert values["gender"] is None
    assert values["birth_year"] is None
    assert values["phone"] is None
    assert values["degree"] is None  # 无教育段 → None，不是「其他」
    assert values["current_title"] is None
    assert len(values["skills_json"][0]) == 300  # 技能不截断（归一化不猜长度）


# --------------------------------------------------------------------------- #
# Prompt B 输出处理
# --------------------------------------------------------------------------- #
def test_normalize_tag_items_dedup_and_skill_priority():
    pairs = svc.normalize_tag_items(
        [
            TagItem(name="Python 3.11", type="general"),
            TagItem(name="python", type="skill"),
            TagItem(name="  ", type="general"),
            TagItem(name="FastAPI", type="skill"),
        ]
    )
    assert pairs == [("python", "skill"), ("fastapi", "skill")]


def test_normalize_tag_items_caps_at_30():
    tags = [TagItem(name=f"t{i}", type="general") for i in range(40)]
    assert len(svc.normalize_tag_items(tags)) == svc.TAG_MAX


def test_clamp_ability_clamps_and_truncates_basis():
    scores = make_output().ability_scores
    scores.professional = AbilityDim(score=120, basis="长" * 200)
    scores.stability = AbilityDim(score=-5, basis="ok")
    result = svc.clamp_ability(scores)

    assert list(result) == [
        "professional",
        "project_experience",
        "education",
        "stability",
        "growth_potential",
    ]
    assert result["professional"]["score"] == 100
    assert len(result["professional"]["basis"]) == 60
    assert result["stability"]["score"] == 0


def test_call_profile_llm_accepts_short_tags_with_one_retry(monkeypatch):
    calls: list[int] = []
    outputs = [make_output(5), make_output(9), make_output(8)]

    def fake_chat_json(*args, **kwargs):
        calls.append(1)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)

    result = svc.call_profile_llm({"basic": {"name": "张三"}})
    assert len(calls) == 2  # 首次 + 重试 1 次
    assert len(result.tags) == 9  # 仍不足 10 也接受（算法 §6.1 不判失败）


def test_call_profile_llm_no_retry_when_enough(monkeypatch):
    calls: list[int] = []

    def fake_chat_json(*args, **kwargs):
        calls.append(1)
        return make_output(12)

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    result = svc.call_profile_llm({"basic": {"name": "张三"}})
    assert len(calls) == 1
    assert len(result.tags) == 12


def test_call_profile_llm_wraps_llm_error(monkeypatch):
    def boom(*args, **kwargs):
        raise llm_client.LLMError("模型超时")

    monkeypatch.setattr(llm_client, "chat_json", boom)
    with pytest.raises(svc.ProfileError) as excinfo:
        svc.call_profile_llm({"basic": {"name": "张三"}})
    assert excinfo.value.stage == "profile"
    assert "Prompt B" in str(excinfo.value)


def test_prompt_input_truncated(monkeypatch):
    text = svc.build_prompt_input({"blob": "x" * (svc.PROMPT_INPUT_MAX + 100)})
    assert len(text) == svc.PROMPT_INPUT_MAX


# --------------------------------------------------------------------------- #
# sync_profile_vector：hash 触发口径（算法 §6.3）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def vector_spy(monkeypatch):
    calls: list[tuple[int, str]] = []

    def fake_sync(profile_id: int, text: str, *, budget=None, persist_status=True):
        calls.append((profile_id, text))
        return milvus_store.VectorSyncResult(
            ok=True,
            profile_id=profile_id,
            text_hash=compute_text_hash(text),
            vector_status="synced",
            elapsed=0.01,
        )

    monkeypatch.setattr(milvus_store, "sync_vector_by_text", fake_sync)
    return calls


def test_sync_profile_vector_skips_when_hash_unchanged(vector_spy):
    profile = make_profile()
    text = build_profile_text_for_row(profile, tags=["python", "fastapi"], structured={})
    profile.profile_text_hash = compute_text_hash(text)
    profile.vector_status = "synced"

    assert svc.sync_profile_vector(profile, structured={}, tags=["python", "fastapi"]) is None
    assert vector_spy == []  # 零 embedding 调用


def test_sync_profile_vector_reembeds_when_hash_changed(vector_spy):
    profile = make_profile(profile_text_hash="0" * 64, vector_status="synced")
    result = svc.sync_profile_vector(profile, structured={}, tags=["python"])

    assert result is not None and result.ok
    assert len(vector_spy) == 1


def test_sync_profile_vector_reembeds_when_status_not_synced(vector_spy):
    """hash 相同但上次没同步成功（状态 failed）→ 必须重试，否则永远补不回来。"""
    profile = make_profile()
    text = build_profile_text_for_row(profile, tags=["python", "fastapi"], structured={})
    profile.profile_text_hash = compute_text_hash(text)
    profile.vector_status = "failed"

    assert svc.sync_profile_vector(profile, structured={}, tags=["python", "fastapi"]) is not None
    assert len(vector_spy) == 1


def test_sync_profile_vector_force_always_reembeds(vector_spy):
    """`revector` 用 force=True：hash 未变也强制重嵌。"""
    profile = make_profile()
    text = build_profile_text_for_row(profile, tags=["python", "fastapi"], structured={})
    profile.profile_text_hash = compute_text_hash(text)
    profile.vector_status = "synced"

    assert svc.sync_profile_vector(profile, structured={}, tags=["python", "fastapi"], force=True)
    assert len(vector_spy) == 1


def test_sync_profile_vector_hash_reacts_to_city_only(vector_spy):
    """只改 city（非标签/技能字段）也必须触发重嵌——验收点明确要求。"""
    profile = make_profile(vector_status="synced")
    first = build_profile_text_for_row(profile, tags=["python"], structured={})
    profile.profile_text_hash = compute_text_hash(first)

    profile.city = "北京"
    result = svc.sync_profile_vector(profile, structured={}, tags=["python"])
    assert result is not None
    assert len(vector_spy) == 1
