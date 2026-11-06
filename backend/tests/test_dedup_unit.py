"""T-17 L1：查重判定的纯逻辑（不触库、不外呼）。

覆盖：
- 手机号命中集里的 target 选择与 reason 归类（同名 → name_phone_match；异名 → phone_match）；
- 标签并集：master 既有标签保留 type/source，新简历技能补成 skill 标签；
- 邮箱脱敏口径（与 `utils.mask.mask_email` 一致）。
"""

from __future__ import annotations

from app.models import TalentProfile, TalentTag
from app.service import dedup_service as svc


def profile(pid: int, name: str) -> TalentProfile:
    row = TalentProfile(name=name, source="resume", vector_status="synced")
    row.id = pid
    return row


# --------------------------------------------------------------------------- #
# 手机号命中 → target 与 reason
# --------------------------------------------------------------------------- #
def test_classify_same_name_returns_name_phone_match():
    target, same_name = svc._classify([profile(5, "张三")], "张三")
    assert target.id == 5
    assert same_name is True


def test_classify_different_name_returns_phone_match():
    target, same_name = svc._classify([profile(5, "李四")], "张三")
    assert target.id == 5
    assert same_name is False


def test_classify_prefers_same_name_among_candidates():
    candidates = [profile(5, "李四"), profile(9, "张三"), profile(7, "张三")]
    target, same_name = svc._classify(candidates, "张三")
    # 同名者里取最早创建的那张（候选已按 created_at, id 升序）
    assert (target.id, same_name) == (9, True)


def test_classify_without_name_falls_back_to_phone_match():
    target, same_name = svc._classify([profile(5, "李四")], None)
    assert (target.id, same_name) == (5, False)


# --------------------------------------------------------------------------- #
# 标签并集
# --------------------------------------------------------------------------- #
def tag(name: str, tag_type: str = "general", source: str = "llm") -> TalentTag:
    return TalentTag(tag=name, tag_type=tag_type, source=source)


def test_merged_tag_rows_keeps_manual_source_and_adds_skills():
    existing = [tag("python", "skill"), tag("团队管理", "general", "manual")]
    merged = svc._merged_tag_rows(existing, ["python", "go"])

    assert merged == [
        ("python", "skill", "llm"),  # 已存在 → 保留原 type/source
        ("团队管理", "general", "manual"),  # 人工标签的 source 不被覆盖
        ("go", "skill", "llm"),  # 新简历技能补成 skill 标签
    ]


def test_merged_tag_rows_without_existing_tags():
    assert svc._merged_tag_rows([], ["go", "milvus"]) == [
        ("go", "skill", "llm"),
        ("milvus", "skill", "llm"),
    ]


def test_merged_tag_rows_ignores_empty_skill():
    assert svc._merged_tag_rows([], ["", "go"]) == [("go", "skill", "llm")]


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
def test_mask_email_keeps_domain():
    assert svc._mask_email("alice@example.com") == "a***e@example.com"
    assert svc._mask_email("a@b.com") == "***@b.com"


def test_mask_email_falls_back_when_not_an_email():
    assert svc._mask_email("not-an-email") == "not-an-email"
