"""T-13 L1：Prompt A 契约 + 语义重试 + 断点入口表（不触数据库、不外呼 LLM）。"""

from __future__ import annotations

import pytest

from app.core import llm_client
from app.schemas.parse import ResumeSchema
from app.service import parse_service
from app.tasks import parse_worker


# --------------------------------------------------------------------------- #
# ResumeSchema：宽松但不失真
# --------------------------------------------------------------------------- #
def test_schema_accepts_full_payload():
    data = ResumeSchema.model_validate(
        {
            "basic": {
                "name": "张三",
                "gender": "男",
                "birth_year": 1996,
                "phone": "+86 138-0000-0000",
                "email": "A@B.COM",
                "city": "深圳",
            },
            "educations": [
                {"school": "某大学", "major": "计算机", "degree": "本科", "start": "2014-09", "end": "2018-06"}
            ],
            "employments": [
                {
                    "company": "A 公司",
                    "title": "后端工程师",
                    "start": "2018-07",
                    "end": None,
                    "description": "订单系统",
                }
            ],
            "skills": ["Python", "FastAPI"],
            "projects": [{"name": "订单中台", "role": "负责人"}],
            "certificates": ["CET-6"],
            "expected_title": "高级后端",
            "work_years": 6.5,
        }
    ).model_dump()

    assert data["basic"]["name"] == "张三"
    assert data["employments"][0]["end"] is None
    assert data["work_years"] == 6.5
    assert data["skills"] == ["Python", "FastAPI"]


def test_schema_tolerates_nulls_and_missing_arrays():
    """模型只给 basic（其余字段省略）时不能整份失败——空数组由"经历为空"重试兜住。"""
    data = ResumeSchema.model_validate({"basic": {"name": None}}).model_dump()
    assert data["basic"]["name"] is None
    assert data["educations"] == [] and data["employments"] == [] and data["projects"] == []
    assert data["expected_title"] is None and data["work_years"] is None


def test_schema_ignores_extra_fields():
    """文档要求 additionalProperties=false，但多吐字段不该让整份简历失败。"""
    data = ResumeSchema.model_validate(
        {"basic": {"name": "李四", "current_title": "架构师"}, "salary": "30k"}
    ).model_dump()
    assert "salary" not in data
    assert "current_title" not in data["basic"]


def test_schema_rescues_dirty_scalars():
    """`1996年` / `6.5 年` / 非字符串技能项：能救就救，救不回丢弃，不编造。"""
    data = ResumeSchema.model_validate(
        {
            "basic": {"name": "王五", "birth_year": "1996年"},
            "skills": ["Python", {"name": "Go"}, 3, "", None, ["嵌套"]],
            "certificates": {"name": "PMP"},
            "work_years": "6.5 年",
        }
    ).model_dump()

    assert data["basic"]["birth_year"] == 1996
    assert data["skills"] == ["Python", "Go", "3"]
    assert data["certificates"] == ["PMP"]
    assert data["work_years"] == 6.5


def test_schema_unparsable_work_years_becomes_none():
    data = ResumeSchema.model_validate({"work_years": "三年以上", "basic": {"birth_year": "保密"}})
    assert data.work_years is None
    assert data.basic.birth_year is None


# --------------------------------------------------------------------------- #
# parse_service：语义重试
# --------------------------------------------------------------------------- #
def _schema(employments: int = 1) -> ResumeSchema:
    return ResumeSchema.model_validate(
        {
            "basic": {"name": "张三", "phone": "13800000000"},
            "employments": [
                {"company": f"公司{i}", "title": "后端", "start": "2020-01"} for i in range(employments)
            ],
            "skills": ["Python"],
        }
    )


class FakeLLM:
    """按调用顺序返回预设结果；记录调用次数与 prompt_name。"""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0
        self.prompt_names: list[str | None] = []

    def __call__(self, prompt, schema=None, **kwargs):
        self.calls += 1
        self.prompt_names.append(kwargs.get("prompt_name"))
        item = self.results[min(self.calls, len(self.results)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def test_extract_structured_normal(monkeypatch):
    fake = FakeLLM(_schema())
    monkeypatch.setattr(llm_client, "chat_json", fake)

    data = parse_service.extract_structured("张三 后端工程师")

    assert fake.calls == 1
    assert fake.prompt_names == ["parse"]
    assert data["basic"]["name"] == "张三"
    assert data["employments"][0]["company"] == "公司0"


def test_extract_structured_retries_when_no_experience(monkeypatch):
    """employments 与 projects 同时为空 → 换一次调用，第二次有经历就采用它。"""
    fake = FakeLLM(_schema(employments=0), _schema(employments=2))
    monkeypatch.setattr(llm_client, "chat_json", fake)

    data = parse_service.extract_structured("张三 后端工程师")

    assert fake.calls == 2
    assert len(data["employments"]) == 2


def test_extract_structured_projects_count_as_experience(monkeypatch):
    """只有项目段也算提取到经历，不触发重试。"""
    only_projects = ResumeSchema.model_validate(
        {"basic": {"name": "张三"}, "projects": [{"name": "订单中台"}]}
    )
    fake = FakeLLM(only_projects)
    monkeypatch.setattr(llm_client, "chat_json", fake)

    data = parse_service.extract_structured("张三 项目经历")

    assert fake.calls == 1
    assert data["projects"][0]["name"] == "订单中台"


def test_extract_structured_fails_after_two_empty_attempts(monkeypatch):
    fake = FakeLLM(_schema(employments=0), _schema(employments=0))
    monkeypatch.setattr(llm_client, "chat_json", fake)

    with pytest.raises(parse_service.ParseError) as excinfo:
        parse_service.extract_structured("只有教育背景")

    assert excinfo.value.stage == "extract"
    assert "经历" in str(excinfo.value)
    assert fake.calls == 2


def test_extract_structured_wraps_llm_error(monkeypatch):
    fake = FakeLLM(llm_client.LLMError("模拟：输出无法解析"))
    monkeypatch.setattr(llm_client, "chat_json", fake)

    with pytest.raises(parse_service.ParseError) as excinfo:
        parse_service.extract_structured("张三")

    assert excinfo.value.stage == "extract"
    assert "Prompt A 调用失败" in str(excinfo.value)
    assert fake.calls == 1, "适配层已重试过一次，本层不再叠加"


def test_extract_structured_rejects_blank_text(monkeypatch):
    fake = FakeLLM(_schema())
    monkeypatch.setattr(llm_client, "chat_json", fake)

    with pytest.raises(parse_service.ParseError):
        parse_service.extract_structured("   \n  ")

    assert fake.calls == 0, "空文本不该浪费一次 LLM 调用"


# --------------------------------------------------------------------------- #
# 断点入口表（数据库 §4.2.1）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("stage", "has_profile", "expected"),
    [
        (None, False, parse_worker.ENTRY_EXTRACT),
        (None, True, parse_worker.ENTRY_EXTRACT),  # stage 为空就当从头跑（多跑一次比漏跑安全）
        ("", False, parse_worker.ENTRY_EXTRACT),
        ("extract", False, parse_worker.ENTRY_EXTRACT),
        ("extract", True, parse_worker.ENTRY_EXTRACT),
        ("profile", False, parse_worker.ENTRY_DEDUP),
        ("profile", True, parse_worker.ENTRY_PROFILE),
        ("vector", True, parse_worker.ENTRY_VECTOR),
        ("vector", False, parse_worker.ENTRY_DEDUP),  # 状态被改坏时退回查重，不硬闯
        ("PROFILE", True, parse_worker.ENTRY_PROFILE),  # 大小写不敏感（手工改库容错）
        ("something", True, parse_worker.ENTRY_EXTRACT),  # 未知值保守全跑
    ],
)
def test_resolve_entry_table(stage, has_profile, expected):
    assert parse_worker.resolve_entry(stage, has_profile=has_profile) == expected
