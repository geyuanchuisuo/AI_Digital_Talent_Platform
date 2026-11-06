"""学历 / 年限 / 技能 / 标签归一化（T-16 新增，T-23 扩展）。

实现依据：
- `docs/算法与判定规则.md` v1.2 §1（学历六档 + 排序值）、§2（工作年限区间合并 + 实习过滤）、
  §3（技能归一化 + 别名表）
- `docs/LLM提示词设计.md` v1.2 §技能别名完整清单（§3.3 的唯一权威别名表）
- `docs/数据库设计.md` v1.2 §5.1：`degree` 入库字面值六选一

**职责边界**：纯函数，不查库、不调 LLM/Embedding、不写日志以外的副作用。
T-16 用到的部分（学历、年限、技能、current_title）在此实现；
T-23（match_engine）需要的**技能命中判定**（词边界正则、`\bjava\b` vs `javascript`、
`use_vector` 开关等）**不在本文件**，属 T-23 排期——本文件只提供归一化结果，不提供命中规则。

⚠️ 公共部分（开发日志 §1.3）：改别名表/学历映射前需用户确认，
否则会同时影响解析入库、标签归一化、岗位技能入库三处口径。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# --------------------------------------------------------------------------- #
# 学历（算法 §1）
# --------------------------------------------------------------------------- #
#: 入库字面值，六选一（顺序即展示顺序，不可增删改字面值）
DEGREE_VALUES: tuple[str, ...] = ("博士", "硕士", "本科", "大专", "高中中专", "其他")

#: 排序值；硬门槛比较用排序值，**严禁字符串直接比较**（算法 §1）
DEGREE_RANK: dict[str, int] = {
    "博士": 5,
    "硕士": 4,
    "本科": 3,
    "大专": 2,
    "高中中专": 1,
    "其他": 0,
}

#: 原文关键词 → 归一化值。**先匹配到的先生效**，故顺序敏感：
#: 「博士研究生」必须在「研究生」之前，「硕士研究生」同理。
_DEGREE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("博士", "phd", "doctor", "doctoral"), "博士"),
    (("硕士", "研究生", "master", "mba", "mpa"), "硕士"),
    (("本科", "学士", "bachelor", "undergraduate"), "本科"),
    (("大专", "专科", "高职", "associate"), "大专"),
    (("高中", "中专", "职高", "技校", "中职"), "高中中专"),
)


def degree_rank(degree: Any) -> int:
    """排序值；非枚举值按「其他」= 0（不抛错，避免脏数据打断解析）。"""
    return DEGREE_RANK.get(str(degree or "").strip(), 0)


def normalize_degree(raw: Any) -> str:
    """任意原文 → 六档字面值之一；缺失/无法识别 → `其他`（算法 §1）。

    注意：LLM 已按 Prompt A 输出归一化值，此处是**兜底**——LLM 输出 "硕士研究生"、
    "Bachelor" 这类非枚举值时仍能落成合法字面值，避免枚举校验失败。
    """
    text = _fold(raw)
    if not text:
        return "其他"
    for keywords, normalized in _DEGREE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return normalized
    return "其他"


def highest_degree(educations: Sequence[Mapping[str, Any]] | None) -> str | None:
    """多段教育取最高学历（算法 §1「取最高学历入 degree」）。

    - 无任何教育段 → `None`（**不是** `其他`）：缺失与「无法识别」在硬门槛里
      都要判不达标，但画像展示上前者应为空；
    - 有教育段但全部无法识别 → `其他`。
    """
    if not educations:
        return None
    best: str | None = None
    best_rank = -1
    for item in educations:
        normalized = normalize_degree(item.get("degree") if isinstance(item, Mapping) else None)
        rank = DEGREE_RANK[normalized]
        if rank > best_rank:
            best, best_rank = normalized, rank
    return best


# --------------------------------------------------------------------------- #
# 技能（算法 §3.2 + LLM提示词设计 v1.2 别名完整清单）
# --------------------------------------------------------------------------- #
#: 别名 → 规范名。表体照抄《LLM提示词设计》v1.2，**一期硬编码，不进 sys_settings**
SKILL_ALIASES: dict[str, str] = {
    "golang": "go",
    "go language": "go",
    "js": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "py": "python",
    "python3": "python",
    "springboot": "spring boot",
    "spring-boot": "spring boot",
    "vuejs": "vue",
    "vue.js": "vue",
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node",
    "node.js": "node",
    "cpp": "c++",
    "cplusplus": "c++",
    "c plus plus": "c++",
    "csharp": "c#",
    "postgresql": "postgres",
    "postgres": "postgres",
    "mongo": "mongodb",
    "k8s": "kubernetes",
    "kubernets": "kubernetes",  # 文档原表里的常见拼写错误，一并归一
    "aws": "aws",
    "gcp": "gcp",
    "azure": "azure",
}

#: 规范名本身（避免 "go" → 别名表里又映射走）
_CANONICAL: frozenset[str] = frozenset(SKILL_ALIASES.values())

#: 前缀修饰（算法 §3.1「去常见修饰（精通/熟练/熟悉前缀剥离）」）
_SKILL_PREFIXES: tuple[str, ...] = (
    "精通", "熟练", "熟悉", "掌握", "了解", "会使用", "会", "使用", "擅长",
)

#: 「Python 3.11」「Java 8」这类**空格分隔**的版本尾巴；只对已知技术基名剥离。
#: 不处理无空格的 `python3`（交给别名表）、更不处理 `es6`/`t0` 这种「字母+数字」的合法名字
_VERSION_RE = re.compile(r"^(?P<base>[a-z+#. ]+?)\s+\d+(?:\.\d+)*\+?$")

_WS_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_INTERN_ZH = "实习"
_INTERN_EN_RE = re.compile(r"\bintern(?:ship)?s?\b")


def _fold(value: Any) -> str:
    """全角转半角 + 小写 + 去首尾空白（算法 §2.6 / §3.1 的 title 归一化口径）。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return text.strip().lower()


def normalize_skill(raw: Any) -> str:
    """单个技能 → 规范名（小写、去修饰、查别名表）。

    幂等：`normalize_skill(normalize_skill(x)) == normalize_skill(x)`。
    """
    text = _fold(raw)
    if not text:
        return ""

    # 去前缀修饰（可能叠加，如「熟练掌握」）
    changed = True
    while changed:
        changed = False
        for prefix in _SKILL_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :].strip(" :：,，.、")
                changed = True

    text = _WS_RE.sub(" ", text).strip(" .;；,，、/")

    # 版本号剥离：「python 3.11」→「python」，但「c++11」不剥离（+ 结尾的基名不处理）
    matched = _VERSION_RE.match(text)
    if matched:
        candidate = matched.group("base").strip()
        if candidate and "+" not in candidate:
            text = candidate

    if text in SKILL_ALIASES:
        return SKILL_ALIASES[text]

    compact = text.replace("-", " ").replace("_", " ")
    compact = _WS_RE.sub(" ", compact).strip()
    if compact in SKILL_ALIASES:
        return SKILL_ALIASES[compact]
    if compact in _CANONICAL:
        return compact
    return compact


def normalize_skills(values: Iterable[Any] | None) -> list[str]:
    """批量归一化：去空、去重、**排序**（排序是为了画像文本 hash 稳定，算法 §6.3）。"""
    if not values:
        return []
    cleaned = {normalize_skill(item) for item in values}
    return sorted(item for item in cleaned if item)


def normalize_tag(raw: Any, tag_type: str = "general") -> str:
    """标签归一化：技能标签走 `normalize_skill`，其余仅折叠空白。

    **纯 ASCII 标签（如 `PyTorch`、`FastAPI`、`AI落地` 之外的英文技术词）也走技能归一化**：
    标签过滤是 `talent_tags.tag` 精确匹配，大小写/版本号不一致会让筛选漏结果；
    中文标签保持原样（「团队协作」「LLM微调」不能被拆）。

    `talent_tags.tag` 是 VARCHAR(100)，超长截断（标签不参与词边界匹配，截断只影响展示）。
    """
    if raw is None:
        return ""
    text = _WS_RE.sub(" ", unicodedata.normalize("NFKC", str(raw))).strip()
    if not text:
        return ""
    if tag_type == "skill" or not _CJK_RE.search(text):
        text = normalize_skill(text)
    return text[:100]


# --------------------------------------------------------------------------- #
# 时间与工作年限（算法 §2）
# --------------------------------------------------------------------------- #
_ONGOING_MARKERS = frozenset({"", "至今", "现在", "今", "present", "now", "current", "至今为止"})
_DATE_RE = re.compile(r"^(?P<year>\d{4})(?:[-/.年](?P<month>\d{1,2}))?(?:[-/.月](?P<day>\d{1,2}))?")


def parse_year_month(value: Any, *, is_end: bool = False) -> tuple[int, int] | None:
    """解析 `YYYY` / `YYYY-MM` / `YYYY/MM` / `YYYY年M月` / ISO 日期 → (年, 月)。

    - 只给年份时：起始按 1 月、结束按 12 月（避免「2020-2020」算成 0 个月）；
    - 无法解析返回 `None`（调用方按算法 §2.3 跳过该段并记 warning）。
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    matched = _DATE_RE.match(text)
    if not matched:
        return None
    year = int(matched.group("year"))
    month_raw = matched.group("month")
    if month_raw is None:
        month = 12 if is_end else 1
    else:
        month = int(month_raw)
        if not 1 <= month <= 12:
            return None
    return year, month


def is_ongoing(value: Any) -> bool:
    """空值 / 至今 / present 视为进行中（算法 §2.2）。"""
    return _fold(value) in _ONGOING_MARKERS


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def is_internship_title(title: Any) -> bool:
    """实习判定（算法 §2.6 / 决策 D-F）：中文子串 + 英文**词边界**。

    `internal`、`internet` 必须不命中（`\\bintern(ship)?s?\\b` 排除）；
    不依赖 LLM 的 is_internship 字段（ResumeSchema 里没有）。
    """
    text = _fold(title)
    if not text:
        return False
    # 中文按子串（中文无词边界），但先去掉空格，避免「实 习生」这类排版漏判
    if _INTERN_ZH in text.replace(" ", ""):
        return True
    # 英文必须按词边界：internal / internet 不能命中
    return bool(_INTERN_EN_RE.search(text))


def calc_work_years(
    employments: Sequence[Mapping[str, Any]] | None,
    *,
    today: date | None = None,
) -> Decimal | None:
    """累计工作年限：区间合并 → 总月数 ÷ 12，1 位小数（算法 §2.1）。

    - 实习段跳过（`title` 关键词判定）；
    - 进行中段按 `today` 截止；
    - 起止解析失败的段跳过（不中断）；
    - 无任何有效段 → `None`（由调用方用 LLM 的 `work_years` 兜底，算法 §2.4）。
    """
    if not employments:
        return None
    current = today or date.today()
    current_index = _month_index(current.year, current.month)

    intervals: list[tuple[int, int]] = []
    for item in employments:
        if not isinstance(item, Mapping):
            continue
        if is_internship_title(item.get("title")):
            continue
        start = parse_year_month(item.get("start"))
        if start is None:
            continue
        start_index = _month_index(*start)
        end_raw = item.get("end")
        if is_ongoing(end_raw):
            end_index = current_index + 1  # 含当前月
        else:
            end = parse_year_month(end_raw, is_end=True)
            if end is None:
                continue
            end_index = _month_index(*end) + 1  # 含结束月
        if end_index <= start_index:
            continue
        intervals.append((start_index, end_index))

    if not intervals:
        return None

    intervals.sort()
    total = 0
    cur_start, cur_end = intervals[0]
    for start_index, end_index in intervals[1:]:
        if start_index <= cur_end:  # 重叠或相接 → 合并
            cur_end = max(cur_end, end_index)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start_index, end_index
    total += cur_end - cur_start

    if total <= 0:
        return None
    years = Decimal(total) / Decimal(12)
    return years.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def pick_current_title(employments: Sequence[Mapping[str, Any]] | None) -> str | None:
    """当前职位：取 `end` 最晚的一段 title，**进行中优先**（T-16 口径）。

    并列时取 `start` 更晚的一段；全部无 title → `None`。
    """
    if not employments:
        return None
    best: tuple[int, int, str] | None = None
    for item in employments:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        end_raw = item.get("end")
        if is_ongoing(end_raw):
            end_key = 10**6  # 进行中永远最晚
        else:
            end = parse_year_month(end_raw, is_end=True)
            if end is None:
                end_key = -1
            else:
                end_key = _month_index(*end)
        start = parse_year_month(item.get("start"))
        start_key = _month_index(*start) if start else -1
        candidate = (end_key, start_key, title)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


# --------------------------------------------------------------------------- #
# 联系方式（数据库 §5.1：phone / email 存规范化值；T-17 查重键复用本函数）
# --------------------------------------------------------------------------- #
_NON_PHONE_RE = re.compile(r"[^\d+]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: Any) -> str | None:
    """手机号规范化：去空格/分隔符/括号、去 `+86`/`0086`/`86` 前缀（数据库 §5.1）。

    `138 0000 0000`、`+86 138-0000-0000`、`8613800000000` 全部归一成 `13800000000`，
    保证 T-17 的 phone_match 不会因格式差异漏判。无法提取数字 → `None`。

    裸 `86` 前缀只在总长 13 位（86 + 11 位号码）时剥离，避免误伤以 86 开头的固话。
    """
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if not text:
        return None
    digits = _NON_PHONE_RE.sub("", text)
    if digits.startswith("+86"):
        digits = digits[3:]
    elif digits.startswith("0086"):
        digits = digits[4:]
    elif digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    digits = digits.lstrip("+")
    return digits or None


def normalize_email(raw: Any) -> str | None:
    """邮箱规范化：去空白 + 小写；格式明显不合法 → `None`（不把垃圾串写进查重键）。"""
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    if not text or not _EMAIL_RE.match(text):
        return None
    return text[:100]


# --------------------------------------------------------------------------- #
# 辅助：联系方式规范化留给 T-17（dedup_service），此处不实现
# --------------------------------------------------------------------------- #
__all__ = [
    "DEGREE_RANK",
    "DEGREE_VALUES",
    "SKILL_ALIASES",
    "calc_work_years",
    "degree_rank",
    "highest_degree",
    "is_internship_title",
    "is_ongoing",
    "normalize_degree",
    "normalize_email",
    "normalize_phone",
    "normalize_skill",
    "normalize_skills",
    "normalize_tag",
    "parse_year_month",
    "pick_current_title",
]


# =========================================================================== #
# 技能命中判定（T-23，算法 §3；D-N：只在归一化之上加判定，别名表仍只有一份）
# =========================================================================== #
#: 规范名 → 别名（由 SKILL_ALIASES 反转；规范名自身不含在内）
CANONICAL_ALIASES: dict[str, list[str]] = {}
for _alias, _canon in SKILL_ALIASES.items():
    if _canon != _alias:
        CANONICAL_ALIASES.setdefault(_canon, []).append(_alias)

_ASCII_RE = re.compile(r"[a-z0-9]")

#: 候选人技能元素的尾部修饰（算法 §3.4-c）：版本号、"(3年)"、"/框架"、"开发" 等。
#: 仅当剥掉后剩余部分为 ASCII（技术名）时才剥，避免「软件开发」→「软件」这类 CJK 误伤。
_ELEMENT_PAREN_RE = re.compile(r"[（(][^（）()]*[）)]+\s*$")
_ELEMENT_SLASH_RE = re.compile(r"/[\u4e00-\u9fff]{1,6}\s*$")
_ELEMENT_TAIL_RE = re.compile(
    r"(?:\s*\d+(?:\.\d+)*\+?|/[\u4e00-\u9fff]{1,6}|(?:[\u4e00-\u9fff]{0,4})?(?:开发|框架|引擎|后端|方向))\s*$"
)


def strip_skill_element(raw: Any) -> str:
    """候选人技能元素 → 剥离版本号/修饰后缀后的折叠文本（算法 §3.4-c 前半）。

    例：`Python 3.11`→`python`、`java(3年)`→`java`、`vue/框架`→`vue`、`java开发`→`java`；
    `软件开发` 剥后剩余为 CJK → 不剥（防误伤真实 CJK 技能名）。
    """
    text = _fold(raw)
    if not text:
        return ""
    changed = True
    while changed:
        changed = False
        paren = _ELEMENT_PAREN_RE.sub("", text).strip()
        if paren and paren != text and _ASCII_RE.search(paren):
            text, changed = paren, True
            continue
        stripped = _ELEMENT_TAIL_RE.sub("", text).strip(" -_/、，,")
        if stripped and stripped != text and _ASCII_RE.search(stripped) and not _CJK_RE.search(stripped):
            text, changed = stripped, True
    return _WS_RE.sub(" ", text).strip()


def _job_skill_patterns(canonical: str) -> list[str]:
    """岗位技能（规范名）→ 待匹配文本片段（规范名 + 别名，多词允许分隔符变体）。

    算法 §3.4-b：含 `+ # . -` 与空格的名称先正则转义；多词名允许分隔符变体
    （如 `spring boot` 允许 `spring-boot`/`spring.boot`），单词名直接转义（如 `c++`）。
    """
    pieces = [canonical, *CANONICAL_ALIASES.get(canonical, [])]
    patterns: list[str] = []
    for piece in pieces:
        # 先按空格/连字符切词，再逐词转义，词间用分隔符类连接
        words = [w for w in re.split(r"[\s\-]+", piece) if w]
        if not words:
            continue
        joined = r"[\s._\-]?".join(re.escape(word) for word in words)
        patterns.append(joined)
    return patterns


def _hit_regex(canonical: str) -> re.Pattern[str] | None:
    """岗位技能 → 带边界的单词正则（禁裸子串，D-A）。

    - 起始：前一个字符不得是 `[a-z0-9]`（`nosql` 不命中 `sql`）；
    - 结束：后一个字符不得是 `[a-z0-9]` 或技术续接符 `[-+.#]`
      （`javascript` 不命中 `java`；`react-native` 不命中 `react`；`c++` 不命中 `c`）。
    """
    alternatives = _job_skill_patterns(canonical)
    if not alternatives:
        return None
    body = "|".join(alternatives)
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])(?![-+.#])")


def skill_hit(job_skill: Any, candidate_element: Any) -> bool:
    """单个岗位技能 × 单个候选人技能元素是否命中（算法 §3.4 a→b→c）。

    - a. 两侧归一化后规范名精确相等；
    - b. 岗位技能规范名/别名作为独立词出现在候选人元素中（带边界正则，禁裸子串）；
    - c. 候选人元素剥离版本号/修饰后缀后再按 a 精确比。
    """
    job_canonical = normalize_skill(job_skill)
    if not job_canonical:
        return False

    folded = _fold(candidate_element)
    if not folded:
        return False

    # a：候选元素归一化后与岗位规范名精确相等（别名表覆盖 golang/js/springboot 等）
    if normalize_skill(folded) == job_canonical:
        return True

    # b：独立词命中（词边界 + 技术续接符排除）
    regex = _hit_regex(job_canonical)
    if regex is not None and regex.search(folded):
        return True

    # c：剥后缀再精确比（`Python 3.11`、`java(3年)`、`vue/框架`）
    stripped = strip_skill_element(folded)
    if stripped and stripped != folded and normalize_skill(stripped) == job_canonical:
        return True
    return False


def any_skill_hit(job_skill: Any, candidate_skills: Iterable[Any] | None) -> str | None:
    """岗位技能 × 候选人技能**数组逐元素独立判定**（D-G：禁止 join 成串）。

    任一元素命中即短路返回**命中的原始元素**（供 hits 展示）；全不命中返回 None。
    """
    if not candidate_skills:
        return None
    for element in candidate_skills:
        if skill_hit(job_skill, element):
            return str(element)
    return None


def skill_variant_names(job_skill: Any) -> list[str]:
    """岗位技能的规范名 + 别名（hits 展示 / T-23 用例辅助）。"""
    canonical = normalize_skill(job_skill)
    if not canonical:
        return []
    return [canonical, *CANONICAL_ALIASES.get(canonical, [])]
