"""L1 单元测试 · 画像文本拼装与 hash（T-05）。

对齐 `docs/算法与判定规则.md` v1.2 §6.3：固定 7 行格式、缺失字段空串占位、
skills/tags 排序后拼接、hash 稳定可复现。
"""

from __future__ import annotations

from decimal import Decimal

from app.core.profile_text import (
    PROJECT_DESC_MAX,
    WORK_DESC_MAX,
    build_profile_text,
    build_profile_text_for_row,
    compute_text_hash,
)

BASE = dict(
    name="张三",
    current_title="后端工程师",
    expected_title="高级后端工程师",
    city="上海",
    degree="本科",
    work_years=Decimal("9.0"),
    skills=["Python", "FastAPI"],
    tags=["互联网", "后端"],
    employments=[{"company": "腾讯", "title": "后端工程师", "start": "2017-07", "description": "订单系统"}],
    projects=[{"name": "支付网关", "role": "核心开发", "description": "重构支付链路"}],
    educations=[{"school": "清华大学", "major": "计算机科学与技术", "degree": "本科"}],
)


def test_format_is_seven_fixed_lines():
    lines = build_profile_text(**BASE).split("\n")
    assert len(lines) == 7
    assert lines[0] == "张三｜后端工程师｜期望:高级后端工程师｜上海"
    assert lines[1] == "学历:本科 9年经验"
    assert lines[2] == "技能:FastAPI,Python"
    assert lines[3] == "标签:互联网,后端"
    assert lines[4] == "工作经历:腾讯-后端工程师-订单系统"
    assert lines[5] == "项目:支付网关-核心开发-重构支付链路"
    assert lines[6] == "教育:清华大学-计算机科学与技术-本科"


def test_missing_fields_keep_placeholders():
    lines = build_profile_text(name="李四").split("\n")
    assert lines[0] == "李四｜｜期望:｜"
    assert lines[1] == "学历: 年经验"
    assert lines[2] == "技能:"
    assert lines[5] == "项目:"


def test_skills_and_tags_order_does_not_change_hash():
    a = build_profile_text(**{**BASE, "skills": ["Python", "FastAPI"], "tags": ["互联网", "后端"]})
    b = build_profile_text(**{**BASE, "skills": ["FastAPI", "Python"], "tags": ["后端", "互联网"]})
    assert a == b
    assert compute_text_hash(a) == compute_text_hash(b)


def test_duplicate_skills_are_removed():
    text = build_profile_text(**{**BASE, "skills": ["Python", "Python", "FastAPI"], "tags": []})
    assert "技能:FastAPI,Python" in text


def test_work_years_formatting_is_stable():
    assert "学历:本科 9年经验" in build_profile_text(degree="本科", work_years=9)
    assert "学历:本科 9.5年经验" in build_profile_text(degree="本科", work_years=Decimal("9.5"))
    assert "学历:本科 9年经验" in build_profile_text(degree="本科", work_years="9.0")
    assert "学历:本科 年经验" in build_profile_text(degree="本科", work_years=None)
    assert "学历:本科 年经验" in build_profile_text(degree="本科", work_years="不是数字")


def test_description_truncation_limits():
    long_desc = "字" * 500
    text = build_profile_text(
        employments=[{"company": "A", "title": "B", "start": "2020-01", "description": long_desc}],
        projects=[{"name": "P", "role": "R", "description": long_desc}],
    )
    work_line = [line for line in text.split("\n") if line.startswith("工作经历:")][0]
    project_line = [line for line in text.split("\n") if line.startswith("项目:")][0]
    assert len(work_line) == len("工作经历:A-B-") + WORK_DESC_MAX
    assert len(project_line) == len("项目:P-R-") + PROJECT_DESC_MAX


def test_employments_sorted_by_start_desc():
    text = build_profile_text(
        employments=[
            {"company": "老东家", "title": "初级", "start": "2015-01"},
            {"company": "新东家", "title": "高级", "start": "2021-05"},
            {"company": "进行中", "title": "专家", "start": "2023-01", "end": None},
        ]
    )
    line = [item for item in text.split("\n") if item.startswith("工作经历:")][0]
    assert line.index("进行中") < line.index("新东家") < line.index("老东家")


def test_hash_is_sha256_hex():
    digest = compute_text_hash("abc")
    assert len(digest) == 64
    assert digest == compute_text_hash("abc")
    assert digest != compute_text_hash("abd")


class _Row:
    name = "王五"
    current_title = "数据工程师"
    expected_title = None
    city = "北京"
    degree = "硕士"
    work_years = Decimal("6.5")
    skills_json = ["SQL", "Python"]
    tags_json = ["快照标签"]


def test_build_from_row_uses_profile_fields_and_structured():
    text = build_profile_text_for_row(
        _Row(),
        tags=["真实标签"],
        structured={"employments": [{"company": "字节", "title": "数据工程师", "start": "2019-03"}]},
    )
    assert "王五｜数据工程师｜期望:｜北京" in text
    assert "学历:硕士 6.5年经验" in text
    assert "标签:真实标签" in text
    assert "工作经历:字节-数据工程师-" in text


def test_build_from_row_falls_back_to_tags_json():
    text = build_profile_text_for_row(_Row(), structured=None)
    assert "标签:快照标签" in text
