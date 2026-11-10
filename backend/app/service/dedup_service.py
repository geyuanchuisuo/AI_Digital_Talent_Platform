"""身份查重与「合并 / 保留」处理（T-17）。

实现依据：
- `docs/开发计划-todolist.md` T-17、需求文档 v1.5 F2-2
- `docs/数据库设计.md` v1.2 §5.3 / §5.3.1（merge）/ §5.3.2（keep）、§4.2.1（状态机）
- `docs/接口设计.md` v1.3 §5
- `docs/架构设计.md` v1.4 §6.1（时序）、§5.4 幂等防护

四条硬口径（改动前先读）：
1. **查重键只有一个来源**：`core.normalize.normalize_phone / normalize_email`（决策 D-N）。
   画像入库（T-16）与判重（本文件）用同一对函数，不要在这里另写一套去空格/去 +86 的逻辑，
   否则会出现"库里存 `13800000000`、判重比 `+8613800000000`"的静默漏判。
2. **姓名单独相同不判重**（F2-2），姓名只作为手机号的**增强证据**：手机号相同且姓名相同 →
   `name_phone_match`；手机号相同但姓名不同/缺失 → `phone_match`（可能是共用号码，交 HR 判断）。
   一期没有 `hash_match`（文件内容重复在上传阶段拦掉了）。
3. **判重排除本解析结果当前指向的画像**（D-I 防自判重）：`parsed.talent_profile_id` 指向的
   那一个必须排除，否则「画像已建 + 向量失败」重试会把自己判成重复人。
4. **merge 不调 LLM**（接口实现注意第 3 条）：`ability_scores_json` 沿用 master 原值、
   标签/技能只做并集，绝不重新跑 Prompt B。

失败语义：业务失败抛 `BizError(400/404)`；向量失败**不抛异常**，由
`ResolveResult.vector_status="failed"` 表达（半写口径：200 + `vector_status`，不回滚合并）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.core.normalize import normalize_email, normalize_phone, normalize_skills
from app.dao import parse_tasks as task_dao
from app.dao import talent_duplicates as dup_dao
from app.dao import talent_profiles as profile_dao
from app.dao.talent_duplicates import STATUS_KEPT, STATUS_MERGED, STATUS_PENDING
from app.models import (
    ParsedResume,
    ParseTask,
    ResumeFile,
    TalentDuplicate,
    TalentProfile,
    TalentTag,
)
from app.service import profile_service
from app.utils.mask import mask_email, mask_phone

logger = logging.getLogger(__name__)

ResolveAction = Literal["merge", "keep"]

REASON_PHONE = "phone_match"
REASON_EMAIL = "email_match"
REASON_NAME_PHONE = "name_phone_match"

#: merge 合并的标量字段（API 名，与 `manual_edited_fields` 同口径）
MERGE_SCALAR_FIELDS: tuple[str, ...] = (
    "name",
    "gender",
    "birth_year",
    "phone",
    "email",
    "city",
    "degree",
    "work_years",
    "current_title",
    "expected_title",
)

NAME_MAX = 50  # talent_profiles.name VARCHAR(50)


@dataclass(frozen=True)
class DuplicateMatch:
    """一次命中：哪张画像、什么理由、命中的规范化值（已脱敏，直接落库展示）。"""

    profile_id: int
    reason: str
    matched_value: str
    profile_name: str


@dataclass
class ResolveResult:
    """resolve 的结果；API 层据此拼响应体（merge 200 / keep 202）。"""

    duplicate: TalentDuplicate
    action: ResolveAction
    profile: TalentProfile
    task: ParseTask | None
    vector_status: str | None = None


# --------------------------------------------------------------------------- #
# 判重（T-13 在写 parsed_resumes 之后调用）
# --------------------------------------------------------------------------- #
def detect(
    db: Session, parsed: ParsedResume, *, structured: Mapping[str, Any] | None = None
) -> DuplicateMatch | None:
    """三路判重，返回**第一个命中**（phone 优先于 email）；无命中返回 `None`。

    - `phone` 命中：取**最早创建**的候选画像；其中若存在同名者，优先取同名的那张并
      归因为 `name_phone_match`（证据更强，HR 一眼能确认）。
    - `email` 命中：`email_match`。
    - **姓名单独相同不判重**：只有姓名、没有手机号/邮箱命中时返回 `None`。
    - 软删除画像不参与判重（回收站里的档案不该拦住新简历入库）。
    """
    data = _as_mapping(structured if structured is not None else parsed.structured_json)
    basic = _as_mapping(data.get("basic"))
    name = str(basic.get("name") or "").strip()[:NAME_MAX] or None
    phone = normalize_phone(basic.get("phone"))
    email = normalize_email(basic.get("email"))
    exclude_id = parsed.talent_profile_id

    if phone:
        candidates = _find_by(db, TalentProfile.phone, phone, exclude_id)
        if candidates:
            target, same_name = _classify(candidates, name)
            reason = REASON_NAME_PHONE if same_name else REASON_PHONE
            return DuplicateMatch(target.id, reason, mask_phone(phone), target.name)

    if email:
        candidates = _find_by(db, TalentProfile.email, email, exclude_id)
        if candidates:
            target = candidates[0]
            return DuplicateMatch(target.id, REASON_EMAIL, _mask_email(email), target.name)

    return None


def _classify(
    candidates: Sequence[TalentProfile], name: str | None
) -> tuple[TalentProfile, bool]:
    """从手机号命中集里挑 target，并判断是否"姓名也一致"。

    - 有同名者 → 取同名者中最早创建的一张，`reason=name_phone_match`（姓名+手机号双命中）；
    - 无同名者 → 取最早创建的一张，`reason=phone_match`（可能共用号码/改过名，交 HR 判断）。
    """
    same_name = [row for row in candidates if name and row.name == name]
    target = (same_name or candidates)[0]
    return target, bool(same_name)


def _find_by(
    db: Session, column: Any, value: str, exclude_profile_id: int | None
) -> list[TalentProfile]:
    """按规范化值查既有画像；排除自己 + 排除软删除；按创建时间升序（稳定的 `profile_a`）。"""
    stmt = select(TalentProfile).where(column == value, TalentProfile.deleted_at.is_(None))
    if exclude_profile_id is not None:
        stmt = stmt.where(TalentProfile.id != exclude_profile_id)
    stmt = stmt.order_by(TalentProfile.created_at, TalentProfile.id)
    return list(db.execute(stmt).scalars().all())


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return mask_email(local, domain) if domain else value


def flag(db: Session, parsed: ParsedResume, match: DuplicateMatch) -> TalentDuplicate:
    """写/重置查重记录并把任务挂起为 `duplicate_pending`（**同一事务**）。

    幂等：`parsed_resume_id` UNIQUE，重试再命中时更新原记录（T-13 防护③）。
    `stage` 记为 `profile`——提取已成功，resolve 后 keep 从这里续跑；
    merge 会把它清成 NULL 并直接 success。
    """
    row, created = dup_dao.upsert_pending(
        db,
        parsed_resume_id=parsed.id,
        profile_a_id=match.profile_id,
        reason=match.reason,
        matched_value=match.matched_value,
    )
    task = task_dao.get_by_resume_file(db, parsed.resume_file_id)
    if task is not None:
        task.status = "duplicate_pending"
        # 提取已成功；已有 stage（正常为 None 或 profile）时不要回退，避免断点被覆盖
        task.stage = task.stage or "profile"
        task.error = None
        task.finished_at = None
    else:
        logger.warning("简历 %s 命中查重但找不到 parse_task，任务状态未同步", parsed.resume_file_id)
    db.commit()
    db.refresh(row)
    logger.info(
        "简历 %s 命中重复画像 %s（%s，%s）",
        parsed.resume_file_id,
        match.profile_id,
        match.reason,
        "新建记录" if created else "重置原记录",
    )
    return row


# --------------------------------------------------------------------------- #
# resolve 主入口
# --------------------------------------------------------------------------- #
def resolve(
    db: Session,
    duplicate_id: int,
    *,
    action: ResolveAction,
    master_profile_id: int | None = None,
    note: str | None = None,
    user_id: int,
) -> ResolveResult:
    """处理一条查重记录：`merge`（同步）/ `keep`（202 异步建行）。

    校验顺序（错误码口径见《统一错误码与鉴权》§五）：
    记录不存在 404 → 非 pending 400 → 解析结果缺失 400 → master 非法 400。
    """
    duplicate = dup_dao.get(db, duplicate_id)
    if duplicate is None:
        raise BizError(404, "查重记录不存在")
    if duplicate.status != STATUS_PENDING:
        raise BizError(400, "该查重记录已处理，不能重复操作")
    if action not in ("merge", "keep"):
        raise BizError(400, "action 只支持 merge / keep")

    parsed = db.get(ParsedResume, duplicate.parsed_resume_id)
    if parsed is None:
        raise BizError(400, "查重记录对应的解析结果不存在，无法处理")

    now = datetime.now()
    if action == "merge":
        result = _merge(db, duplicate, parsed, master_profile_id, note, user_id, now)
    else:
        result = _keep(db, duplicate, parsed, note, user_id, now)

    # commit 会让对象过期；向量状态由 `milvus_store.set_vector_status` 在另一个 session 写入，
    # 不 refresh 会把旧的 `vector_status` 带进响应体
    db.refresh(result.duplicate)
    db.refresh(result.profile)
    if result.task is not None:
        db.refresh(result.task)
    return result


# --------------------------------------------------------------------------- #
# merge：同步（≤ VECTOR_SYNC_TIMEOUT + 一次写入）
# --------------------------------------------------------------------------- #
def _merge(
    db: Session,
    duplicate: TalentDuplicate,
    parsed: ParsedResume,
    master_profile_id: int | None,
    note: str | None,
    user_id: int,
    now: datetime,
) -> ResolveResult:
    """合并到 master（**只能是 `profile_a_id`**），不重跑任何 LLM。"""
    if master_profile_id is not None and master_profile_id != duplicate.profile_a_id:
        raise BizError(400, "一期合并只支持 master_profile_id 等于 profile_a_id")

    master = db.scalar(select(TalentProfile).where(TalentProfile.id == duplicate.profile_a_id).with_for_update().execution_options(populate_existing=True))
    if master is None or master.deleted_at is not None:
        raise BizError(400, "主画像不存在或已删除，无法合并")

    duplicate = db.scalar(select(TalentDuplicate).where(TalentDuplicate.id == duplicate.id)
        .with_for_update().execution_options(populate_existing=True))
    if duplicate is None or duplicate.status != STATUS_PENDING:
        raise BizError(409, "查重记录状态已变化，请刷新后重试")
    parsed = db.scalar(select(ParsedResume).where(ParsedResume.id == parsed.id)
        .with_for_update().execution_options(populate_existing=True))
    if parsed is None:
        raise BizError(409, "解析结果已变化，请刷新后重试")

    from app.modules.talents.history import save_version
    save_version(db, master, "baseline")
    from app.modules.talents import merge_journal
    before_merge = merge_journal.begin(db, master, parsed)
    data = _as_mapping(parsed.structured_json)
    values = profile_service.build_scalar_fields(data)
    protected = set(master.manual_edited_fields or [])

    updates: dict[str, Any] = {}
    for api_name in MERGE_SCALAR_FIELDS:
        if api_name in protected:
            continue
        column = profile_service.FIELD_COLUMNS[api_name]
        new_value = values.get(column)
        if new_value is None or new_value == "":
            continue  # 新简历没填 → 保留 master 原值
        if api_name == "name" and new_value == profile_service.DEFAULT_NAME and master.name != new_value:
            continue  # 占位名不覆盖真名（T-16 的 DEFAULT_NAME 兜底）
        updates[column] = new_value

    new_skills = list(values.get("skills_json") or [])
    if "skills" not in protected:
        merged_skills = normalize_skills(list(master.skills_json or []) + new_skills)
        if merged_skills != list(master.skills_json or []):
            updates["skills_json"] = merged_skills

    tag_rows = _merged_tag_rows(profile_dao.get_tag_rows(db, master.id), new_skills)
    if "tags" not in protected:
        updates["tags_json"] = [tag for tag, _, _ in tag_rows]

    changed = profile_dao.apply_fields(db, master, updates)
    if "tags" not in protected:
        profile_dao.replace_tag_rows(db, master.id, tag_rows)
    master.updated_by = user_id

    # 经历类：该解析结果改指 master，详情页/画像文本据此聚合多份简历（§5.3.1 第 4 条）
    parsed.talent_profile_id = master.id

    dup_dao.mark_resolved(
        db, duplicate, status=STATUS_MERGED, user_id=user_id, at=now, note=note
    )
    task = _finish_task(db, parsed.resume_file_id, now)

    db.flush()
    merged_version = save_version(db, master, "merge", changed_by=user_id)
    merge_journal.finish(db, master, parsed, duplicate, before_merge, merged_version, user_id)
    db.commit()
    db.refresh(master)
    logger.info(
        "查重 %s 合并到画像 %s（字段 %s，标签 %d 个）",
        duplicate.id,
        master.id,
        changed or "无变化",
        len(tag_rows),
    )

    # 向量：业务数据已提交，失败只影响 vector_status（半写口径，不回滚合并）
    aggregated = profile_service.aggregate_structured(db, master.id)
    tag_names = [row.tag for row in profile_dao.get_tag_rows(db, master.id)]
    vector = profile_service.sync_profile_vector(
        master,
        structured=aggregated,
        tags=tag_names,
        budget=settings.vector_sync_timeout,
    )
    vector_status = "synced" if vector is None else vector.vector_status

    return ResolveResult(
        duplicate=duplicate,
        action="merge",
        profile=master,
        task=task,
        vector_status=vector_status,
    )


def undo_merge(db: Session, duplicate_id: int, *, user_id: int, reason: str) -> dict:
    from app.modules.talents.merge_journal import undo
    return undo(db, duplicate_id, user_id=user_id, reason=reason)


def _merged_tag_rows(
    existing: Sequence[TalentTag], new_skills: Sequence[str]
) -> list[tuple[str, str, str]]:
    """标签并集：master 既有标签（保留 `source`）+ 新简历技能补成 `skill` 标签。

    新简历在查重阶段**没有跑过 Prompt B**（不建画像就不调 LLM），所以它的"标签"
    实际只有归一化技能；把技能补进 master 的标签集，既不丢信息也不多花一次 LLM。
    同名标签保留 master 原有的 type/source（`manual` 标记不被覆盖，§5.3.1 第 5 条）。
    """
    merged: dict[str, tuple[str, str]] = {}
    for row in existing:
        merged[row.tag] = (row.tag_type, row.source)
    for skill in new_skills:
        if skill and skill not in merged:
            merged[skill] = ("skill", "llm")
    return [(tag, tag_type, source) for tag, (tag_type, source) in merged.items()]


def _finish_task(db: Session, resume_file_id: int, now: datetime) -> ParseTask | None:
    """merge 完成 → 任务 success（`profiled_at/finished_at` 必须非空）。"""
    task = task_dao.get_by_resume_file(db, resume_file_id)
    if task is not None:
        task.status = "success"
        task.stage = None
        task.error = None
        task.profiled_at = now
        task.finished_at = now
        task.heartbeat_at = None
    return task


# --------------------------------------------------------------------------- #
# keep：同步建画像空行 + 任务转 pending(stage=profile)
# --------------------------------------------------------------------------- #
def _keep(
    db: Session,
    duplicate: TalentDuplicate,
    parsed: ParsedResume,
    note: str | None,
    user_id: int,
    now: datetime,
) -> ResolveResult:
    """保留为独立画像：同步只建**空行**，标签/能力/向量交给 runner（§5.3.2）。"""
    existing = db.get(TalentProfile, parsed.talent_profile_id) if parsed.talent_profile_id else None
    if existing is not None and existing.id == duplicate.profile_a_id:
        raise BizError(400, "该解析结果已归属主画像，不能再保留为独立画像")

    profile = existing
    if profile is None:
        values = profile_service.build_scalar_fields(_as_mapping(parsed.structured_json))
        # tags_json / ability_scores_json 保持 NULL、vector_status=pending：
        # 这是"半成品画像"，runner 从 stage=profile 补齐（所以接口能同步返回 id）
        profile = profile_dao.create(db, source="resume", vector_status="pending", **values)
        parsed.talent_profile_id = profile.id
        db.flush()

    dup_dao.mark_resolved(
        db,
        duplicate,
        status=STATUS_KEPT,
        user_id=user_id,
        at=now,
        note=note,
        profile_b_id=profile.id,
    )

    task = task_dao.get_by_resume_file(db, parsed.resume_file_id)
    if task is not None:
        task.status = "pending"
        task.stage = "profile"
        task.error = None
        task.started_at = None
        task.heartbeat_at = None
        task.profiled_at = None
        task.finished_at = None
    else:
        logger.warning("简历 %s keep 后找不到 parse_task，后台不会补标签", parsed.resume_file_id)

    db.commit()
    db.refresh(profile)
    logger.info("查重 %s 保留为独立画像 %s（任务转 pending/stage=profile）", duplicate.id, profile.id)

    return ResolveResult(
        duplicate=duplicate,
        action="keep",
        profile=profile,
        task=task,
        vector_status=profile.vector_status,
    )


# --------------------------------------------------------------------------- #
# 列表（`GET /talents/duplicates`）
# --------------------------------------------------------------------------- #
def list_duplicates(
    db: Session, *, status: str | None = STATUS_PENDING, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """分页列出查重记录；`matched_value` 已脱敏（库里存的就是脱敏值）。

    批量取画像/解析结果/文件，避免一条查重 3 次查库（一页 20 条就是 60 次）。
    """
    rows, total = dup_dao.list_page(db, status=status, offset=offset, limit=limit)
    if not rows:
        return [], total

    profile_ids = {row.profile_a_id for row in rows}
    profile_ids |= {row.profile_b_id for row in rows if row.profile_b_id}
    profiles = {item.id: item for item in profile_dao.list_by_ids(db, profile_ids)}

    parsed_ids = {row.parsed_resume_id for row in rows}
    parsed_rows = {
        item.id: item
        for item in db.execute(select(ParsedResume).where(ParsedResume.id.in_(parsed_ids))).scalars()
    }

    file_ids = {item.resume_file_id for item in parsed_rows.values()}
    files = (
        {
            item.id: item
            for item in db.execute(select(ResumeFile).where(ResumeFile.id.in_(file_ids))).scalars()
        }
        if file_ids
        else {}
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        parsed = parsed_rows.get(row.parsed_resume_id)
        file_row = files.get(parsed.resume_file_id) if parsed is not None else None
        items.append(
            {
                "id": row.id,
                "reason": row.reason,
                "matched_value": row.matched_value,
                "status": row.status,
                "profile_a": _profile_summary(profiles.get(row.profile_a_id)),
                "profile_b": _profile_summary(profiles.get(row.profile_b_id))
                if row.profile_b_id
                else None,
                "parsed_resume": _parsed_summary(parsed, file_row),
                "created_at": row.created_at,
            }
        )
    return items, total


def _profile_summary(profile: TalentProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "id": profile.id,
        "name": profile.name,
        "current_title": profile.current_title,
        "created_at": profile.created_at,
    }


def _parsed_summary(
    parsed: ParsedResume | None, file_row: ResumeFile | None
) -> dict[str, Any] | None:
    if parsed is None:
        return None
    basic = _as_mapping(_as_mapping(parsed.structured_json).get("basic"))
    phone = normalize_phone(basic.get("phone"))
    return {
        "id": parsed.id,
        "filename": file_row.filename if file_row is not None else None,
        "name": str(basic.get("name") or "").strip()[:NAME_MAX] or None,
        "phone": mask_phone(phone) if phone else None,
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    """与 `profile_service._as_mapping` 同口径：MySQL JSON 列返回 dict，字符串兜底再解析一次。

    兜底不是多余：旧数据/测试造数可能是 JSON 字符串，解析失败按空对象处理，
    绝不让"判重静默失效"变成"所有简历都当成新人才"。
    """
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


__all__ = [
    "MERGE_SCALAR_FIELDS",
    "REASON_EMAIL",
    "REASON_NAME_PHONE",
    "REASON_PHONE",
    "DuplicateMatch",
    "ResolveResult",
    "detect",
    "flag",
    "list_duplicates",
    "resolve",
]
