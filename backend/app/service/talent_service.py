"""人才画像 CRUD 服务（T-18）· 接口 §5 + 需求 F2-3/F2-4/F2-6/F2-7。

实现依据：
- `docs/接口设计.md` v1.3 §5（GET/PATCH/DELETE/restore/revector/resume-file）
- `docs/数据库设计.md` v1.2 §5.1/§5.2（画像字段、标签行同步规则）
- 需求 #34（D-J）：经历类字段一期只读，由 `parsed_resumes` 聚合展示
- 决策 D-B：向量失败不回滚业务写入；revector 无业务写入 → 可 503

三条硬口径（改动前先读）：
1. **向量同步唯一入口**是 `profile_service.sync_profile_vector()`（hash 触发，T-16 落地）。
   本服务不得自行拼画像文本、不得直接调 embedding/Milvus（软删向量删除除外，
   那是 `delete_profile_vector()` 的专职）。
2. **PATCH 的 manual_edited_fields 只记「实际变化」的字段**：值归一化后与现值相等
   不算修改（例如 phone 传 `+86 138-...` 而库里已是 `138...`）。字段级保护由
   `build_profile()`（T-16）按这份清单跳过自动覆盖。
3. **PATCH / restore 是业务写入 → 向量失败返回 200 + `vector_status:"failed"`**；
   **revector 无业务写入 → 向量失败抛 503**（接口 §5 错误约定，D-B 的 503 边界）。

标签重建采用「tags ∪ skills」合并模型（日志 T-18 取舍③）：`talent_tags` 最终 =
请求标签 ∪ `skills_json`（技能行 `tag_type='skill'`），与 T-17 merge 的并集口径一致。
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.normalize import DEGREE_VALUES, normalize_email, normalize_phone, normalize_skills, normalize_tag
from app.dao import resume_files as resume_file_dao
from app.dao import talent_profiles as profile_dao
from app.models import JobProfile, MatchBatch, MatchResult, ScreenBatch, ScreenResult, TalentProfile, TalentTag
from app.schemas.profile import ABILITY_KEYS, BASIS_MAX_CHARS
from app.schemas.talent import TAG_TYPE_VALUES, TagEntry, TalentPatchRequest
from app.service.profile_service import FIELD_COLUMNS, aggregate_structured, sync_profile_vector

logger = logging.getLogger(__name__)

PROFILE_NOT_FOUND = "画像不存在"
#: PATCH 里出现即 400 的只读字段（D-J：经历类由 parsed_resumes 聚合展示，画像表无存储位）
READONLY_EXPERIENCE_FIELDS = ("educations", "employments", "projects", "certificates")
WORK_YEARS_MAX = Decimal("80")
VECTOR_UNAVAILABLE_MESSAGE = "向量服务暂不可用，重建失败，请稍后重试"


# --------------------------------------------------------------------------- #
# 查询：列表 / 详情
# --------------------------------------------------------------------------- #
def list_talents(
    db: Session,
    *,
    deleted: bool = False,
    keyword: str | None = None,
    tag: str | None = None,
    tag_type: str | None = None,
    degree: str | None = None,
    min_years: float | None = None,
    max_years: float | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """画像分页列表；标签行一次预取防 N+1（接口 §5「列表查询用预加载防 N+1」）。"""
    rows, total = profile_dao.list_page(
        db,
        deleted=deleted,
        keyword=keyword or None,
        tag=tag or None,
        tag_type=tag_type or None,
        degree=degree or None,
        min_years=min_years,
        max_years=max_years,
        offset=offset,
        limit=limit,
    )
    tag_map = profile_dao.tag_rows_for_profiles(db, [row.id for row in rows])
    items: list[dict[str, Any]] = []
    for row in rows:
        tag_rows = tag_map.get(row.id, [])
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "current_title": row.current_title,
                "work_years": float(row.work_years) if row.work_years is not None else None,
                "degree": row.degree,
                "city": row.city,
                "tags": [tag_row.tag for tag_row in tag_rows[:8]],
                "tag_total": len(tag_rows),
                "source": row.source,
                "manual_edited_fields": list(row.manual_edited_fields or []),
                "vector_status": row.vector_status,
                "deleted_at": row.deleted_at,
                "updated_at": row.updated_at,
            }
        )
    return items, total


def get_detail(db: Session, talent_id: int) -> dict[str, Any]:
    """画像详情：标量 + 标签行 + 经历聚合只读视图 + 原始简历 + 筛选/匹配历史。

    已软删除画像 404（接口 §5）；历史筛选/匹配结果仍在结果表里，join 时由
    画像名兜底（详情接口不可达不影响历史列表显示姓名）。
    """
    profile = _require_profile(db, talent_id)
    aggregated = aggregate_structured(db, talent_id)
    tag_rows = profile_dao.get_tag_rows(db, talent_id)
    files = resume_file_dao.list_by_talent_profile(db, talent_id)
    return {
        "profile": _profile_out(profile, tag_rows),
        "educations": aggregated.get("educations", []),
        "employments": aggregated.get("employments", []),
        "projects": aggregated.get("projects", []),
        "certificates": aggregated.get("certificates", []),
        "resume_files": [
            {
                "resume_file_id": file_row.id,
                "filename": file_row.filename,
                "upload_batch_id": file_row.upload_batch_id,
            }
            for file_row in files
        ],
        "screen_history": _screen_history(db, talent_id),
        "match_history": _match_history(db, talent_id),
    }


def _screen_history(db: Session, talent_id: int) -> list[dict[str, Any]]:
    """筛选历史：`effective_verdict = manual_verdict ?? verdict`（数据库 §6.3）。

    读查询直接 select（T-18 局部只读视图）；筛选批次自己的 DAO 归 T-24，不在此抢建。
    """
    rows = db.execute(
        select(ScreenResult, JobProfile.title)
        .join(ScreenBatch, ScreenBatch.id == ScreenResult.screen_batch_id)
        .join(JobProfile, JobProfile.id == ScreenBatch.job_profile_id, isouter=True)
        .where(ScreenResult.talent_profile_id == talent_id)
        .order_by(ScreenResult.created_at.desc(), ScreenResult.id.desc())
    ).all()
    return [
        {
            "batch_id": result.screen_batch_id,
            "job_title": title,
            "effective_verdict": effective_verdict(result.verdict, result.manual_verdict),
            "score": float(result.score) if result.score is not None else None,
            "created_at": result.created_at,
        }
        for result, title in rows
    ]


def _match_history(db: Session, talent_id: int) -> list[dict[str, Any]]:
    """匹配历史（接口 §5：batch_id / job_title / score / hard_fail / created_at）。"""
    rows = db.execute(
        select(MatchResult, JobProfile.title)
        .join(MatchBatch, MatchBatch.id == MatchResult.match_batch_id)
        .join(JobProfile, JobProfile.id == MatchBatch.job_profile_id, isouter=True)
        .where(MatchResult.talent_profile_id == talent_id)
        .order_by(MatchResult.created_at.desc(), MatchResult.id.desc())
    ).all()
    return [
        {
            "batch_id": result.match_batch_id,
            "job_title": title,
            "score": float(result.score),
            "hard_fail": bool(result.hard_fail),
            "created_at": result.created_at,
        }
        for result, title in rows
    ]


def effective_verdict(verdict: str, manual_verdict: str | None) -> str:
    """有效结论 = 人工改判优先（数据库 §6.3）。"""
    return manual_verdict or verdict


# --------------------------------------------------------------------------- #
# PATCH：人工修正（F2-6 字段级保护）
# --------------------------------------------------------------------------- #
def patch_profile(
    db: Session, talent_id: int, payload: TalentPatchRequest, *, user_id: int
) -> dict[str, Any]:
    """人工修正画像；返回 {profile, manual_edited_fields, vector_status, profile_text_changed}。

    流程：归一化入参（400）→ 与现值 diff（只有真变化才入保护清单）→
    同一事务写字段 + `manual_edited_fields` + `updated_by` + 标签行重建 →
    commit 后按 hash 触发向量重算（失败不回滚，200 + failed）。
    """
    profile = _require_profile(db, talent_id)
    provided = payload.model_fields_set
    values = _build_patch_values(payload, provided)

    changed = [name for name, value in values.items() if getattr(profile, FIELD_COLUMNS[name]) != value]

    tag_rows_before = profile_dao.get_tag_rows(db, talent_id)
    skills_final = values["skills"] if "skills" in values else list(profile.skills_json or [])
    tags_changed = _tags_changed(tag_rows_before, values)
    skills_changed = "skills" in values and values["skills"] != list(profile.skills_json or [])
    rebuild_tags = tags_changed or skills_changed
    new_tag_rows = (
        build_tag_rows(tag_rows_before, values.get("tags"), skills_final) if rebuild_tags else None
    )

    if changed:
        # tags 不走标量赋值：tags_json 由下面的标签行重建统一定名（否则 TagEntry 会泄进 JSON 列）
        columns = {FIELD_COLUMNS[name]: values[name] for name in changed if name != "tags"}
        profile_dao.apply_fields(db, profile, columns)
        # 字段级保护：并入（去重、保持原顺序），不清除历史记录（F2-6）
        profile.manual_edited_fields = list(dict.fromkeys([*(profile.manual_edited_fields or []), *changed]))
        profile.updated_by = user_id
    if rebuild_tags:
        # 标签行重建与字段写入同一事务（数据库 §5.2 同步规则）
        profile_dao.replace_tag_rows(db, talent_id, new_tag_rows)
        profile.tags_json = [name for name, _, _ in new_tag_rows]
    if not changed and not rebuild_tags:
        # 无实际变化：不写库、不触发向量（验收点「改一个 hash 不变的字段则零 embedding」的极限情形）
        return {
            "profile": _profile_out(profile, tag_rows_before),
            "manual_edited_fields": list(profile.manual_edited_fields or []),
            "vector_status": profile.vector_status,
            "profile_text_changed": False,
        }
    db.commit()
    db.refresh(profile)

    before_hash = profile.profile_text_hash
    structured = aggregate_structured(db, talent_id)
    tags_now = profile_dao.get_tags(db, talent_id)
    result = sync_profile_vector(profile, structured=structured, tags=tags_now)
    tag_rows_after = profile_dao.get_tag_rows(db, talent_id) if rebuild_tags else tag_rows_before
    fresh = _reload_profile(talent_id)
    return {
        "profile": _profile_out(fresh, tag_rows_after),
        "manual_edited_fields": list(fresh.manual_edited_fields or []),
        "vector_status": result.vector_status if result is not None else fresh.vector_status,
        "profile_text_changed": bool(result) and result.text_hash != before_hash,
    }


def _require_profile(db: Session, talent_id: int, *, allow_deleted: bool = False) -> TalentProfile:
    profile = profile_dao.get(db, talent_id)
    if profile is None or (profile.deleted_at is not None and not allow_deleted):
        raise BizError(404, PROFILE_NOT_FOUND)
    return profile


def _reload_profile(profile_id: int) -> TalentProfile:
    """在**新事务**里重读画像行。

    `set_vector_status` 在另一个 session 提交；本 session 若未结束事务（revector 无业务
    写入、PATCH 的 commit 发生在同步之前），REPEATABLE READ 快照让 refresh 读到旧值
    （T-17 已踩过同坑）。跨 session 重读是唯一可靠做法。
    """
    from app.database import SessionLocal

    with SessionLocal() as fresh:
        return fresh.get(TalentProfile, profile_id)


def _build_patch_values(payload: TalentPatchRequest, provided: set[str]) -> dict[str, Any]:
    """按「实际提供的字段」归一化入参；非法值抛 400（接口 §5：400 枚举/类型非法）。"""
    values: dict[str, Any] = {}
    if "name" in provided:
        if payload.name is None or not payload.name.strip():
            raise BizError(400, "name 不能为空")
        values["name"] = payload.name.strip()
    if "phone" in provided:
        values["phone"] = _normalize_contact(payload.phone, normalize_phone, "手机号")
    if "email" in provided:
        values["email"] = _normalize_contact(payload.email, normalize_email, "邮箱")
    if "city" in provided:
        values["city"] = _optional_text(payload.city)
    if "degree" in provided:
        if payload.degree is None or not payload.degree.strip():
            values["degree"] = None
        elif payload.degree.strip() not in DEGREE_VALUES:
            raise BizError(400, f"degree 仅支持 {'/'.join(DEGREE_VALUES)}")
        else:
            values["degree"] = payload.degree.strip()
    if "work_years" in provided:
        if payload.work_years is None:
            values["work_years"] = None
        else:
            years = Decimal(str(payload.work_years))
            if years < 0 or years > WORK_YEARS_MAX:
                raise BizError(400, f"work_years 需在 0–{WORK_YEARS_MAX} 之间")
            values["work_years"] = years.quantize(Decimal("0.1"))
    if "current_title" in provided:
        values["current_title"] = _optional_text(payload.current_title)
    if "expected_title" in provided:
        values["expected_title"] = _optional_text(payload.expected_title)
    if "skills" in provided:
        values["skills"] = normalize_skills(payload.skills or [])
    if "tags" in provided:
        entries: list[TagEntry] = []
        for item in payload.tags or []:
            entry = item if isinstance(item, TagEntry) else TagEntry(tag=item)
            if entry.tag_type is not None and entry.tag_type not in TAG_TYPE_VALUES:
                raise BizError(400, f"tag_type 仅支持 {'/'.join(TAG_TYPE_VALUES)}")
            entries.append(entry)
        values["tags"] = entries
    if "ability_scores" in provided:
        values["ability_scores"] = _normalize_ability(payload.ability_scores)
    return values


def _normalize_contact(raw: str | None, normalize: Any, label: str) -> str | None:
    """联系方式归一化（D-N：`normalize_phone/email` 是唯一实现，也是查重键来源）。

    None / 空串 → 清空（NULL）；非空但归一化失败（提取不出数字 / 非法邮箱）→ 400。
    """
    if raw is None or not raw.strip():
        return None
    normalized = normalize(raw.strip())
    if not normalized:
        raise BizError(400, f"{label}格式无法识别：{raw.strip()}")
    return normalized


def _optional_text(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _normalize_ability(scores: dict[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """五维整体替换：键必须恰好是五个维度（未知/缺失都 400），score clamp 0–100、basis 截 60。"""
    if scores is None:
        return None
    unknown = [key for key in scores if key not in ABILITY_KEYS]
    if unknown:
        raise BizError(400, f"ability_scores 含未知维度：{', '.join(sorted(unknown))}")
    missing = [key for key in ABILITY_KEYS if key not in scores]
    if missing:
        raise BizError(400, f"ability_scores 缺少维度：{', '.join(missing)}")
    result: dict[str, dict[str, Any]] = {}
    for key in ABILITY_KEYS:
        dim = scores[key]
        result[key] = {
            "score": max(0, min(100, int(dim.score))),
            "basis": (dim.basis or "").strip()[:BASIS_MAX_CHARS],
        }
    return result


def _requested_tag_types(entries: Sequence[TagEntry]) -> dict[str, str | None]:
    """请求标签 → {归一化名: 显式 type}；重名时**显式 type 优先于缺省 None，先到先得**。"""
    result: dict[str, str | None] = {}
    for entry in entries:
        name = normalize_tag(entry.tag)
        if not name:
            continue
        if name not in result or entry.tag_type:
            result[name] = entry.tag_type
    return result


def _tags_changed(existing: Sequence[TalentTag], values: dict[str, Any]) -> bool:
    """请求 tags 与现标签是否有实际差异：名字集合不同，或显式给出的 tag_type 与现值不同。"""
    if "tags" not in values:
        return False
    existing_map = {row.tag: row for row in existing}
    requested = _requested_tag_types(values["tags"])
    if set(requested) != set(existing_map):
        return True
    for name, tag_type in requested.items():
        if tag_type and existing_map[name].tag_type != tag_type:
            return True
    return False


def build_tag_rows(
    existing: Sequence[TalentTag],
    requested: Sequence[TagEntry] | None,
    skills_final: Sequence[str],
) -> list[tuple[str, str, str]]:
    """计算重建 `talent_tags` 的目标行 `(tag, tag_type, source)`（合并模型见模块 docstring）。

    - `requested=None`（只改 skills）：保留既有非技能行，技能行随 `skills_final` 增删；
    - `requested` 给定：以请求名单为准（缺的一并补上 skills 名单）；
    - 既有行 source 恒保留（`manual` 不被覆盖，接口 §5 PATCH 第 2 条）；新增行 source='manual'；
    - 名字命中 `skills_final` 的行 tag_type 恒为 `skill`（数据库 §5.2）。
    """
    existing_map = {row.tag: row for row in existing}
    skill_names = {name for name in (normalize_tag(skill) for skill in skills_final) if name}
    requested_map: dict[str, str | None] = {}

    if requested is None:
        kept = [
            row.tag
            for row in existing
            if not (row.tag_type == "skill" and row.tag not in skill_names)
        ]
        ordered = kept + sorted(name for name in skill_names if name not in existing_map)
    else:
        requested_map = _requested_tag_types(requested)
        ordered = list(requested_map) + sorted(name for name in skill_names if name not in requested_map)

    rows: list[tuple[str, str, str]] = []
    for name in ordered:
        old = existing_map.get(name)
        explicit_type = requested_map.get(name) if requested is not None else None
        if name in skill_names:
            tag_type = "skill"
        elif explicit_type:
            tag_type = explicit_type
        elif old is not None:
            tag_type = old.tag_type
        else:
            tag_type = "general"
        source = old.source if old is not None else "manual"
        rows.append((name, tag_type, source))
    return rows


# --------------------------------------------------------------------------- #
# 语义检索（T-19，F2-5）
# --------------------------------------------------------------------------- #
SEARCH_QUERY_MAX_CHARS = 500
VECTOR_UNAVAILABLE_PREFIX = "向量服务暂不可用"


def search_talents(db: Session, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """语义检索：query embedding（短期缓存）→ Milvus COSINE topK → 回查 MySQL 排除软删。

    本接口**无任何业务写入** → Embedding/Milvus 不可用时抛 503（接口 §5 的 503 合法
    场景，D-B 边界的另一侧）；绝不返回"空列表伪装成功"。

    命中的向量可能滞后（画像改了字段还没重算，`text_hash` 与 `profile_text_hash`
    不一致），一期只回传 `vector_status` 供前端提示，不做hash比对展示（接口未要求）。
    """
    text = (query or "").strip()
    if not text:
        raise BizError(400, "query 不能为空")
    if len(text) > SEARCH_QUERY_MAX_CHARS:
        raise BizError(400, f"query 最长 {SEARCH_QUERY_MAX_CHARS} 字")

    from app.core.embedding_client import embed_query
    from app.core.milvus_store import get_store

    try:
        vector = embed_query(text)  # 真实调用后被动刷新 health.embedding_alive（T-05 口径）
    except Exception as exc:  # noqa: BLE001 - Embedding 挂了必须 503 而不是 500/空列表
        logger.warning("语义检索 embedding 失败：%s: %s", type(exc).__name__, exc)
        raise BizError(503, f"{VECTOR_UNAVAILABLE_PREFIX}（检索服务响应异常）") from exc
    try:
        hits = get_store().search(vector, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("语义检索 Milvus search 失败：%s: %s", type(exc).__name__, exc)
        raise BizError(503, f"{VECTOR_UNAVAILABLE_PREFIX}（检索服务响应异常）") from exc

    # 回查 MySQL：排除软删与已消失的画像（残留向量无害的兜底，数据库 §5.1）
    rows = {
        row.id: row
        for row in profile_dao.list_by_ids(db, [hit.talent_profile_id for hit in hits])
    }
    live_ids = [rid for rid, row in rows.items() if row.deleted_at is None]
    tag_map = profile_dao.tag_rows_for_profiles(db, live_ids)

    items: list[dict[str, Any]] = []
    for hit in hits:
        row = rows.get(hit.talent_profile_id)
        if row is None or row.deleted_at is not None:
            continue
        tag_rows = tag_map.get(row.id, [])
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "current_title": row.current_title,
                "work_years": float(row.work_years) if row.work_years is not None else None,
                "degree": row.degree,
                "city": row.city,
                "tags": [tag_row.tag for tag_row in tag_rows[:8]],
                "tag_total": len(tag_rows),
                "vector_status": row.vector_status,
                "similarity": round(min(1.0, max(0.0, hit.score)), 4),
            }
        )
    return items


# --------------------------------------------------------------------------- #
# DELETE / restore / revector / 原文下载
# --------------------------------------------------------------------------- #
def soft_delete(db: Session, talent_id: int, *, user_id: int) -> dict[str, Any]:
    """软删除（F2-7）：写 deleted_at/deleted_by → 提交 → 删 Milvus 向量（失败不阻塞）。

    重复删除 404（接口 §5）。关联 parsed_resumes / 原始文件 / 历史结果全部保留。
    """
    profile = _require_profile(db, talent_id)
    if profile.deleted_at is not None:
        raise BizError(404, PROFILE_NOT_FOUND)
    profile.deleted_at = datetime.now()
    profile.deleted_by = user_id
    db.commit()
    vector_deleted = _delete_vector_quietly(talent_id)
    return {"deleted": True, "vector_deleted": vector_deleted}


def restore(db: Session, talent_id: int, *, user_id: int) -> dict[str, Any]:
    """撤销软删除（D-H）：清 deleted_at/deleted_by → `force=True` 重嵌向量。

    必须 force：软删时向量已从 Milvus 删除，而 `profile_text_hash` 与
    `vector_status` 均未变化——不 force 会被「hash 未变且 synced」短路，向量永远补不回来。
    """
    profile = _require_profile(db, talent_id, allow_deleted=True)
    if profile.deleted_at is None:
        raise BizError(400, "画像未被软删除，无需恢复")
    profile.deleted_at = None
    profile.deleted_by = None
    profile.updated_by = user_id
    db.commit()
    db.refresh(profile)

    structured = aggregate_structured(db, talent_id)
    tags = profile_dao.get_tags(db, talent_id)
    result = sync_profile_vector(profile, structured=structured, tags=tags, force=True)
    vector_status = result.vector_status if result is not None else profile.vector_status
    return {"restored": True, "vector_status": vector_status}


def revector(db: Session, talent_id: int) -> dict[str, Any]:
    """向量补偿（D-B）：只重算文本 + embedding + upsert，不触碰业务字段。

    无业务写入 → 失败抛 503（接口 §5 明确的 503 合法场景）；成功返回最新
    vector_status / vector_synced_at（由 `set_vector_status` 在独立 session 写入，
    本 session 无 commit、REPEATABLE READ 快照读不到，必须新事务重读）。
    """
    profile = _require_profile(db, talent_id)
    structured = aggregate_structured(db, talent_id)
    tags = profile_dao.get_tags(db, talent_id)
    result = sync_profile_vector(profile, structured=structured, tags=tags, force=True)
    if result is None or not result.ok:
        raise BizError(503, VECTOR_UNAVAILABLE_MESSAGE)
    fresh = _reload_profile(talent_id)
    return {
        "vector_status": fresh.vector_status,
        "vector_synced_at": fresh.vector_synced_at,
    }


def get_resume_file(db: Session, talent_id: int, resume_file_id: int) -> tuple[Any, Any]:
    """原文下载定位：画像存在且未软删、文件属于该画像、磁盘文件存在（路径穿越防护见
    `resolve_stored_path`：相对路径钉在 `backend/` 内，越界抛 ValueError）。"""
    _require_profile(db, talent_id)
    file_row = resume_file_dao.get_for_talent_profile(db, talent_id, resume_file_id)
    if file_row is None:
        raise BizError(404, "该画像下不存在此简历文件")
    from app.service.upload_service import resolve_stored_path

    path = resolve_stored_path(file_row.stored_path)
    if not path.is_file():
        logger.warning("画像 %s 的简历文件 %s 磁盘缺失：%s", talent_id, resume_file_id, path)
        raise BizError(404, "简历文件已丢失，请联系管理员")
    return file_row, path


def _delete_vector_quietly(talent_id: int) -> bool:
    from app.core.milvus_store import delete_profile_vector

    try:
        return delete_profile_vector(talent_id)
    except Exception:  # noqa: BLE001 - 向量删除失败不阻塞软删（数据库 §5.1）
        logger.exception("画像 %s 向量删除异常（按失败处理）", talent_id)
        return False


def _profile_out(profile: TalentProfile, tag_rows: Sequence[TalentTag]) -> dict[str, Any]:
    """详情 / PATCH 共用的 profile 对象（接口 §5 字段集合；手机号/邮箱完整返回）。"""
    return {
        "id": profile.id,
        "name": profile.name,
        "gender": profile.gender,
        "birth_year": profile.birth_year,
        "phone": profile.phone,
        "email": profile.email,
        "city": profile.city,
        "degree": profile.degree,
        "work_years": float(profile.work_years) if profile.work_years is not None else None,
        "current_title": profile.current_title,
        "expected_title": profile.expected_title,
        "skills": list(profile.skills_json or []),
        "tags": [
            {"tag": row.tag, "tag_type": row.tag_type, "source": row.source} for row in tag_rows
        ],
        "ability_scores": profile.ability_scores_json,
        "manual_edited_fields": list(profile.manual_edited_fields or []),
        "vector_status": profile.vector_status,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
