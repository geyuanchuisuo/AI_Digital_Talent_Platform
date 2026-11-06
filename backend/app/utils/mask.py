"""敏感信息脱敏工具（T-01 供请求日志使用；T-02 统一异常体系直接复用）。

验收口径：todolist T-02「日志中 13800000000 显示为 138****0000」。
邮箱口径：保留首字符 + `***` + 末字符，域名不变，例如 `alice@example.com` → `a***e@example.com`。
"""

from __future__ import annotations

import re

# 中国大陆 11 位手机号，前后不能再跟数字，避免误伤长编号
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_phone(number: str) -> str:
    return number[:3] + "****" + number[-4:]


def mask_email(local: str, domain: str) -> str:
    if len(local) <= 1:
        return f"***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def mask_sensitive(text: str) -> str:
    """对任意日志文本中的手机号、邮箱做脱敏，其余内容原样保留。"""
    if not text:
        return text
    masked = _PHONE_RE.sub(lambda m: mask_phone(m.group(1)), text)
    return _EMAIL_RE.sub(lambda m: mask_email(m.group(1), m.group(2)), masked)
