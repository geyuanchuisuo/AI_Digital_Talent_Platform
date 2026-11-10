"""筛选批次服务（T-24）· 接口 §7 + 数据库 §6.2/§6.3 + 需求 F1-6/F1-9~F1-12。

硬口径（改动前先读）：
1. **成员快照（D-D）**：`POST /screen/batches` 同步阶段按 scope 圈定一次，
   `talent_profile_id` 清单写进 `scope_json.resolved_ids`、`total=len(resolved_ids)`；
   **worker 与 retry 只读快照**——本服务不提供任何重圈人查询。
2. **retry 复用原批次（D-E）**：仅 `failed`；复位 status/error/started/heartbeat/
   finished/vector_degraded、`retry_count+1`；**快照与 total 不变**；`progress` 按
   已有结果行数重算；worker 依 `uk_screen_batch_talent` 只补缺（结果行数不增不减）。
3. **`estimated_llm_calls`（A02-1 起）**：快照内有效画像数。V2.6 §5 取消硬门槛
   自动淘汰后，所有候选人都进 Prompt C，不再按预检通过人数折减（创建阶段仍不调 LLM）。
   规则层核对结果只作不符项/待核实展示，见 `match_engine.classify_rule_gaps`。
4. **T-A42 快照固定性**：`config_snapshot_json` 额外固定 `model_version` /
   `prompt_version`；worker 写结果行时**只读快照**，不读运行时 `settings`，
   保证同批次（含 retry 补缺）模型口径一致且历史批次可追溯。
5. **T-A42 请求级幂等**：客户端显式 `idempotency_key` 时，同岗位重复请求返回
   原批次（不新建、不重圈人、不重复建结果）；不传键行为零变化——岗位/档案
   变更仍新建一轮。并发靠 `(job_profile_id, idempotency_key)` 唯一约束兜底。
6. **A03 批次档案冻结**：创建批次时一次性冻结每位成员的**评分输入值拷贝**与
   B 正式档案版本/内容摘要，写进 `frozen_inputs_json`。worker 与 retry 只读该
   冻结输入，不再读取当前 `talent_profiles` / `parsed_resumes` 的内容；排队期间
   人工修订、重新解析、字段变更都不影响本批，历史回读不读取新版本。
   仅「软删/不存在跳过」仍按当前档案存在性判断（T-24 既有语义）。
   `config_snapshot_json` 仍只放岗位配置，不混入成员数据（引擎按白名单读它）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.dao import job_profiles as job_dao
from app.dao import screen as screen_dao
from app.models import (
    JobProfile,
    ParsedResume,
    ResumeFile,
    ScreenBatch,
    ScreenResult,
    TalentProfile,
    TalentProfileVersion,
)

logger = logging.getLogger(__name__)

BATCH_NOT_FOUND = "筛选批次不存在"
RESULT_NOT_FOUND = "筛选结果不存在"
JOB_NOT_FOUND = "岗位不存在"
SCOPE_TYPES = ("all_unscreened", "upload_batch", "resume_file_ids", "all")
VERDICTS = ("passed", "pending", "rejected")

IDEMPOTENCY_KEY_MAX = 64


def normalize_idempotency_key(raw: str | None) -> str | None:
    """T-A42：空白视为未提供（行为零变化）；超长显式 400，避免静默截断后误判幂等生效。

    `match_service` 复用同一归一化函数，保证两侧口径一致。
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > IDEMPOTENCY_KEY_MAX:
        raise BizError(400, f"idempotency_key 最长 {IDEMPOTENCY_KEY_MAX} 个字符")
    return key


def resolve_batch_model_versions(
    snapshot: Mapping[str, Any], *, logger: logging.Logger | None = None
) -> tuple[str, str]:
    """T-A42：结果行版本必须来自批次快照（同批次一致 + 历史可追溯）。

    A_0010 之前创建的历史批次快照没有版本字段：回退当前配置并告警，不阻断
    retry；这类历史批次无法追溯原始模型口径，属已知限制，不做静默伪装。
    """
    model = snapshot.get("model_version")
    prompt = snapshot.get("prompt_version")
    if isinstance(model, str) and model and isinstance(prompt, str) and prompt:
        return model, prompt
    if logger is not None:
        logger.warning(
            "批次快照缺 model_version/prompt_version，按当前配置回退（历史批次无法追溯）"
        )
    return settings.llm_model, settings.prompt_version


# --------------------------------------------------------------------------- #
# A03 批次档案冻结（创建时一次性值拷贝 + 绑定 B 正式版本）
# --------------------------------------------------------------------------- #
FREEZE_SCHEMA_VERSION = "A03-v1"
FREEZE_SOURCE_PERSISTED = "b_persisted"
FREEZE_SOURCE_BASELINE = "a_baseline"

# B 契约 UPD-B-20261107-002845 §5 的 profile_input 兼容键；只取评分所需子集，
# 不把 `sources` / `source_pages` 原文整段塞进批次表（体积与泄露边界）。
_PERSISTED_PROFILE_KEYS = (
    "name", "degree", "work_years", "skills", "vector_status", "current_title",
    "expected_title", "tags", "ability_scores", "manual_edited_fields",
)
_PERSISTED_PARSED_KEYS = ("educations", "employments", "projects", "skills", "certificates")


def _plain(value: Any) -> Any:
    """把 ORM 行/Decimal/日期/嵌套 JSON 转成纯值拷贝，杜绝引用污染与序列化失败。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        # date.isoformat() 不接受 timespec，单独处理避免 TypeError
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return str(value)


def _latest_parsed(db: Session, talent_ids: list[int]) -> dict[int, ParsedResume]:
    """每人取最新一条解析结果（与两个 worker 原有 `_evidence_input` 同口径）。"""
    if not talent_ids:
        return {}
    rows = db.execute(
        select(ParsedResume)
        .where(ParsedResume.talent_profile_id.in_(talent_ids))
        .order_by(ParsedResume.created_at.desc(), ParsedResume.id.desc())
    ).scalars()
    latest: dict[int, ParsedResume] = {}
    for row in rows:
        latest.setdefault(row.talent_profile_id, row)
    return latest


def build_profile_input(profile: TalentProfile, parsed: ParsedResume | None) -> dict[str, Any]:
    """A 侧评分输入（与 worker 原有拼法完全一致）；A03 冻结与历史回退共用。"""
    profile_input: dict[str, Any] = {
        "talent_profile_id": profile.id,
        "name": profile.name,
        "degree": profile.degree,
        "work_years": float(profile.work_years) if profile.work_years is not None else None,
        "skills_json": _plain(list(profile.skills_json or [])),
        "vector_status": profile.vector_status,
        "current_title": profile.current_title,
        "expected_title": profile.expected_title,
        "tags_json": _plain(list(profile.tags_json or [])),
        "ability_scores_json": _plain(profile.ability_scores_json),
        "manual_edited_fields": _plain(list(profile.manual_edited_fields or [])),
    }
    if parsed is None or not parsed.structured_json:
        profile_input["missing_fields"] = ["resume_parsed"]
    else:
        structured = parsed.structured_json if isinstance(parsed.structured_json, dict) else {}
        profile_input.update(
            {
                "educations": _plain(list(structured.get("educations") or [])),
                "employments": _plain(list(structured.get("employments") or [])),
                "projects": _plain(list(structured.get("projects") or [])),
            }
        )
        parsed_skills = _plain(list(structured.get("skills") or []))
        if parsed_skills and not profile_input["skills_json"]:
            profile_input["skills_json"] = parsed_skills
    return _plain(profile_input)


def _latest_persisted_versions(db: Session, talent_ids: list[int]) -> dict[int, TalentProfileVersion]:
    """只读 B 的不可变版本行，一次批量取每人最新版本（A03 / B 契约阶段 2）。

    直接读 `talent_profile_versions.profile_snapshot_json`，不调用
    `history.read_version()` 的 evidence 组装：评分输入不需要逐字段证据，且创建
    阶段需控制查询数。B 的版本行只追加不改写，故按版本回读天然不可变。
    """
    if not talent_ids:
        return {}
    rows = db.execute(
        select(TalentProfileVersion)
        .where(TalentProfileVersion.talent_profile_id.in_(talent_ids))
        .order_by(
            TalentProfileVersion.talent_profile_id,
            TalentProfileVersion.version.desc(),
        )
    ).scalars()
    latest: dict[int, TalentProfileVersion] = {}
    for row in rows:
        latest.setdefault(row.talent_profile_id, row)
    return latest


def _persisted_binding(row: TalentProfileVersion) -> tuple[dict[str, Any], str | None, int | None]:
    """从 B 版本行提取可核对的内容摘要、快照时间与正式版本号。"""
    raw = row.profile_snapshot_json if isinstance(row.profile_snapshot_json, dict) else {}
    profile_raw = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    parsed_raw = raw.get("parsed") if isinstance(raw.get("parsed"), dict) else {}
    content = {
        "profile": {k: _plain(profile_raw.get(k)) for k in _PERSISTED_PROFILE_KEYS},
        "parsed": {k: _plain(parsed_raw.get(k)) for k in _PERSISTED_PARSED_KEYS},
    }
    version = raw.get("profile_version")
    if not isinstance(version, int):
        version = int(row.version)
    return content, _plain(raw.get("snapshot_at")), version


def capture_batch_profile_inputs(
    db: Session,
    talent_ids: list[int],
    *,
    profiles: Mapping[int, TalentProfile] | None = None,
) -> dict[str, Any]:
    """创建批次时冻结每位成员的评分输入 + B 正式版本/内容摘要（A03 / F03）。

    冻结后 worker 只读取本 JSON 的 `profile_input`，不再读取当前档案内容；排队
    期间的人工修订、重新解析、字段变更都不影响本批。B 版本表尚无该人记录时
    以 `a_baseline` 标记并仍冻结 A 侧值拷贝，不阻断创建（历史数据兼容）。
    """
    ids = [int(i) for i in talent_ids]
    profile_map = dict(profiles) if profiles is not None else screen_dao.list_profiles_by_ids(db, ids)
    parsed_map = _latest_parsed(db, ids)
    versions = _latest_persisted_versions(db, ids)
    items: dict[str, Any] = {}
    for tid in ids:
        profile = profile_map.get(tid)
        if profile is None:
            # 圈人后瞬时消失：不冻结，worker 仍按「软删/不存在」跳过
            continue
        item: dict[str, Any] = {
            "profile_input": build_profile_input(profile, parsed_map.get(tid)),
            "profile_version": None,
            "version_source": FREEZE_SOURCE_BASELINE,
            "snapshot_at": None,
            "content_sha256": None,
            "persisted_content": None,
        }
        row = versions.get(tid)
        if row is not None:
            content, snapshot_at, version = _persisted_binding(row)
            item.update(
                {
                    "profile_version": version,
                    "version_source": FREEZE_SOURCE_PERSISTED,
                    "snapshot_at": snapshot_at,
                    "content_sha256": hashlib.sha256(
                        json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                    "persisted_content": content,
                }
            )
        items[str(tid)] = item
    return {
        "schema": FREEZE_SCHEMA_VERSION,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }


def frozen_profile_input(frozen: Any, talent_id: int) -> dict[str, Any] | None:
    """worker 取冻结输入；缺失/未知 schema 返回 None 让调用方显式回退并告警。"""
    if not isinstance(frozen, Mapping) or frozen.get("schema") != FREEZE_SCHEMA_VERSION:
        return None
    items = frozen.get("items")
    if not isinstance(items, Mapping):
        return None
    item = items.get(str(int(talent_id)))
    if not isinstance(item, Mapping) or not isinstance(item.get("profile_input"), Mapping):
        return None
    copy = _plain(dict(item["profile_input"]))
    return copy if isinstance(copy, dict) else None


def frozen_inputs_summary(frozen: Any) -> dict[str, Any]:
    """详情接口摘要：只给数量/版本来源/时间，**不回传全量成员冻结输入**。"""
    if not isinstance(frozen, Mapping) or frozen.get("schema") != FREEZE_SCHEMA_VERSION:
        return {
            "available": False,
            "schema": None,
            "frozen_count": 0,
            "captured_at": None,
            "version_sources": {},
        }
    items = frozen.get("items")
    items = items if isinstance(items, Mapping) else {}
    sources: dict[str, int] = {}
    for item in items.values():
        key = item.get("version_source") if isinstance(item, Mapping) else None
        key = str(key or "unknown")
        sources[key] = sources.get(key, 0) + 1
    return {
        "available": True,
        "schema": FREEZE_SCHEMA_VERSION,
        "frozen_count": len(items),
        "captured_at": _plain(frozen.get("captured_at")),
        "version_sources": sources,
    }


# --------------------------------------------------------------------------- #
# scope 圈定（仅创建批次时执行一次，D-D）
# --------------------------------------------------------------------------- #
def _validate_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    scope_type = scope.get("type")
    if scope_type not in SCOPE_TYPES:
        raise BizError(400, f"scope.type 仅支持 {' / '.join(SCOPE_TYPES)}")
    normalized: dict[str, Any] = {"type": scope_type}
    if scope_type == "upload_batch":
        batch_id = (scope.get("upload_batch_id") or "").strip()
        if not batch_id:
            raise BizError(400, "upload_batch scope 需要提供 upload_batch_id")
        normalized["upload_batch_id"] = batch_id
    elif scope_type == "resume_file_ids":
        ids = scope.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and i > 0 for i in ids):
            raise BizError(400, "resume_file_ids scope 需要提供非空的 ids 数组（正整数）")
        if len(ids) > 1000:
            raise BizError(400, "resume_file_ids 单次最多 1000 个文件")
        normalized["ids"] = sorted(set(ids))
    return normalized


def resolve_talent_ids(db: Session, job_profile_id: int, scope: Mapping[str, Any]) -> list[int]:
    """按 scope 语义圈定"解析成功且已有画像"的人才 id 清单（附录 B）。

    只在本函数里查库圈人；返回升序去重后的 id 列表（快照写入 scope_json.resolved_ids）。
    """
    scope_type = scope["type"]
    base = (
        select(TalentProfile.id)
        .join(ParsedResume, ParsedResume.talent_profile_id == TalentProfile.id)
        .where(TalentProfile.deleted_at.is_(None))
    )
    if scope_type == "all":
        stmt = base
    elif scope_type == "all_unscreened":
        # 该岗位在 screen_results 中无任何记录的人才（不重复筛）
        from app.models import ScreenResult

        screened = select(ScreenResult.talent_profile_id).where(
            ScreenResult.job_profile_id == job_profile_id
        )
        stmt = base.where(TalentProfile.id.not_in(screened))
    elif scope_type == "upload_batch":
        stmt = base.join(
            ResumeFile, ResumeFile.id == ParsedResume.resume_file_id
        ).where(ResumeFile.upload_batch_id == scope["upload_batch_id"])
    else:  # resume_file_ids
        stmt = base.where(ParsedResume.resume_file_id.in_(scope["ids"]))
    return sorted(set(db.execute(stmt).scalars().all()))


def build_config_snapshot(job: JobProfile) -> dict[str, Any]:
    """批次快照（与 `job_profile_service._out` 同源字段；引擎/详情页都读它）。"""
    return {
        "title": job.title,
        "department": job.department,
        "jd_text": job.jd_text,
        "requirements_text": job.requirements_text,
        "min_degree": job.min_degree,
        "min_work_years": float(job.min_work_years) if job.min_work_years is not None else None,
        "must_skills": list(job.must_skills_json or []),
        "bonus_skills": list(job.bonus_skills_json or []),
        "hard_rule_switches": dict(job.hard_rule_switches_json),
        "pass_threshold": float(job.pass_threshold),
        "pending_threshold": float(job.pending_threshold),
        "weights": dict(job.weights_json),
        # T-A42：模型/Prompt 版本在创建时随批次固定；worker 只读快照不读运行时 settings。
        "model_version": settings.llm_model,
        "prompt_version": settings.prompt_version,
    }


# --------------------------------------------------------------------------- #
# 创建 / 重试 / 查询
# --------------------------------------------------------------------------- #
def _find_batch_by_key(db: Session, job_profile_id: int, key: str) -> ScreenBatch | None:
    """T-A42：幂等键限定在同一岗位内（组合唯一约束同口径）。"""
    return db.execute(
        select(ScreenBatch)
        .where(ScreenBatch.job_profile_id == job_profile_id)
        .where(ScreenBatch.idempotency_key == key)
        .order_by(ScreenBatch.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _replay_payload(db: Session, batch: ScreenBatch) -> dict[str, Any]:
    """重复请求回原批次：**不重新圈人**，estimated 仍按快照成员计算。"""
    resolved_ids = list(dict(batch.scope_json or {}).get("resolved_ids") or [])
    profiles = screen_dao.list_profiles_by_ids(db, resolved_ids)
    return {
        "batch_id": batch.id,
        "total": batch.total,
        "status": batch.status,
        "estimated_llm_calls": sum(1 for tid in resolved_ids if profiles.get(tid) is not None),
    }


def create_batch(
    db: Session,
    job_profile_id: int,
    scope: Mapping[str, Any],
    *,
    user_id: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """同步阶段（≤2s）：校验 → 圈人 → 预检 → 落批次行。返回 202 data。

    T-A42：显式 `idempotency_key` 命中已有批次时直接返回原批次，不新建、
    不重圈人；并发插入由唯一约束兜底（IntegrityError → 回查原批次）。
    """
    key = normalize_idempotency_key(idempotency_key)
    if key is not None:
        existing = _find_batch_by_key(db, job_profile_id, key)
        if existing is not None:
            return _replay_payload(db, existing)

    job = job_dao.get(db, job_profile_id)
    if job is None:
        raise BizError(404, JOB_NOT_FOUND)
    if job.is_active == 0:
        raise BizError(400, "岗位已停用，不能发起筛选（历史批次可回看）")

    normalized_scope = _validate_scope(scope)
    resolved_ids = resolve_talent_ids(db, job_profile_id, normalized_scope)
    if not resolved_ids:
        raise BizError(400, "范围内没有可筛选对象（需解析成功且已有画像）")

    snapshot = build_config_snapshot(job)
    profiles = screen_dao.list_profiles_by_ids(db, resolved_ids)
    # A02-1：所有人都会进 Prompt C，不再用 hard_gate 折减调用数
    estimated = sum(1 for talent_id in resolved_ids if profiles.get(talent_id) is not None)
    # A03：批次开始时一次性冻结成员输入 + B 正式版本（排队期档案变更不影响本批）
    frozen_inputs = capture_batch_profile_inputs(db, resolved_ids, profiles=profiles)

    try:
        batch = screen_dao.create_batch(
            db,
            job_profile_id=job_profile_id,
            job_profile_version=job.version,
            config_snapshot_json=snapshot,
            scope_json={**normalized_scope, "resolved_ids": resolved_ids},
            frozen_inputs_json=frozen_inputs,
            status="pending",
            total=len(resolved_ids),
            created_by=user_id,
            idempotency_key=key,
        )
        db.commit()
    except IntegrityError:
        # 并发窗口：另一个请求已用同键建批次。回查并返回原批次，不向外抛 500。
        db.rollback()
        existing = _find_batch_by_key(db, job_profile_id, key) if key is not None else None
        if existing is not None:
            return _replay_payload(db, existing)
        raise
    db.refresh(batch)
    return {
        "batch_id": batch.id,
        "total": batch.total,
        "status": batch.status,
        "estimated_llm_calls": estimated,
    }


def retry_batch(db: Session, batch_id: int) -> dict[str, Any]:
    """复用原批次重试（D-E）：仅 failed；快照/total 不变、progress 按结果行数重算。"""
    batch = screen_dao.get_batch(db, batch_id)
    if batch is None:
        raise BizError(404, BATCH_NOT_FOUND)
    if batch.status != "failed":
        raise BizError(400, "仅 failed 状态的批次可以重试")
    batch.status = "pending"
    batch.error = None
    batch.started_at = None
    batch.heartbeat_at = None
    batch.finished_at = None
    batch.vector_degraded = 0
    batch.retry_count += 1
    batch.progress = screen_dao.count_results(db, batch_id)
    db.commit()
    db.refresh(batch)
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "retry_count": batch.retry_count,
        "total": batch.total,
        "progress": batch.progress,
    }


def get_batch_detail(db: Session, batch_id: int) -> dict[str, Any]:
    """批次详情（轮询用）：三档计数 + processed + skipped_deleted + scope 摘要。

    - `processed` = 已写结果行数；
    - `skipped_deleted` = 快照内已软删/不存在 **且** 尚无结果行的成员数（done 后即跳过总数）；
    - `scope` 返回原始条件但**不含 resolved_ids 全量**，只给 `resolved_ids_count`。
    """
    batch = screen_dao.get_batch(db, batch_id)
    if batch is None:
        raise BizError(404, BATCH_NOT_FOUND)
    scope = dict(batch.scope_json or {})
    resolved_ids: list[int] = scope.pop("resolved_ids", [])
    scope["resolved_ids_count"] = len(resolved_ids)

    processed = screen_dao.count_results(db, batch_id)
    written = screen_dao.result_talent_ids(db, batch_id)
    skipped_deleted = 0
    if resolved_ids:
        profiles = screen_dao.list_profiles_by_ids(db, resolved_ids)
        for talent_id in resolved_ids:
            if talent_id in written:
                continue
            row = profiles.get(talent_id)
            if row is None or row.deleted_at is not None:
                skipped_deleted += 1

    counts = screen_dao.count_by_effective_verdict(db, batch_id)
    return {
        "batch_id": batch.id,
        "job_profile_id": batch.job_profile_id,
        "job_profile_version": batch.job_profile_version,
        "config_snapshot": dict(batch.config_snapshot_json or {}),
        "scope": scope,
        # A03：只回摘要，不回传全量成员冻结输入（与 resolved_ids 不外泄同口径）
        "frozen_inputs": frozen_inputs_summary(batch.frozen_inputs_json),
        "status": batch.status,
        "progress": batch.progress,
        "total": batch.total,
        "passed": counts["passed"],
        "pending_count": counts["pending"],
        "rejected": counts["rejected"],
        "processed": processed,
        "skipped_deleted": skipped_deleted,
        "vector_degraded": bool(batch.vector_degraded),  # 筛选批次恒为 False（算法 §4.0）
        "error": batch.error,
        "retry_count": batch.retry_count,
        "created_at": batch.created_at,
        "finished_at": batch.finished_at,
    }


def _result_item(result: ScreenResult, profile: TalentProfile | None) -> dict[str, Any]:
    return {
        "result_id": result.id,
        "talent_profile_id": result.talent_profile_id,
        "name": profile.name if profile is not None else "（已删除）",
        "deleted": profile is None or profile.deleted_at is not None,
        "current_title": profile.current_title if profile is not None else None,
        "degree": profile.degree if profile is not None else None,
        "work_years": float(profile.work_years) if profile is not None and profile.work_years is not None else None,
        "hard_fail": bool(result.hard_fail),
        "hard_fail_reason": result.hard_fail_reason,
        "score": float(result.score) if result.score is not None else None,
        "verdict": result.verdict,
        "manual_verdict": result.manual_verdict,
        "effective_verdict": result.manual_verdict or result.verdict,
        "manual_verdict_by": result.manual_verdict_by,
        "manual_verdict_at": result.manual_verdict_at,
        "reason_summary": result.reason_summary,
        "hits": list(result.hits_json or []),
        "misses": list(result.misses_json or []),
        "created_at": result.created_at,
    }


def list_results(
    db: Session,
    *,
    batch_id: int,
    verdict: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """结果分页；verdict 按**有效结论**过滤（manual_verdict ?? verdict）。"""
    if verdict is not None and verdict not in VERDICTS:
        raise BizError(400, f"verdict 仅支持 {' / '.join(VERDICTS)}")
    rows, total = screen_dao.list_results_page(
        db, batch_id=batch_id, verdict=verdict, keyword=keyword, offset=offset, limit=limit
    )
    items = [_result_item(result, profile) for result, profile in rows]
    return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}


def set_manual_verdict(
    db: Session, result_id: int, manual_verdict: str | None, *, user_id: int
) -> dict[str, Any]:
    """人工改判（F1-10）：写 by/at（撤销置 NULL）→ 重算批次三档计数。改判不改 score。"""
    result = screen_dao.get_result(db, result_id)
    if result is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if manual_verdict is not None and manual_verdict not in VERDICTS:
        raise BizError(400, f"manual_verdict 仅支持 {' / '.join(VERDICTS)} 或 null（撤销）")
    result.manual_verdict = manual_verdict
    if manual_verdict is None:
        result.manual_verdict_by = None
        result.manual_verdict_at = None
    else:
        result.manual_verdict_by = user_id
        result.manual_verdict_at = datetime.now()
    db.flush()  # SessionLocal autoflush=False：改判先落库再重算计数
    counts = screen_dao.count_by_effective_verdict(db, result.screen_batch_id)
    batch = screen_dao.get_batch(db, result.screen_batch_id)
    if batch is not None:
        batch.passed = counts["passed"]
        batch.pending_count = counts["pending"]
        batch.rejected = counts["rejected"]
    db.commit()
    db.refresh(result)
    profile = screen_dao.list_profiles_by_ids(db, [result.talent_profile_id]).get(result.talent_profile_id)
    return _result_item(result, profile)


def get_batch_row(db: Session, batch_id: int) -> ScreenBatch | None:
    return screen_dao.get_batch(db, batch_id)


def list_batches_with_title(
    db: Session, *, job_profile_id: int | None = None, offset: int = 0, limit: int = 20
) -> tuple[list[tuple[ScreenBatch, str | None]], int]:
    """批次列表（附岗位标题）；T-24 列表接口用。"""
    return screen_dao.list_batches(db, job_profile_id=job_profile_id, offset=offset, limit=limit)
