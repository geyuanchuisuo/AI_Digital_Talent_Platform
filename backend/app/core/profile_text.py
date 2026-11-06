"""画像文本构建 —— embedding 输入的唯一拼装口径（T-05）。

实现依据：`docs/算法与判定规则.md` v1.2 §6.3（原文格式见下），
`docs/架构设计.md` v1.4 §5.3「按 `profile_text_hash` 变化触发 embedding」。

固定格式（缺失字段以空串占位、分隔符固定）：
```
{name}｜{current_title}｜期望:{expected_title}｜{city}
学历:{degree} {work_years}年经验
技能:{skills 归一化逗号分隔}
标签:{tags 逗号分隔}
工作经历:{每段 company-title-职责摘要(截 200 字)}
项目:{每个 name-role-摘要(截 150 字)}
教育:{school-major-degree}
```

**稳定性约定**（保证同内容 hash 可复现，§6.3 最后一条）：
- skills / tags 先去重、再**排序**后拼接（调用方传入的归一化结果顺序不影响 hash）；
- 工作经历按 `start` 倒序（进行中视为最晚）、项目按 `name`、教育按 `start` 倒序排序后拼接；
- 多段之间固定用 `;` 分隔，字段之间固定用 `-`；
- `work_years` 统一保留 1 位小数并去掉尾随 `.0`（`9` / `9.5`）。

职责边界：**只做纯函数拼装 + hash，不查库、不调 LLM/Embedding**。
T-16 的 `profile_service.sync_profile_vector(profile)` 与 T-18 的 PATCH/merge/restore
都必须调用本模块，禁止各自再拼一套文本（否则 hash 口径会分叉）。

⚠️ 公共部分（开发日志 §1.3），修改前需用户确认。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

SEP = "｜"
WORK_DESC_MAX = 200
PROJECT_DESC_MAX = 150
SHORT_DESC_MAX = 200


def _text(value: Any) -> str:
    """None → 空串；其余去首尾空白。"""
    if value is None:
        return ""
    return str(value).strip()


def _years(value: Any) -> str:
    """年限格式化：1 位小数，去掉尾随 `.0`；缺失为空串（§6.3 固定格式）。"""
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001 - 非法值按缺失处理，绝不因格式化抛错
        return ""
    formatted = f"{number:.1f}"
    return formatted[:-2] if formatted.endswith(".0") else formatted


def _unique_sorted(values: Iterable[Any] | None) -> list[str]:
    """去重 + 排序：hash 稳定，且不让调用方顺序影响结果。"""
    if not values:
        return []
    cleaned = {_text(item) for item in values if _text(item)}
    return sorted(cleaned)


def _desc(value: Any, limit: int) -> str:
    return _text(value).replace("\r", " ").replace("\n", " ")[:limit]


def _start_key(item: Mapping[str, Any]) -> str:
    """`start` 排序键：缺失排最后；ISO 字符串可直接倒序比较。"""
    return _text(item.get("start"))


def build_profile_text(
    *,
    name: Any = None,
    current_title: Any = None,
    expected_title: Any = None,
    city: Any = None,
    degree: Any = None,
    work_years: Any = None,
    skills: Sequence[Any] | None = None,
    tags: Sequence[Any] | None = None,
    employments: Sequence[Mapping[str, Any]] | None = None,
    projects: Sequence[Mapping[str, Any]] | None = None,
    educations: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """按 §6.3 拼装画像文本（7 行，行内分隔符固定）。"""
    lines = [
        f"{_text(name)}{SEP}{_text(current_title)}{SEP}期望:{_text(expected_title)}{SEP}{_text(city)}",
        f"学历:{_text(degree)} {_years(work_years)}年经验",
        f"技能:{','.join(_unique_sorted(skills))}",
        f"标签:{','.join(_unique_sorted(tags))}",
        "工作经历:" + ";".join(_employment_line(item) for item in _sorted_employments(employments)),
        "项目:" + ";".join(_project_line(item) for item in _sorted_projects(projects)),
        "教育:" + ";".join(_education_line(item) for item in _sorted_educations(educations)),
    ]
    return "\n".join(lines)


def _sorted_employments(employments: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if not employments:
        return []
    return sorted(employments, key=lambda item: (_start_key(item), _text(item.get("company"))), reverse=True)


def _sorted_projects(projects: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if not projects:
        return []
    return sorted(projects, key=lambda item: _text(item.get("name")))


def _sorted_educations(educations: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if not educations:
        return []
    return sorted(educations, key=lambda item: (_start_key(item), _text(item.get("school"))), reverse=True)


def _employment_line(item: Mapping[str, Any]) -> str:
    return "-".join(
        [
            _text(item.get("company")),
            _text(item.get("title")),
            _desc(item.get("description"), WORK_DESC_MAX),
        ]
    )


def _project_line(item: Mapping[str, Any]) -> str:
    return "-".join(
        [
            _text(item.get("name")),
            _text(item.get("role")),
            _desc(item.get("description"), PROJECT_DESC_MAX),
        ]
    )


def _education_line(item: Mapping[str, Any]) -> str:
    return "-".join([_text(item.get("school")), _text(item.get("major")), _text(item.get("degree"))])


def compute_text_hash(text: str) -> str:
    """§6.3：拼接后计算 SHA-256 存 `profile_text_hash`（64 位小写十六进制）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_profile_text_for_row(
    profile: Any,
    *,
    tags: Iterable[Any] | None = None,
    structured: Mapping[str, Any] | None = None,
) -> str:
    """从 ORM 画像行 + 标签 + ResumeSchema 结构化结果拼文本（rebuild_vectors 用）。

    - 画像自有字段（name/city/degree/work_years/current_title/expected_title/skills）取自
      `talent_profiles`，这是 §6.3 重算触发的核心：改 city/degree/title 也必须变 hash。
    - 经历类（employments/projects/educations）不落画像表，从 `parsed_resumes.structured_json` 聚合。
    - `tags` 传入 `talent_tags` 的 tag 列表；未传时回退画像 `tags_json` 展示快照。
    """
    data = structured or {}
    profile_tags = tags if tags is not None else getattr(profile, "tags_json", None)
    return build_profile_text(
        name=getattr(profile, "name", None),
        current_title=getattr(profile, "current_title", None),
        expected_title=getattr(profile, "expected_title", None),
        city=getattr(profile, "city", None),
        degree=getattr(profile, "degree", None),
        work_years=getattr(profile, "work_years", None),
        skills=getattr(profile, "skills_json", None),
        tags=list(profile_tags or []),
        employments=data.get("employments") or [],
        projects=data.get("projects") or [],
        educations=data.get("educations") or [],
    )
