"""Prompt A/B/C 的输出 JSON schema。

逐字段照抄 `docs/LLM提示词设计.md` v1.2（该文档已与算法 v1.2 §4.3、
数据库 v1.2 §4.3 ResumeSchema / §5.1 ability_scores_json 对齐）。
这里只作为**拼进 system prompt 的结构约束**；实际响应校验由 chat_json 的
Pydantic 模型完成（各业务任务自带契约模型）。
"""

from __future__ import annotations

PROMPT_A_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "basic": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "gender": {"type": ["string", "null"], "enum": ["男", "女", None]},
                "birth_year": {"type": ["integer", "null"], "description": "仅当简历明示出生年月时填写"},
                "phone": {"type": ["string", "null"], "description": "手机号，含区号+86前缀原样返回；规则层再规范化"},
                "email": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"], "description": "当前工作或期望工作城市"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "educations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "school": {"type": ["string", "null"]},
                    "major": {"type": ["string", "null"]},
                    "degree": {
                        "type": ["string", "null"],
                        "enum": ["博士", "硕士", "本科", "大专", "高中中专", "其他", None],
                    },
                    "start": {"type": ["string", "null"], "description": "ISO 8601 日期或 YYYY-MM 或 YYYY"},
                    "end": {"type": ["string", "null"], "description": "同上；在读为 null"},
                },
                "required": ["school", "degree"],
                "additionalProperties": False,
            },
        },
        "employments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "start": {"type": ["string", "null"], "description": "ISO 8601 日期或 YYYY-MM 或 YYYY"},
                    "end": {"type": ["string", "null"], "description": "同上；进行中为 null"},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["company", "title", "start"],
                "additionalProperties": False,
            },
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "候选人显式提到的技能/技术栈/工具，保留原文；归一化由规则层完成",
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "role": {"type": ["string", "null"]},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "certificates": {
            "type": "array",
            "items": {"type": "string", "description": "证书/认证名称，纯文本"},
        },
        "expected_title": {"type": ["string", "null"]},
        "work_years": {"type": ["number", "null"], "description": "累计工作年数，1 位小数；仅作规则层兜底"},
    },
    "required": [
        "basic", "educations", "employments", "skills",
        "projects", "certificates", "expected_title", "work_years",
    ],
    "additionalProperties": False,
}

ABILITY_DIM = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "basis": {"type": "string", "maxLength": 60, "description": "一句话依据，引用简历事实"},
    },
    "required": ["score", "basis"],
    "additionalProperties": False,
}

PROMPT_B_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "标签文本"},
                    "type": {"type": "string", "enum": ["skill", "industry", "level", "general"]},
                },
                "required": ["name", "type"],
            },
            "minItems": 10,
            "maxItems": 30,
        },
        "ability_scores": {
            "type": "object",
            "properties": {
                "professional": {"$ref": "#/definitions/dim"},
                "project_experience": {"$ref": "#/definitions/dim"},
                "education": {"$ref": "#/definitions/dim"},
                "stability": {"$ref": "#/definitions/dim"},
                "growth_potential": {"$ref": "#/definitions/dim"},
            },
            "required": [
                "professional", "project_experience", "education",
                "stability", "growth_potential",
            ],
            "additionalProperties": False,
        },
    },
    "definitions": {"dim": ABILITY_DIM},
    "required": ["tags", "ability_scores"],
    "additionalProperties": False,
}

MATCH_DIM = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "hits": {"type": "array", "items": {"type": "string"}, "description": "该维度的命中项"},
        "misses": {"type": "array", "items": {"type": "string"}, "description": "该维度的缺失项"},
    },
    "required": ["score", "hits", "misses"],
    "additionalProperties": False,
}

PROMPT_C_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "skills": {"$ref": "#/definitions/dim"},
        "experience": {"$ref": "#/definitions/dim"},
        "education": {"$ref": "#/definitions/dim"},
        "others": {"$ref": "#/definitions/dim"},
        "reason": {"type": "string", "maxLength": 200, "description": "150 字以内，简洁中文理由"},
    },
    "definitions": {"dim": MATCH_DIM},
    "required": ["skills", "experience", "education", "others", "reason"],
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict] = {
    "parse": PROMPT_A_SCHEMA,
    "profile": PROMPT_B_SCHEMA,
    "match": PROMPT_C_SCHEMA,
}
