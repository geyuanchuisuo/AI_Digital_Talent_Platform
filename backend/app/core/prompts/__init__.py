"""提示词集中管理（T-04）。

- `get_prompt(name)` → PromptSpec（含 system 正文、输出 JSON schema、prompt_version）
- `build_messages(user_prompt, ...)` → 防注入包裹后的 OpenAI 兼容 messages
- `SCHEMAS` / `PROMPT_NAMES` / `get_prompt_version()`

模板正文见 parse.txt / profile.txt / match.txt，输出契约见 schemas.py。
⚠️ 公共部分（开发日志 §1.3），修改前需用户确认。
"""

from app.core.prompts.loader import (
    DATA_NOTICE,
    MAX_INPUT_CHARS,
    PROMPT_NAMES,
    PROMPT_VERSION,
    PromptSpec,
    build_messages,
    get_prompt,
    get_prompt_version,
    sanitize_user_input,
    wrap_system,
)
from app.core.prompts.schemas import (
    PROMPT_A_SCHEMA,
    PROMPT_B_SCHEMA,
    PROMPT_C_SCHEMA,
    SCHEMAS,
)

__all__ = [
    "DATA_NOTICE",
    "MAX_INPUT_CHARS",
    "PROMPT_NAMES",
    "PROMPT_VERSION",
    "PROMPT_A_SCHEMA",
    "PROMPT_B_SCHEMA",
    "PROMPT_C_SCHEMA",
    "SCHEMAS",
    "PromptSpec",
    "build_messages",
    "get_prompt",
    "get_prompt_version",
    "sanitize_user_input",
    "wrap_system",
]
