"""T-16 L1：归一化纯函数单测（不触 DB / 不触外部服务）。

覆盖 `docs/算法与判定规则.md` v1.2 §1（学历）、§2（年限 + 实习过滤）、§3（技能别名）
以及数据库 §5.1 的联系方式规范化口径。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.normalize import (
    DEGREE_RANK,
    DEGREE_VALUES,
    calc_work_years,
    degree_rank,
    highest_degree,
    is_internship_title,
    is_ongoing,
    normalize_degree,
    normalize_email,
    normalize_phone,
    normalize_skill,
    normalize_skills,
    normalize_tag,
    parse_year_month,
    pick_current_title,
)


# --------------------------------------------------------------------------- #
# 学历（算法 §1）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("博士", "博士"),
        ("博士研究生", "博士"),
        ("PhD", "博士"),
        ("硕士研究生", "硕士"),  # 「研究生」在「博士」之后匹配，不能串档
        ("研究生", "硕士"),
        ("MBA", "硕士"),
        ("Bachelor", "本科"),
        ("大学本科", "本科"),
        ("专科", "大专"),
        ("高职", "大专"),
        ("高中", "高中中专"),
        ("技校", "高中中专"),
        ("  ", "其他"),
        (None, "其他"),
        ("幼儿园", "其他"),
    ],
)
def test_normalize_degree(raw, expected):
    assert normalize_degree(raw) == expected
    assert expected in DEGREE_VALUES  # 入库字面值必须六选一


def test_degree_rank_locked():
    assert DEGREE_RANK == {"博士": 5, "硕士": 4, "本科": 3, "大专": 2, "高中中专": 1, "其他": 0}
    assert degree_rank("高中") == 0  # 非枚举值（旧变体）不得参与比较
    assert degree_rank(None) == 0


def test_highest_degree_picks_max_rank():
    assert highest_degree([{"degree": "大专"}, {"degree": "本科"}, {"degree": "其他"}]) == "本科"
    assert highest_degree([{"degree": "本科"}, {"degree": "硕士研究生"}]) == "硕士"


def test_highest_degree_empty_is_none_not_other():
    """无教育段 → None（简历没写 ≠ 写了但认不出，硬门槛都要判不达标，展示口径不同）。"""
    assert highest_degree([]) is None
    assert highest_degree(None) is None
    assert highest_degree([{"degree": "幼儿园"}]) == "其他"


# --------------------------------------------------------------------------- #
# 技能（算法 §3.2 + 别名完整清单）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("golang", "go"),
        ("Go language", "go"),
        ("js", "javascript"),
        ("ts", "typescript"),
        ("py", "python"),
        ("python3", "python"),
        ("Python 3.11", "python"),
        ("springboot", "spring boot"),
        ("Spring-Boot", "spring boot"),
        ("vuejs", "vue"),
        ("react.js", "react"),
        ("ReactJS", "react"),
        ("Node.js", "node"),
        ("nodejs", "node"),
        ("cpp", "c++"),
        ("CPlusPlus", "c++"),
        ("C#", "c#"),
        ("csharp", "c#"),
        ("PostgreSQL", "postgres"),
        ("Mongo", "mongodb"),
        ("K8s", "kubernetes"),
        ("Kubernets", "kubernetes"),
        ("熟练掌握 Java", "java"),
        ("精通Python", "python"),
        ("  FastAPI  ", "fastapi"),
    ],
)
def test_normalize_skill(raw, expected):
    assert normalize_skill(raw) == expected


def test_normalize_skill_keeps_react_native_distinct():
    """`react-native` 不能被归一成 `react`，否则 T-23 的反例（react×react-native 不命中）会失守。"""
    assert normalize_skill("React Native") == "react native"


def test_normalize_skill_idempotent_and_empty():
    for raw in ("Golang", "Python 3.11", "Spring-Boot", "  "):
        once = normalize_skill(raw)
        assert normalize_skill(once) == once
    assert normalize_skill(None) == ""
    assert normalize_skill("   ") == ""


def test_normalize_skills_dedup_and_sort():
    """去重 + 排序：画像文本 hash 要可复现（算法 §6.3）。"""
    assert normalize_skills(["Python", "python3", "Golang", "java", " "]) == ["go", "java", "python"]
    assert normalize_skills(None) == []
    assert normalize_skills([]) == []


def test_normalize_tag_skill_vs_general():
    assert normalize_tag("Python 3.11", "skill") == "python"
    assert normalize_tag("  团队  协作 ", "general") == "团队 协作"
    assert normalize_tag(None) == ""


# --------------------------------------------------------------------------- #
# 时间解析与年限（算法 §2）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "is_end", "expected"),
    [
        ("2020", False, (2020, 1)),
        ("2020", True, (2020, 12)),
        ("2020-03", False, (2020, 3)),
        ("2020/3", False, (2020, 3)),
        ("2020年3月", False, (2020, 3)),
        ("2020-03-15", False, (2020, 3)),
        ("2020-13", False, None),
        ("bad", False, None),
        (None, False, None),
    ],
)
def test_parse_year_month(raw, is_end, expected):
    assert parse_year_month(raw, is_end=is_end) == expected


@pytest.mark.parametrize("raw", [None, "", "至今", "现在", "present", "Now", "current"])
def test_is_ongoing(raw):
    assert is_ongoing(raw) is True


def test_is_ongoing_false_for_dates():
    assert is_ongoing("2023-06") is False


def test_calc_work_years_merges_overlaps():
    """两段重叠 2020-03~2022-06 与 2022-01~2023-06 → 合并为 40 个月（3.3 年）。"""
    employments = [
        {"title": "后端", "start": "2020-03", "end": "2022-06"},
        {"title": "后端", "start": "2022-01", "end": "2023-06"},
    ]
    assert calc_work_years(employments, today=date(2024, 1, 1)) == Decimal("3.3")


def test_calc_work_years_ongoing_and_internship_excluded():
    employments = [
        {"title": "实习生", "start": "2019-01", "end": "2020-01"},  # 不计
        {"title": "后端", "start": "2020-03", "end": "2022-06"},  # 28 个月
        {"title": "架构师", "start": "2022-07", "end": "至今"},  # 到 2023-06 = 12 个月
    ]
    assert calc_work_years(employments, today=date(2023, 6, 15)) == Decimal("3.3")


def test_calc_work_years_skips_bad_and_reversed_segments():
    employments = [
        {"title": "A", "start": "bad", "end": "2020"},  # 起始不可解析
        {"title": "B", "start": "2020-06", "end": "2019-01"},  # 倒挂
        {"title": "C", "start": "2021-01", "end": "2021-12"},  # 12 个月
    ]
    assert calc_work_years(employments, today=date(2023, 1, 1)) == Decimal("1.0")


def test_calc_work_years_none_when_nothing_valid():
    assert calc_work_years([]) is None
    assert calc_work_years(None) is None
    assert calc_work_years([{"title": "实习生", "start": "2020-01", "end": "2021-01"}]) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("算法实习生", True),
        ("实 习生", True),
        ("Software Engineering Intern", True),
        ("Internship Program", True),
        ("INTERN", True),
        ("Internal Tools Engineer", False),  # 词边界必须排除
        ("Internet Analyst", False),
        ("正式员工", False),
        ("", False),
        (None, False),
    ],
)
def test_is_internship_title_word_boundary(title, expected):
    assert is_internship_title(title) is expected


def test_pick_current_title_prefers_ongoing():
    employments = [
        {"title": "后端", "start": "2019-01", "end": "2021-01"},
        {"title": "架构师", "start": "2021-02", "end": "2023-01"},
        {"title": "技术专家", "start": "2023-02", "end": "至今"},
    ]
    assert pick_current_title(employments) == "技术专家"


def test_pick_current_title_latest_end_wins_without_ongoing():
    employments = [
        {"title": "早", "start": "2019-01", "end": "2020-12"},
        {"title": "晚", "start": "2019-06", "end": "2021-06"},
    ]
    assert pick_current_title(employments) == "晚"


def test_pick_current_title_none_when_missing():
    assert pick_current_title([]) is None
    assert pick_current_title([{"title": "", "start": "2020-01", "end": "2021-01"}]) is None


# --------------------------------------------------------------------------- #
# 联系方式（数据库 §5.1：存规范化值）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13800000000", "13800000000"),
        ("138 0000 0000", "13800000000"),
        ("+86 138-0000-0000", "13800000000"),
        ("0086 13800000000", "13800000000"),
        ("8613800000000", "13800000000"),
        ("010-12345678", "01012345678"),  # 固话保留区号 0，不做 86 剥离
        ("", None),
        (None, None),
        ("无", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_email():
    assert normalize_email("  A@B.COM ") == "a@b.com"
    assert normalize_email("bad-email") is None
    assert normalize_email(None) is None
