"""T-23 L1+L2：匹配/筛选共用评分引擎。

- L1（不触外部服务）：技能命中判定**强制反例/正例清单**（算法 §3.4）、
  硬门槛五类样例、加权合成、COSINE clamp；
- L2（真实逻辑 + 假件 LLM/Embedding/Milvus）：evaluate 全路径、
  **`use_vector=False` 断言 Embedding/Milvus 零调用**、Milvus 停机降级、
  同输入两次评分一致（temperature=0 契约）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import embedding_client, milvus_store
from app.core.errors import BizError
from app.core.normalize import any_skill_hit, skill_hit, skill_variant_names, strip_skill_element
from app.schemas.match import MatchScoreOutput, ScoreDimension
from app.service import match_engine as engine

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# L1：技能命中判定（算法 §3.4 强制清单）
# --------------------------------------------------------------------------- #
def test_skill_hit_mandatory_counterexamples():
    """反例必不命中：java×javascript / c×c++ / go×google / react×react-native / sql×nosql。"""
    assert skill_hit("java", "javascript") is False
    assert skill_hit("c", "c++") is False
    assert skill_hit("go", "google") is False
    assert skill_hit("react", "react-native") is False
    assert skill_hit("sql", "nosql") is False


def test_skill_hit_mandatory_positives():
    """正例必命中：别名表 + 版本号剥离。"""
    assert skill_hit("go", "golang") is True
    assert skill_hit("spring boot", "springboot") is True
    assert skill_hit("spring boot", "spring-boot") is True
    assert skill_hit("javascript", "js") is True
    assert skill_hit("python", "Python 3.11") is True


def test_skill_hit_extra_edges():
    # 真实同名 / 大小写 / 全角
    assert skill_hit("c++", "C＋＋") is True or skill_hit("c++", "C++") is True
    assert skill_hit("react", "React") is True
    # 分隔符变体（多词规范名）
    assert skill_hit("spring boot", "spring.boot") is True
    # 剥后缀（规则 c）
    assert skill_hit("java", "java开发") is True
    assert skill_hit("vue", "vue/框架") is True
    assert skill_hit("python", "python(3年)") is True
    # CJK 技能不被误剥
    assert skill_hit("软件开发", "软件开发") is True
    # join 成串的反面教材：逐元素独立判定（D-G）
    assert any_skill_hit("java", ["javascript"]) is None
    assert any_skill_hit("java", ["javascript", "java"]) == "java"
    assert skill_variant_names("javascript")[0] == "javascript"
    assert "js" in skill_variant_names("javascript")


def test_strip_skill_element():
    assert strip_skill_element("Python 3.11") == "python"
    assert strip_skill_element("java(3年)") == "java"
    assert strip_skill_element("vue/框架") == "vue"
    assert strip_skill_element("软件开发") == "软件开发"  # 剥后非 ASCII → 不剥


def test_hard_gate_five_sample_classes():
    """达标 / 学历不足 / 年限不足 / 技能缺失 / 边缘 五类判定。"""
    job = {
        "title": "高级后端",
        "jd_text": "职责",
        "requirements_text": "要求",
        "min_degree": "本科",
        "min_work_years": 3,
        "must_skills": ["python", "mysql"],
        "bonus_skills": ["milvus"],
        "hard_rule_switches": {"degree": True, "work_years": True, "must_skills": True},
        "weights": {"skills": 40, "experience": 30, "education": 15, "others": 15},
    }

    def profile(**overrides):
        base = {
            "talent_profile_id": 1,
            "degree": "本科",
            "work_years": 5.0,
            "skills_json": ["python", "mysql", "redis", "milvus"],
            "vector_status": "synced",
        }
        base.update(overrides)
        return base

    # 1 达标
    reasons, hits, _ = engine.hard_gate(job, profile())
    assert reasons == []
    assert any("命中必备技能 python" in h for h in hits)
    # bonus 命中进 hits 且不淘汰
    assert any("加分技能" in h for h in hits)

    # 2 学历不足
    reasons, _, misses = engine.hard_gate(job, profile(degree="大专"))
    assert any("学历不满足" in r for r in reasons)
    assert any("学历不满足" in m for m in misses)

    # 3 年限不足
    reasons, _, _ = engine.hard_gate(job, profile(work_years=2.0))
    assert any("工作年限不足" in r for r in reasons)
    # 边缘：恰好等于要求 → 达标
    reasons, _, _ = engine.hard_gate(job, profile(work_years=3.0))
    assert reasons == []

    # 4 技能缺失（全部缺失项列出）
    reasons, _, misses = engine.hard_gate(job, profile(skills_json=["go", "redis"]))
    assert any("必备技能未全部命中" in r for r in reasons)
    assert sum(1 for m in misses if m.startswith("缺失必备技能")) == 2  # python、mysql 都列

    # 边缘：技能列表为空
    reasons, _, _ = engine.hard_gate(job, profile(skills_json=[]))
    assert "技能列表为空" in reasons

    # 学历缺失 / 年限缺失
    reasons, _, _ = engine.hard_gate(job, profile(degree=None))
    assert "学历缺失或无法识别" in reasons
    reasons, _, _ = engine.hard_gate(job, profile(work_years=None))
    assert "工作年限缺失" in reasons

    # 开关关闭 → 对应门槛不生效
    job_off = {**job, "hard_rule_switches": {"degree": False, "work_years": False, "must_skills": False}}
    reasons, _, _ = engine.hard_gate(job_off, profile(degree=None, work_years=None, skills_json=[]))
    assert reasons == []


# --------------------------------------------------------------------------- #
# L2：evaluate 全路径（假件）
# --------------------------------------------------------------------------- #
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
    """假件：LLM 固定输出、Milvus 取向量可控、embedding 计数（供零调用断言）。"""
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
    monkeypatch.setattr(
        engine,
        "chat_json",
        lambda prompt, schema, **kwargs: fake_llm_output(),
    )
    return SimpleNamespace(calls=calls, store=store, llm=fake_llm_output)


def test_evaluate_screen_no_vector_calls_and_score(fakes):
    """筛选路径：分数正确且 Embedding/Milvus **零调用**（决策 D-A）。"""
    result = engine.evaluate(JOB, PROFILE, use_vector=False)

    assert result.hard_fail is False
    assert result.score == pytest.approx(0.4 * 80 + 0.3 * 70 + 0.15 * 75 + 0.15 * 60)  # 73.25
    assert result.dimension_scores["experience"] == 70.0  # 不做向量合成
    assert result.vector_similarity is None and result.vector_degraded is False
    assert fakes.calls == {"embed_one": 0, "embed_query": 0, "get_vector": 0}
    # hits/misses 合并去重（LLM + 规则层 bonus）
    assert any("命中加分技能" in h for h in result.hits)


def test_evaluate_match_with_vector(fakes):
    """匹配路径：相似度 1.0 → experience = 0.7×70 + 0.3×100 = 79。"""
    job_vector = [1.0] + [0.0] * 1023  # 与画像向量同向 → COSINE=1
    result = engine.evaluate(JOB, PROFILE, use_vector=True, job_vector=job_vector)

    assert result.dimension_scores["experience"] == 79.0
    assert result.vector_similarity == pytest.approx(1.0)
    assert result.vector_degraded is False
    assert result.score == pytest.approx(0.4 * 80 + 0.3 * 79 + 0.15 * 75 + 0.15 * 60)  # 75.95
    assert fakes.calls["get_vector"] == 1  # 画像向量只取一次


def test_evaluate_match_degrades_when_milvus_down(fakes):
    """Milvus 停机：降级不失败——experience=llm、sim=None、degraded=True、分数与筛选一致。"""
    fakes.store.down = True
    result = engine.evaluate(JOB, PROFILE, use_vector=True, job_vector=[1.0] + [0.0] * 1023)

    assert result.vector_similarity is None
    assert result.vector_degraded is True
    assert result.dimension_scores["experience"] == 70.0
    assert result.score == pytest.approx(73.25)
    assert "向量依赖不可用" in result.reason


def test_evaluate_match_degrades_when_profile_not_synced(fakes):
    result = engine.evaluate(
        JOB, {**PROFILE, "vector_status": "failed"}, use_vector=True, job_vector=[1.0] + [0.0] * 1023
    )
    assert result.vector_degraded is True
    assert fakes.calls["get_vector"] == 0  # 非 synced 直接降级，不查 Milvus

    result = engine.evaluate(JOB, PROFILE, use_vector=True, job_vector=None)
    assert result.vector_degraded is True  # 岗位向量缺失（如 embedding 挂）→ 降级


def test_evaluate_rule_gaps_do_not_eliminate_candidate(fakes):
    """A02-1（V2.6 §5）：硬条件不符者仍显示、仍评分、不自动淘汰。

    旧口径在此断言 `score=None / score=0 / dimensions={}` 且零 LLM；该行为已被
    需求 §5「硬条件或分数线不符合者仍显示不符项与资料缺口，不自动关闭申请」废除。
    """
    failed_profile = {**PROFILE, "degree": "大专"}

    screen = engine.evaluate(JOB, failed_profile, use_vector=False)
    assert screen.hard_fail is False and screen.hard_fail_reason is None
    assert screen.score is not None and screen.score > 0
    assert screen.dimension_scores  # 确实进了 Prompt C
    assert screen.llm_called is True
    assert any(m.startswith(engine.RULE_GAP_PREFIX) and "学历不满足" in m for m in screen.misses)

    match = engine.evaluate(JOB, failed_profile, use_vector=True, job_vector=[1.0] * 1024)
    assert match.hard_fail is False and match.score is not None and match.score > 0
    assert match.dimension_scores and match.llm_called is True
    assert any(m.startswith(engine.RULE_GAP_PREFIX) and "学历不满足" in m for m in match.misses)


def test_missing_evidence_is_pending_verification(fakes):
    """资料缺失标「待核实」而非「不满足」，且照样评分（V2.6 §5 缺失证据不直接判不满足）。"""
    result = engine.evaluate(
        JOB,
        {**PROFILE, "degree": None, "work_years": None, "skills_json": []},
        use_vector=False,
    )
    assert result.hard_fail is False and result.score is not None and result.score > 0
    assert result.llm_called is True
    pending = [m for m in result.misses if m.startswith(engine.PENDING_VERIFICATION_PREFIX)]
    assert any("学历缺失" in m for m in pending)
    assert any("工作年限缺失" in m for m in pending)
    assert any("技能列表为空" in m for m in pending)
    assert not any(m.startswith(engine.RULE_GAP_PREFIX) for m in result.misses)


def test_rule_review_text_is_passed_to_prompt(fakes, monkeypatch):
    """不符项/待核实清单随 Prompt C 输入交给模型，并明确声明不是淘汰结论。"""
    captured: dict[str, str] = {}

    def spy(prompt, schema, **kwargs):
        captured["prompt"] = prompt
        return fake_llm_output()

    monkeypatch.setattr(engine, "chat_json", spy)
    engine.evaluate(JOB, {**PROFILE, "degree": "大专"}, use_vector=False)

    text = captured["prompt"]
    assert "不符项：学历不满足" in text
    assert "待核实：无" in text
    assert "不是淘汰结论" in text
    assert "不得推断" in text
    assert "【候选人画像摘要】" in text and "【岗位条件】" in text  # 原输入未被覆盖


def test_evaluate_deterministic_same_input(fakes):
    """同输入两次评分一致（temperature=0 契约；假件为确定性实现）。"""
    first = engine.evaluate(JOB, PROFILE, use_vector=False)
    second = engine.evaluate(JOB, PROFILE, use_vector=False)
    assert first.score == second.score
    assert first.dimension_scores == second.dimension_scores
    assert first.hits == second.hits and first.misses == second.misses


def test_cosine_similarity_clamp():
    assert engine.cosine_similarity([1, 0], [1, 0]) == 1.0
    assert engine.cosine_similarity([1, 0], [0, 1]) == 0.0
    assert engine.cosine_similarity([1, 0], [-1, 0]) == 0.0  # 负值 clamp 到 0
    assert engine.cosine_similarity([1, 2], [1, 2, 3]) is None  # 维度不符
    assert engine.cosine_similarity([0, 0], [1, 0]) is None  # 零向量


def test_build_job_vector_text():
    text = engine.build_job_vector_text(JOB)
    assert "高级后端工程师" in text and "必备技能:python,mysql" in text and "加分技能:milvus" in text


def test_llm_failure_propagates(fakes, monkeypatch):
    """LLM 失败原样上抛（worker 决定批次失败/重试），不静默降级。"""
    def broken(prompt, schema, **kwargs):
        raise RuntimeError("LLM 不可用（测试模拟）")

    monkeypatch.setattr(engine, "chat_json", broken)
    with pytest.raises(RuntimeError):
        engine.evaluate(JOB, PROFILE, use_vector=False)


# ---- H-A07 修复（2026-11-07）：经历证据输入补全 ----


def test_candidate_summary_uses_explicit_experience_summary():
    """显式 experience_summary 优先，结构化证据不覆盖它。"""
    summary = engine.build_candidate_summary(PROFILE)
    assert "做过订单系统" in summary
    assert "教育经历" not in summary  # 未提供结构化数据时不生成


def test_candidate_summary_generates_evidence_from_structured_input():
    """worker 新输入（educations/employments/projects）生成经历证据。"""
    profile = {
        "name": "李四",
        "degree": "本科",
        "work_years": 3.0,
        "skills_json": ["java"],
        "current_title": "后端工程师",
        "educations": [
            {"school": "某大学", "major": "计算机", "degree": "本科", "start": "2016", "end": "2020"}
        ],
        "employments": [
            {"company": "某科技", "title": "后端工程师", "start": "2021-03", "end": "至今", "description": "负责订单服务"}
        ],
        "projects": [
            {"name": "订单中台", "role": "核心开发", "start": "2023", "end": "2024", "description": "QPS 提升"}
        ],
    }
    summary = engine.build_candidate_summary(summaryless := dict(profile))
    assert "教育经历" in summary and "某大学" in summary
    assert "工作经历" in summary and "负责订单服务" in summary
    assert "项目经历" in summary and "订单中台" in summary
    assert "当前职位：后端工程师" in summary
    assert summaryless["name"] == "李四"  # 不修改入参


def test_candidate_summary_marks_missing_evidence():
    """原文未解析（missing_fields）时标注缺失，提示按缺失处理。"""
    profile = {"name": "王五", "degree": "大专", "work_years": 1.0, "skills_json": [], "missing_fields": ["resume_parsed"]}
    summary = engine.build_candidate_summary(profile)
    assert "证据缺失" in summary and "resume_parsed" in summary
    assert "不得推断" in summary


def test_candidate_summary_without_any_evidence_has_no_empty_blocks():
    """三类证据全缺且无 experience_summary：不生成空的证据段。"""
    profile = {"name": "赵六", "degree": "本科", "work_years": 2.0, "skills_json": ["python"]}
    summary = engine.build_candidate_summary(profile)
    assert "经历证据" not in summary
    assert "证据缺失" not in summary
