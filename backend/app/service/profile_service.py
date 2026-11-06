"""画像生成服务（T-16）· 结构化解析结果 → 画像 + 标签 + 能力 + 向量。

实现依据：
- `docs/开发计划-todolist.md` T-16
- `docs/算法与判定规则.md` v1.2 §1（学历）、§2（年限）、§3（技能）、§6（标签/五维/画像文本）
- `docs/LLM提示词设计.md` v1.2 Prompt B
- `docs/数据库设计.md` v1.2 §5.1（画像字段与 vector_status 三态）、§5.2（talent_tags）

三条硬口径（改动前先读）：
1. **幂等（D-G / D-I）**：`parsed_resumes.talent_profile_id` 已存在就不建新行；`tags_json` /
   `ability_scores_json` 已落库就跳过对应 LLM 子步骤，**只补缺失**——keep 路径与断点续跑都走这条路。
2. **同事务**：建画像行 + 回填 `parsed_resumes.talent_profile_id` 在一个事务；
   `tags_json` + `talent_tags` 在一个事务。向量是**另一个事务**，失败不回滚已写入的画像/标签。
3. **hash 触发向量**：任何写入路径都先重算画像文本 → 比对 `profile_text_hash` → 变了才
   embedding + upsert（算法 §6.3）。`sync_profile_vector()` 是全项目唯一入口，
   T-18（PATCH/merge/restore/rebuild）必须复用它，**禁止各自再拼文本**。

失败语义：LLM 失败抛 `ProfileError(stage="profile")`（画像标量已提交，可重试）；
向量失败**不抛异常**，由 `ProfileResult.vector.ok=False` 表达，调用方（T-13）
据此把任务落 `failed(stage="vector")`——因为「画像/标签已写、向量没写成」是合法中间态。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import llm_client, milvus_store
from app.core.normalize import (
    calc_work_years,
    highest_degree,
    is_ongoing,
    normalize_email,
    normalize_phone,
    normalize_skills,
    normalize_tag,
    parse_year_month,
    pick_current_title,
)
from app.core.profile_text import build_profile_text_for_row, compute_text_hash
from app.dao import talent_profiles as profile_dao
from app.models import ParsedResume, TalentProfile
from app.schemas.profile import BASIS_MAX_CHARS, ProfileLLMOutput, TagItem

logger = logging.getLogger(__name__)

#: `name` 是 NOT NULL；LLM 允许输出 null（Prompt A 只在无法识别时给 null），
#: 缺名不能让画像建不出来，落占位值并记 warning（不影响任何匹配口径）
DEFAULT_NAME = "未知"

TAG_MIN = 10
TAG_MAX = 30
TAG_RETRY = 1
ABILITY_SCORE_MIN = 0
ABILITY_SCORE_MAX = 100
WORK_YEARS_MAX = Decimal("80")
BIRTH_YEAR_MIN = 1900
PROMPT_INPUT_MAX = 8000  # 全局约束：非简历文本建议 ≤8000

#: API 字段名 → ORM 列名（`manual_edited_fields` 存的是**API 名**，见接口 §6 PATCH）
FIELD_COLUMNS: dict[str, str] = {
    "name": "name",
    "gender": "gender",
    "birth_year": "birth_year",
    "phone": "phone",
    "email": "email",
    "city": "city",
    "degree": "degree",
    "work_years": "work_years",
    "current_title": "current_title",
    "expected_title": "expected_title",
    "skills": "skills_json",
    "tags": "tags_json",
    "ability_scores": "ability_scores_json",
}
_COLUMN_TO_API: dict[str, str] = {column: api for api, column in FIELD_COLUMNS.items()}


class ProfileError(RuntimeError):
    """画像阶段业务失败；`stage` 直接落 `parse_tasks.stage`（profile / vector）。"""

    def __init__(self, message: str, *, stage: str = "profile") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class ProfileResult:
    """一次画像生成的结果；调用方据此决定任务状态。"""

    profile: TalentProfile
    created: bool
    llm_called: bool
    tags_written: bool
    ability_written: bool
    vector: milvus_store.VectorSyncResult | None

    @property
    def vector_skipped(self) -> bool:
        """`True` = 画像文本 hash 未变，未调用 embedding（验收点「hash 未变零 embedding」）。"""
        return self.vector is None

    @property
    def ok(self) -> bool:
        return self.vector is None or self.vector.ok

    @property
    def vector_status(self) -> str:
        return "synced" if self.vector is None else self.vector.vector_status


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def build_profile(
    db: Session,
    parsed: ParsedResume,
    *,
    structured: Mapping[str, Any] | None = None,
    allow_llm: bool = True,
) -> ProfileResult:
    """创建或补齐画像（幂等）。

    - `parsed.talent_profile_id` 已存在 → 复用该行，**不建新行**；
    - 标量字段按 `manual_edited_fields` 跳过人工保护项；
    - `tags_json` / `ability_scores_json` 缺哪个补哪个（一次 LLM 调用，只写缺失的）；
    - 最后按 hash 决定是否重算向量。

    `allow_llm=False` 供「只补向量」的续跑场景（stage=vector）使用：
    此时若仍有缺失的标签/能力，直接抛 `ProfileError`，绝不静默写半成品。
    """
    data = _as_mapping(structured if structured is not None else parsed.structured_json)
    values = build_scalar_fields(data)

    profile = profile_dao.get(db, parsed.talent_profile_id) if parsed.talent_profile_id else None
    created = False
    if profile is None:
        # 建行与回填 parsed_resumes.talent_profile_id 必须同事务（T-13 幂等防护②）
        profile = profile_dao.create(db, source="resume", **values)
        parsed.talent_profile_id = profile.id
        db.flush()
        created = True
        logger.info("简历 %s 新建画像 %s", parsed.resume_file_id, profile.id)
    else:
        protected = set(profile.manual_edited_fields or [])
        allowed = {
            column: value
            for column, value in values.items()
            if _COLUMN_TO_API.get(column, column) not in protected
        }
        changed = profile_dao.apply_fields(db, profile, allowed)
        if changed:
            logger.info("简历 %s 复用画像 %s，更新字段 %s", parsed.resume_file_id, profile.id, changed)
    db.commit()
    db.refresh(profile)

    protected = set(profile.manual_edited_fields or [])
    need_tags = not profile.tags_json and "tags" not in protected
    need_ability = not profile.ability_scores_json and "ability_scores" not in protected

    llm_called = tags_written = ability_written = False
    if need_tags or need_ability:
        if not allow_llm:
            missing = "、".join(
                name for name, need in (("tags", need_tags), ("ability_scores", need_ability)) if need
            )
            raise ProfileError(f"缺 {missing} 且本次不允许调用 LLM（只补向量场景）")
        output = call_profile_llm(data)
        llm_called = True
        if need_tags:
            pairs = normalize_tag_items(output.tags)
            if not pairs:
                raise ProfileError("Prompt B 未返回任何有效标签")
            # tags_json 展示快照 + talent_tags 关联行，**同一事务**
            profile.tags_json = [name for name, _ in pairs]
            profile_dao.replace_tags(db, profile.id, pairs, source="llm")
            tags_written = True
        if need_ability:
            profile.ability_scores_json = clamp_ability(output.ability_scores)
            ability_written = True
        db.commit()
        db.refresh(profile)

    tags = profile_dao.get_tags(db, profile.id) or list(profile.tags_json or [])
    vector = sync_profile_vector(profile, structured=data, tags=tags)

    return ProfileResult(
        profile=profile,
        created=created,
        llm_called=llm_called,
        tags_written=tags_written,
        ability_written=ability_written,
        vector=vector,
    )


def sync_profile_vector(
    profile: TalentProfile,
    *,
    structured: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    budget: float | None = None,
    force: bool = False,
) -> milvus_store.VectorSyncResult | None:
    """**全项目唯一的画像向量同步入口**（解析 / PATCH / merge / restore / rebuild 共用）。

    - 拼文本 → SHA-256 → 与 `profile.profile_text_hash` 比对：
      未变且 `vector_status='synced'` → 直接返回 `None`（零 embedding 调用）；
    - 变了（或上次失败留下旧 hash / 非 synced 状态）→ `sync_vector_by_text()`
      （受 `VECTOR_SYNC_TIMEOUT` 预算约束，失败置 `vector_status='failed'` 且**不写新 hash**，
      所以下次重试一定还会认为"变了"，能自愈）；
    - `force=True` 供 `revector` 强制重嵌（Hash 未变也重算）。

    调用方必须传 `structured`（经历类不落画像表，缺了会少算工作经历/项目/教育三段，
    导致 hash 与解析期不一致）。T-18 用 `load_structured()` 从 `parsed_resumes` 取。
    """
    if structured is None:
        logger.warning("画像 %s 未传 structured，画像文本将缺失经历类内容", profile.id)

    text = build_profile_text_for_row(profile, tags=tags, structured=structured)
    text_hash = compute_text_hash(text)
    if not force and profile.profile_text_hash == text_hash and profile.vector_status == "synced":
        logger.debug("画像 %s 文本 hash 未变（%s），跳过 embedding", profile.id, text_hash[:12])
        return None

    return milvus_store.sync_vector_by_text(profile.id, text, budget=budget)


def load_structured(db: Session, profile_id: int) -> dict[str, Any]:
    """取画像关联的结构化解析结果（重算画像文本用；merge 场景需 T-17 自行聚合多份）。"""
    row = db.execute(
        select(ParsedResume)
        .where(ParsedResume.talent_profile_id == profile_id)
        .order_by(ParsedResume.id)
        .limit(1)
    ).scalar_one_or_none()
    return _as_mapping(row.structured_json) if row is not None else {}


def aggregate_structured(db: Session, profile_id: int) -> dict[str, Any]:
    """聚合画像下**全部** `parsed_resumes` 的经历类数组（merge / 详情页共用）。

    为什么 merge 不能用 `load_structured()`：合并后一个画像对应多份简历，
    只取第一份会让画像文本漏掉新简历的经历，hash 也随之不更新（数据库 §5.3.1 第 4 条）。

    去重键与保留规则（数据库 §5.3.1 第 4 条）：
    - `employments`：`company + title + start`
    - `projects`：`name + role + start`、`educations`：`school + major + degree`、`certificates`：`name`
    - 同一组键有差异时：**`end` 较晚（离今更近）优先**，其次 `description` 更长，
      再其次**后出现的简历优先**（`parsed_resumes.id` 升序，新简历在后）。
    """
    rows = list(
        db.execute(
            select(ParsedResume)
            .where(ParsedResume.talent_profile_id == profile_id)
            .order_by(ParsedResume.id)
        ).scalars().all()
    )

    aggregated: dict[str, list[dict[str, Any]]] = {}
    for key, identity_fields in _AGGREGATE_KEYS.items():
        merged: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in rows:
            for item in _as_list(_as_mapping(row.structured_json).get(key)):
                if not isinstance(item, Mapping):
                    continue
                entry = dict(item)
                identity = tuple(_identity_part(entry.get(field)) for field in identity_fields)
                current = merged.get(identity)
                if current is None or _more_complete(entry, current):
                    merged[identity] = entry
        aggregated[key] = list(merged.values())
    return aggregated


#: 聚合去重键（顺序即输出顺序）；`certificates` 只有证书名
_AGGREGATE_KEYS: dict[str, tuple[str, ...]] = {
    "educations": ("school", "major", "degree"),
    "employments": ("company", "title", "start"),
    "projects": ("name", "role", "start"),
    "certificates": ("name",),
}


def _identity_part(value: Any) -> str:
    return str(value or "").strip().casefold()


def _end_rank(value: Any) -> int:
    """`end` 转可比较的整数：空 = 0，进行中 = 999912（最晚），日期 = YYYYMM。"""
    if is_ongoing(value):
        return 999912
    parsed = parse_year_month(value)
    return parsed[0] * 100 + parsed[1] if parsed else 0


def _more_complete(new: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    """同键两条经历谁更完整：`end` 更晚 > `description` 更长 > 后出现的（新简历）。"""
    new_end, old_end = _end_rank(new.get("end")), _end_rank(old.get("end"))
    if new_end != old_end:
        return new_end > old_end
    return len(str(new.get("description") or "")) >= len(str(old.get("description") or ""))



def build_scalar_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """`structured_json` → `talent_profiles` 标量字段（学历/年限/技能/联系方式已归一化）。"""
    basic = _as_mapping(data.get("basic"))
    employments = _as_list(data.get("employments"))
    educations = _as_list(data.get("educations"))

    name = str(basic.get("name") or "").strip()
    if not name:
        logger.warning("结构化结果缺姓名，画像 name 落占位值 %s", DEFAULT_NAME)
        name = DEFAULT_NAME

    work_years = calc_work_years(employments)
    if work_years is None:
        # 算法 §2.4：employments 算不出时用 LLM 的 work_years 兜底
        work_years = _to_work_years(data.get("work_years"))

    return {
        "name": name[:50],
        "gender": _gender(basic.get("gender")),
        "birth_year": _birth_year(basic.get("birth_year")),
        "phone": normalize_phone(basic.get("phone")),
        "email": normalize_email(basic.get("email")),
        "city": _short(basic.get("city"), 50),
        "degree": highest_degree(educations),
        "work_years": work_years,
        "current_title": _short(pick_current_title(employments), 100),
        "expected_title": _short(data.get("expected_title"), 100),
        "skills_json": normalize_skills(_as_list(data.get("skills"))),
    }


def _gender(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text if text in {"男", "女"} else None


def _birth_year(raw: Any) -> int | None:
    try:
        year = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if BIRTH_YEAR_MIN <= year <= date.today().year:
        return year
    return None


def _short(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _to_work_years(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        years = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if years < 0:
        return None
    return min(years, WORK_YEARS_MAX).quantize(Decimal("0.1"))


# --------------------------------------------------------------------------- #
# LLM 标签 + 五维能力
# --------------------------------------------------------------------------- #
def build_prompt_input(data: Mapping[str, Any]) -> str:
    """Prompt B 的 user 内容：结构化结果 JSON（Prompt B 的输入就是 Prompt A 的输出）。"""
    text = json.dumps(data, ensure_ascii=False, sort_keys=False)
    if len(text) > PROMPT_INPUT_MAX:
        logger.warning("Prompt B 输入 %d 字符超出 %d，已截断", len(text), PROMPT_INPUT_MAX)
        text = text[:PROMPT_INPUT_MAX]
    return text


def call_profile_llm(data: Mapping[str, Any]) -> ProfileLLMOutput:
    """调用 Prompt B；标签不足 10 个时**额外重试 1 次**，仍不足则接受并记 warning（算法 §6.1）。"""
    prompt = build_prompt_input(data)
    try:
        output = llm_client.chat_json(prompt, ProfileLLMOutput, prompt_name="profile")
    except llm_client.LLMError as exc:
        raise ProfileError(f"Prompt B 调用失败：{exc}") from exc
    assert isinstance(output, ProfileLLMOutput)  # schema 已传，chat_json 保证类型

    if _unique_tag_count(output.tags) >= TAG_MIN:
        return output

    logger.warning("Prompt B 返回标签 %d 个（< %d），重试 1 次", len(output.tags), TAG_MIN)
    for _ in range(TAG_RETRY):
        try:
            retry = llm_client.chat_json(prompt, ProfileLLMOutput, prompt_name="profile")
        except llm_client.LLMError as exc:  # 重试失败沿用首次结果，不升级成任务失败
            logger.warning("Prompt B 标签重试失败，沿用首次结果：%s", exc)
            break
        assert isinstance(retry, ProfileLLMOutput)
        if _unique_tag_count(retry.tags) > _unique_tag_count(output.tags):
            output = retry
        if _unique_tag_count(output.tags) >= TAG_MIN:
            break

    if _unique_tag_count(output.tags) < TAG_MIN:
        logger.warning("标签仍不足 %d 个（%d 个），接受并继续（算法 §6.1 不判失败）", TAG_MIN, len(output.tags))
    return output


def _unique_tag_count(tags: Sequence[TagItem]) -> int:
    return len({normalize_tag(item.name, item.type) for item in tags if normalize_tag(item.name, item.type)})


def normalize_tag_items(tags: Sequence[TagItem]) -> list[tuple[str, str]]:
    """标签归一化 + 去重 + 截断到 30 个；同名标签以 `skill` 优先（算法 §6.1）。"""
    pairs: dict[str, str] = {}
    for item in tags:
        name = normalize_tag(item.name, item.type)
        if not name:
            continue
        existing = pairs.get(name)
        if existing is None or (existing != "skill" and item.type == "skill"):
            pairs[name] = item.type
    return list(pairs.items())[:TAG_MAX]


def clamp_ability(scores: Any) -> dict[str, dict[str, Any]]:
    """五维分值 clamp 0–100、basis 截断 60 字（算法 §6.2 / Prompt B）。"""
    result: dict[str, dict[str, Any]] = {}
    for key in ("professional", "project_experience", "education", "stability", "growth_potential"):
        dim = getattr(scores, key)
        score = max(ABILITY_SCORE_MIN, min(ABILITY_SCORE_MAX, int(dim.score)))
        result[key] = {"score": score, "basis": str(dim.basis or "").strip()[:BASIS_MAX_CHARS]}
    return result


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("structured_json 不是合法 JSON，按空对象处理")
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


__all__ = [
    "FIELD_COLUMNS",
    "ProfileError",
    "ProfileResult",
    "aggregate_structured",
    "build_profile",
    "build_prompt_input",
    "build_scalar_fields",
    "call_profile_llm",
    "clamp_ability",
    "load_structured",
    "normalize_tag_items",
    "sync_profile_vector",
]
