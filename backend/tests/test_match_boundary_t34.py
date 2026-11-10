"""T-A34 评分边界反例（V2.6 §5「整合补充」+ M4 验收点，逐条固化）。

本单元不引入新评分公式（A02-2 仍被 D 的 `PROMPT_C_SCHEMA` 契约阻塞），只把
V2.6 §5 已定的边界做成可运行的门禁，并守住本轮两处真实口径修复：

1. 阈值**必须来自管理员配置的批次快照**，不设来源示例默认值；等于分数线归达线；
2. 评分失败**单独显示、不计为不满足条件**；
3. 明确技能词边界（Java≠JavaScript、C≠C++）；
4. 缺失证据标「待核实」，不推断为「没有」；
5. 年龄/性别不参与本版岗位评分；模型自报准确率被输出契约拒绝；
6. 语义相似度不等于业务匹配分；低分/硬条件不符不自动淘汰。

与 `test_match_engine_t23.py` 的关系：T-23 管引擎主路径与算法清单，本文件管
**需求侧边界口径**，用例按 V2.6 §5 逐条命名，便于验收时逐条对照。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core import embedding_client, milvus_store
from app.core.normalize import any_skill_hit, skill_hit
from app.schemas.match import MatchScoreOutput, ScoreDimension
from app.service import match_engine as engine
from app.tasks import screen_worker

pytestmark = pytest.mark.integration

JOB = {
    "title": "高级后端工程师",
    "jd_text": "负责核心服务",
    "requirements_text": "本科 3 年",
    "min_degree": "本科",
    "min_work_years": 3,
    "must_skills": ["python", "mysql"],
    "bonus_skills": ["milvus"],
    "hard_rule_switches": {"degree": True, "work_years": True, "must_skills": True},
    "weights": {"skills": 40, "experience": 30, "education": 15, "others": 15},
    "pass_threshold": 75,
    "pending_threshold": 60,
}

PROFILE = {
    "talent_profile_id": 101,
    "name": "张三",
    "degree": "本科",
    "work_years": 5.0,
    "skills_json": ["python", "mysql", "redis", "milvus"],
    "tags_json": ["后端"],
    "ability_scores_json": {"professional": {"score": 85, "basis": "x"}},
    "experience_summary": "做过订单系统",
    "vector_status": "synced",
}

SNAPSHOT = {"pass_threshold": 75.0, "pending_threshold": 60.0}


def fake_llm_output(**overrides) -> MatchScoreOutput:
    dims = {
        "skills": ScoreDimension(score=80, hits=["python"], misses=["mysql"]),
        "experience": ScoreDimension(score=70, hits=["订单系统"], misses=[]),
        "education": ScoreDimension(score=75, hits=["本科"], misses=[]),
        "others": ScoreDimension(score=60, hits=[], misses=[]),
    }
    dims.update(overrides)
    return MatchScoreOutput(reason="匹配良好，技能基本命中", **dims)


@pytest.fixture()
def fakes(monkeypatch):
    """假件：LLM 固定输出、Milvus 取向量可控、embedding 计数（沿用 T-23 口径）。"""
    calls = {"embed_one": 0, "embed_query": 0, "get_vector": 0}
    store = SimpleNamespace(down=False, profile_vector=[1.0] + [0.0] * 1023)

    def fake_embed_one(text):
        calls["embed_one"] += 1
        return [0.001] * 1024

    def fake_embed_query(text):
        calls["embed_query"] += 1
        return [0.001] * 1024

    def fake_get_vector(profile_id):
        calls["get_vector"] += 1
        if store.down:
            raise milvus_store.MilvusStoreError("Milvus 不可用（测试模拟）")
        return store.profile_vector

    monkeypatch.setattr(embedding_client, "embed_one", fake_embed_one)
    monkeypatch.setattr(embedding_client, "embed_query", fake_embed_query)
    monkeypatch.setattr(milvus_store, "get_profile_vector", fake_get_vector)
    monkeypatch.setattr(engine, "chat_json", lambda prompt, schema, **kwargs: fake_llm_output())
    return SimpleNamespace(calls=calls, store=store, llm=fake_llm_output)


# --------------------------------------------------------------------------- #
# 1. 分数线边界（V2.6 §5「达到阈值（含等于）与低于阈值的判定口径写清楚」）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "score,expected",
    [
        (100.0, "passed"),
        (75.01, "passed"),
        (75.0, "passed"),     # 等于 pass_threshold 归达线
        (74.99, "pending"),
        (60.0, "pending"),    # 等于 pending_threshold
        (59.99, "rejected"),
        (0.0, "rejected"),    # 低分只给参考标签，不淘汰（淘汰语义见第 6 组）
    ],
)
def test_threshold_equality_belongs_to_pass(score, expected):
    assert screen_worker._verdict_for(score, SNAPSHOT) == expected


def test_thresholds_come_from_snapshot_not_example_defaults():
    """管理员把阈值配成 90/80 时必须生效；旧实现硬编码 75/60 会误判。"""
    custom = {"pass_threshold": 90.0, "pending_threshold": 80.0}
    assert screen_worker._verdict_for(85.0, custom) == "pending"
    assert screen_worker._verdict_for(90.0, custom) == "passed"
    assert screen_worker._verdict_for(79.99, custom) == "rejected"


def test_missing_threshold_raises_instead_of_silent_defaults():
    """V2.6 §5「不设置来源示例默认值」：快照缺字段不得静默按 75/60 判定。"""
    with pytest.raises(KeyError):
        screen_worker._verdict_for(75.0, {})
    with pytest.raises(KeyError):
        screen_worker._verdict_for(75.0, {"pass_threshold": 75.0})


def test_batch_rejects_broken_snapshot_before_calling_llm(monkeypatch):
    """批次级早校验：损坏快照在调用模型之前就失败，避免整批无效 LLM 调用。"""
    class _Batch:
        id = 999
        config_snapshot_json = {"title": "缺阈值快照"}
        scope_json = {"resolved_ids": []}

    def _forbidden(*args, **kwargs):
        raise AssertionError("快照缺阈值时不应调用评分引擎")

    monkeypatch.setattr(engine, "evaluate", _forbidden)
    with pytest.raises(ValueError, match="缺少阈值字段"):
        screen_worker._run_batch(object(), _Batch())


# --------------------------------------------------------------------------- #
# 2. 评分失败单独显示（V2.6 §5「评分失败单独显示，不计为不满足条件」）
# --------------------------------------------------------------------------- #
def test_scoring_failure_is_not_counted_as_rejection():
    with pytest.raises(ValueError) as exc:
        screen_worker._verdict_for(None, SNAPSHOT)
    assert "评分失败" in str(exc.value)
    assert "不得写入内部参考标签" in str(exc.value)
    assert "rejected" not in str(exc.value)


# --------------------------------------------------------------------------- #
# 3. 技能词边界（V2.6 §5「避免把 Java 当作 JavaScript、把 C 当作 C++」）
# --------------------------------------------------------------------------- #
def test_skill_word_boundary_counterexamples():
    assert skill_hit("java", "javascript") is False
    assert skill_hit("c", "c++") is False
    assert skill_hit("go", "google") is False
    assert skill_hit("react", "react-native") is False
    assert skill_hit("sql", "nosql") is False
    # 逐元素独立判定，禁止 join 成串造成假命中
    assert any_skill_hit("java", ["javascript", "nosql"]) is None
    assert any_skill_hit("java", ["javascript", "java开发"]) == "java开发"


def test_skill_word_boundary_does_not_break_aliases():
    assert skill_hit("go", "golang") is True
    assert skill_hit("javascript", "js") is True
    assert skill_hit("spring boot", "spring-boot") is True


# --------------------------------------------------------------------------- #
# 4. 缺失证据（V2.6 §5「缺失证据不直接判为不满足」「不得推断为没有」）
# --------------------------------------------------------------------------- #
def test_missing_evidence_is_pending_verification_not_absence():
    gaps, pending = engine.classify_rule_gaps(
        ["学历缺失或无法识别", "工作年限缺失", "技能列表为空"]
    )
    assert gaps == []
    assert len(pending) == 3
    assert all(item.startswith(engine.PENDING_VERIFICATION_PREFIX) for item in pending)
    review = engine.build_rule_review_text(["学历缺失或无法识别"])
    assert "待核实：学历缺失或无法识别" in review
    assert "不是淘汰结论" in review
    assert "不得推断" in review


def test_explicit_mismatch_is_gap_not_pending():
    gaps, pending = engine.classify_rule_gaps(["学历不满足：要求 本科，候选人 大专"])
    assert len(gaps) == 1 and pending == []
    assert gaps[0].startswith(engine.RULE_GAP_PREFIX)


def test_candidate_summary_marks_missing_evidence():
    summary = engine.build_candidate_summary({**PROFILE, "missing_fields": ["employments"]})
    assert "证据缺失" in summary
    assert "不得推断为「没有」" in summary


# --------------------------------------------------------------------------- #
# 5. 受保护属性与模型自报准确率（V2.6 §5「年龄、性别不参与」「不得将模型自报
#    的准确率作为实际准确率」）
# --------------------------------------------------------------------------- #
def test_protected_attributes_are_stripped_from_prompt_view():
    dirty = {
        **PROFILE,
        "age": 987654,
        "gender": "SENSITIVE_GENDER_TOKEN",
        "birthDate": "1998-01-01",
        "phone": "PHONE_TOKEN_000",
        "Email": "MAIL_TOKEN@example.com",
        "id_card": "IDCARD_TOKEN",
    }
    clean = engine.sanitize_profile_for_prompt(dirty)
    for key in ("age", "gender", "birthDate", "phone", "Email", "id_card"):
        assert key not in clean
    assert clean["skills_json"] == PROFILE["skills_json"]
    assert clean["vector_status"] == "synced"  # 允许字段不受影响
    assert dirty["age"] == 987654  # 不改入参


def test_prompt_text_never_contains_protected_values(fakes, monkeypatch):
    captured: dict[str, str] = {}

    def spy(prompt, schema, **kwargs):
        captured["prompt"] = prompt
        return fake_llm_output()

    monkeypatch.setattr(engine, "chat_json", spy)
    engine.evaluate(
        JOB,
        {
            **PROFILE,
            "age": 987654,
            "gender": "SENSITIVE_GENDER_TOKEN",
            "phone": "PHONE_TOKEN_000",
            "birthDate": "BIRTHDATE_TOKEN",
        },
        use_vector=False,
    )
    text = captured["prompt"]
    for token in ("987654", "SENSITIVE_GENDER_TOKEN", "PHONE_TOKEN_000", "BIRTHDATE_TOKEN"):
        assert token not in text
    assert "python" in text  # 允许字段仍在，未被误删
    assert "【岗位条件】" in text


def test_model_reported_accuracy_is_rejected_by_output_contract():
    """`MatchScoreOutput` extra=forbid：模型自报 accuracy 字段会被结构化校验拒绝。"""
    dims = {key: ScoreDimension(score=80) for key in engine.DIM_KEYS}
    with pytest.raises(ValidationError):
        MatchScoreOutput(reason="我自报准确率 99%", accuracy=0.99, **dims)


# --------------------------------------------------------------------------- #
# 6. 语义相似度 ≠ 业务匹配分；低分不自动淘汰（V2.6 §5 整合补充 / A02-1）
# --------------------------------------------------------------------------- #
def test_vector_similarity_is_not_the_business_score(fakes):
    screen = engine.evaluate(JOB, PROFILE, use_vector=False)
    assert screen.score == pytest.approx(73.25)  # 四维加权，与向量无关
    assert screen.dimension_scores["experience"] == 70.0
    assert screen.vector_similarity is None
    assert fakes.calls == {"embed_one": 0, "embed_query": 0, "get_vector": 0}

    job_vector = [1.0] + [0.0] * 1023
    matched = engine.evaluate(JOB, PROFILE, use_vector=True, job_vector=job_vector)
    # 相似度只折算进 experience 维度（0.7×llm + 0.3×sim×100），不直接充当综合分
    assert matched.dimension_scores["experience"] == pytest.approx(79.0)
    assert matched.score == pytest.approx(75.95)
    assert matched.score != pytest.approx(matched.vector_similarity * 100)


def test_low_score_and_hard_gaps_never_auto_eliminate(fakes):
    weak = {**PROFILE, "degree": "大专", "work_years": 1.0, "skills_json": []}
    result = engine.evaluate(JOB, weak, use_vector=False)
    assert result.hard_fail is False
    assert result.hard_fail_reason is None
    assert result.score is not None and result.score > 0
    assert any("不符项" in item or "待核实" in item for item in result.misses)
