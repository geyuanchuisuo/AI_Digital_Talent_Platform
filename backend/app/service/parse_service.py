"""简历结构化提取（Prompt A 调用 + 语义重试），T-13 的"提取"阶段实现。

职责边界：**只负责「纯文本 → ResumeSchema dict」**，不碰文件、不碰数据库、不碰任务状态。
编排在 `app/tasks/parse_worker.py`，落库在 `app/dao/parsed_resumes.py`。
这样 Prompt A 的重试口径可以单独测，不必起数据库。

两条口径（依据 `docs/LLM提示词设计.md` v1.2 Prompt A）：
1. **失败重试交给适配层**：`chat_json(..., retry=1)` 已处理 JSON 损坏 / Pydantic 校验失败；
   本模块只处理**语义重试**——`employments` 与 `projects` 同时为空 = 疑似没提取到经历，
   换一次调用仍为空才判失败（`ParseError(stage="extract")`）。
2. **不做归一化**：学历/技能/年限/联系方式一律留原文，归一化在 `core/normalize.py`
   （决策 D-N），此处动手会让"LLM 原始输出"与"入库值"难以追溯。

⚠️ 空经历判定是 Prompt A 文档写死的规则，不是本模块的发明：它用来兜"模型把经历段落
整段漏掉"这种质量问题。副作用是**只有教育背景、没有任何工作/项目段的简历会判失败**
（算法上没有这类简历的降级路径）。若产品决定放行，改 `EMPTY_EXPERIENCE_IS_FAILURE`
一处即可，不要在多处散落判断。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core import llm_client
from app.schemas.parse import ResumeSchema

logger = logging.getLogger(__name__)

#: T-30（2026-11-06）：Prompt A 原先不知道「今天几号」，遇到「至今」只能凭印象估年数，
#: 实测会返回 None 或乱估。这里把基准日期作为一条**事实**拼进数据段（不是指令，
#: 与 loader.DATA_NOTICE 的「分隔符内是待处理数据」口径一致），让相对时间有确定锚点。
DATE_HINT = (
    "【解析基准日期】{today}。简历中的「至今」「现在」「当前」等相对时间一律按此日期确定结束时间；"
    "若简历明示「工作年限：X 年」，work_years 优先采用明示值，否则按各段工作经历的起止年月"
    "合并区间折算为年（保留 1 位小数），不要凭印象估计。"
)

STAGE = "extract"
#: 语义重试次数（Prompt A 文档：疑似未提取到经历 → 重试 1 次）
SEMANTIC_RETRY = 1
EMPTY_EXPERIENCE_MESSAGE = "Prompt A 未提取到任何工作或项目经历（已自动重试 1 次）"
#: 是否把"两次都提不出经历"判为失败（见模块 docstring 的副作用说明）
EMPTY_EXPERIENCE_IS_FAILURE = True


class ParseError(RuntimeError):
    """提取阶段业务失败；`stage` 直接落 `parse_tasks.stage`。"""

    def __init__(self, message: str, *, stage: str = STAGE) -> None:
        super().__init__(message)
        self.stage = stage


def extract_structured(text: str) -> dict[str, Any]:
    """简历纯文本 → `structured_json`（Prompt A 原始输出，未归一化）。

    `text` 已由 `extract_service` 归一化并截断到 15000 字符（T-11），这里不再截。
    """
    prompt = text.strip()
    if not prompt:
        raise ParseError("简历文本为空，无法结构化提取")
    prompt = f"{DATE_HINT.format(today=date.today().isoformat())}\n\n{prompt}"

    output = _call(prompt)
    if has_experience(output):
        return to_dict(output)

    for attempt in range(SEMANTIC_RETRY):
        logger.warning(
            "Prompt A 第 %d 次输出无工作/项目经历（employments=0, projects=0），重试",
            attempt + 1,
        )
        retry = _call(prompt)
        if has_experience(retry):
            logger.info("Prompt A 语义重试成功（第 %d 次重试）", attempt + 1)
            return to_dict(retry)
        output = retry

    if EMPTY_EXPERIENCE_IS_FAILURE:
        raise ParseError(EMPTY_EXPERIENCE_MESSAGE)
    logger.warning("Prompt A 两次输出均无经历，按放行口径继续（EMPTY_EXPERIENCE_IS_FAILURE=False）")
    return to_dict(output)


def has_experience(schema: ResumeSchema) -> bool:
    """Prompt A 的"提取到经历"判定：工作段或项目段至少一段。"""
    return bool(schema.employments or schema.projects)


def to_dict(schema: ResumeSchema) -> dict[str, Any]:
    """落库用 dict；`mode="json"` 保证全部是 JSON 原生类型（MySQL JSON 列直接吃）。"""
    return schema.model_dump(mode="json")


def _call(prompt: str) -> ResumeSchema:
    try:
        output = llm_client.chat_json(prompt, ResumeSchema, prompt_name="parse")
    except llm_client.LLMError as exc:
        # 适配层已重试过 1 次；这里不再重试，避免 4 次调用把单任务拖到 8 分钟
        raise ParseError(f"Prompt A 调用失败：{exc}") from exc
    assert isinstance(output, ResumeSchema)  # schema 已传，chat_json 保证类型
    return output


__all__ = [
    "EMPTY_EXPERIENCE_IS_FAILURE",
    "EMPTY_EXPERIENCE_MESSAGE",
    "ParseError",
    "STAGE",
    "extract_structured",
    "has_experience",
    "to_dict",
]
