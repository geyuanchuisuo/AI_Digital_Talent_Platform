"""题库扩充：管理员触发真实模型生成待审核题目（契约 §3 自适应题库供给）。

- 复用 `app/core/llm_client.chat_json`，C 自带 system prompt 与 schema，不改 D 的 prompt 注册表。
- 生成结果一律以 `pending` 入库，必须人工审核通过后才可被自适应选题使用。
- 模型失败由 service 转为 503；不在本地伪造题目。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.llm_client import chat_json

GENERATION_PROMPT_VERSION = "c-question-gen-v1"

SYSTEM_PROMPT = (
    "你是企业岗位笔试题库的出题人。请按给定岗位、考核维度、维度类型、难度和题型出题。"
    "单选题必须有 2–6 个选项，correct_answer 必须与某个选项完全一致；"
    "简答题必须给出 reference_points 与 scoring_guideline，不设选项与标准答案。"
    '只输出 JSON：{"questions": [{"stem": "...", "options": ["..."] 或 null, '
    '"correct_answer": "..." 或 null, "reference_points": "..." 或 null, '
    '"scoring_guideline": "..." 或 null}]}。'
    "题干与选项不得包含提示答案、泄题或与岗位无关的隐私信息。"
)


class GeneratedQuestion(BaseModel):
    stem: str = Field(min_length=1, max_length=2000)
    options: list[str] | None = Field(default=None, max_length=6)
    correct_answer: str | None = None
    reference_points: str | None = None
    scoring_guideline: str | None = None


class GeneratedQuestionList(BaseModel):
    questions: list[GeneratedQuestion]


def default_generate_questions(request: dict[str, Any]) -> list[dict[str, Any]]:
    """调用真实模型生成题目；异常交由 service 记录为 503。"""
    prompt = (
        f"岗位：{(request.get('job_title') or '通用岗位').strip()}\n"
        f"考核维度：{request.get('dimension_name')}\n"
        f"维度类型：{request.get('dimension_type')}（professional=专业，general=通用）\n"
        f"难度：{request.get('difficulty')}（easy/medium/hard）\n"
        f"题型：{request.get('question_type')}（single_choice/short_answer）\n"
        f"数量：{int(request.get('count') or 1)}\n"
        "请输出符合要求的 JSON。"
    )
    result = chat_json(
        prompt, GeneratedQuestionList, system_prompt=SYSTEM_PROMPT, max_tokens=2048
    )
    return [item.model_dump() for item in result.questions]
