"""解析流水线 worker（T-13）· 只做编排，业务规则都在服务层。

流水线（架构 §6.1 / 数据库 §4.2.1）：

    提取文本(T-11) → Prompt A 结构化(parse_service) → 写 parsed_resumes
      → 查重(T-17)  ──命中──> talent_duplicates(pending) + 任务 duplicate_pending（不建画像）
      → 建画像/标签/能力/向量(T-16) → 任务 success

**断点续跑（决策 D-I + D-G）**：认领后先看 `parse_tasks.stage` 与
`parsed_resumes.talent_profile_id`，据此决定入口（`resolve_entry()` 是这张表的唯一实现）：

| 入口 stage | 已有画像归属 | 执行范围 |
|---|---|---|
| NULL / extract | — | 提取 → Prompt A → 查重 → 画像 |
| profile | 否 | 跳过提取，**从查重开始** |
| profile | 是 | 跳过查重，只补标签/能力/向量（`build_profile` 内部按 `tags_json`/`ability_scores_json` 只补缺失子步骤） |
| vector | 是 | **只补 embedding + upsert，不跑任何 LLM** |

**三重幂等**：①查重排除 `parsed_resumes.talent_profile_id` 指向的画像（`dedup_service.detect`
已实现，否则"画像已建 + 向量失败"重试会把自己判成重复人）；②建画像与回填 `talent_profile_id`
同事务（`profile_service.build_profile` 已实现）；③查重记录按 `parsed_resume_id` 唯一
（`dedup_service.flag` 已实现）。

**stage 的写入口径**：进入某阶段先 `stage=该阶段` 再执行，成功后推进到下一阶段，所以
进程被杀/心跳超时后恢复的起点就是"最后失败的那一步"，不会白跑已经完成的 LLM。
`status` 的终态（success/failed/duplicate_pending）由本文件写；runner 只兜未捕获异常。

失败语义：`ParseError` / `ProfileError` 带 `stage`，直接落 `failed(stage, error)`；
其余未预期异常按当前 `stage` 落 failed（避免永远 running），**不向上抛**——抛出去 runner
也会兜成 failed，但会丢掉 stage。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.dao import parsed_resumes as parsed_dao
from app.dao import talent_profiles as profile_dao
from app.database import SessionLocal
from app.models import ParsedResume, ParseTask, ResumeFile, TalentProfile
from app.service import dedup_service, parse_service, profile_service
from app.service.extract_service import ExtractError, extract_text
from app.service.parse_service import ParseError
from app.service.profile_service import ProfileError
from app.service.upload_service import resolve_stored_path
from app.modules.files.processing import begin_attempt, finish_attempt, store_pages, trace_attempt, append_model_call, attempt_scope

logger = logging.getLogger("app.parse_worker")

STAGE_EXTRACT = "extract"
STAGE_PROFILE = "profile"
STAGE_VECTOR = "vector"

#: 入口（内部枚举，不落库）
ENTRY_EXTRACT = "extract"
ENTRY_DEDUP = "dedup"
ENTRY_PROFILE = "profile"
ENTRY_VECTOR = "vector"

ERROR_MAX_CHARS = 2000


# --------------------------------------------------------------------------- #
# 入口解析（纯函数，单测直接覆盖这张表）
# --------------------------------------------------------------------------- #
def resolve_entry(stage: str | None, *, has_profile: bool) -> str:
    """按数据库 §4.2.1 的断点表决定入口；未知 stage 按全流程重跑（保守）。"""
    key = (stage or "").strip().lower()
    if key in ("", STAGE_EXTRACT):
        return ENTRY_EXTRACT
    if key == STAGE_PROFILE:
        # 已建画像 → 只补标签/能力；未建 → 从查重开始（keep 路径与"标量已提交但标签失败"）
        return ENTRY_PROFILE if has_profile else ENTRY_DEDUP
    if key == STAGE_VECTOR:
        # 正常应有画像；缺失说明状态被外部改坏，退回查重（不会对已有画像重复建行）
        return ENTRY_VECTOR if has_profile else ENTRY_DEDUP
    logger.warning("解析任务 stage=%r 非法，按全流程重跑", stage)
    return ENTRY_EXTRACT


# --------------------------------------------------------------------------- #
# runner handler
# --------------------------------------------------------------------------- #
def handle_parse(task_id: int) -> None:
    """runner 注册的执行体：接收 `parse_tasks.id`，自己开 session、自己写终态。"""
    with SessionLocal() as db:
        task = db.get(ParseTask, task_id)
        if task is None:
            logger.warning("解析任务 %s 不存在（可能已被清理），跳过", task_id)
            return
        if task.status != "running":
            # 认领与派发之间被 retry/删除改了状态：不覆盖调用方意图
            logger.warning("解析任务 %s 状态为 %s（非 running），跳过", task_id, task.status)
            return

        attempt = begin_attempt(db, task)
        try:
            with attempt_scope(attempt.id):
                run_task(db, task)
        except (ParseError, ProfileError) as exc:
            _fail(db, task, getattr(exc, "stage", None) or STAGE_EXTRACT, str(exc))
        except BizError as exc:
            _fail(db, task, task.stage or STAGE_PROFILE, str(exc))
        except Exception as exc:  # noqa: BLE001 - 不能把任务永远留在 running
            logger.exception("解析任务 %s 未预期异常", task_id)
            _fail(db, task, task.stage or STAGE_EXTRACT, f"{type(exc).__name__}: {exc}")
        finally:
            current = db.get(ParseTask, task.id)
            finish_attempt(db, attempt, status=current.status, stage=current.stage, error=current.error)


def run_task(db: Session, task: ParseTask) -> None:
    """执行一个已认领（running）的解析任务；成功/挂起由本函数写，失败向上抛。"""
    file_row = db.get(ResumeFile, task.resume_file_id)
    if file_row is None:
        raise ParseError("简历文件记录不存在（resume_files 行缺失）", stage=STAGE_EXTRACT)

    parsed = parsed_dao.get_by_resume_file(db, task.resume_file_id)
    has_profile = bool(parsed is not None and parsed.talent_profile_id)
    entry = resolve_entry(task.stage, has_profile=has_profile)
    logger.info(
        "解析任务 %s 开始：resume_file=%s entry=%s stage=%s has_profile=%s",
        task.id,
        task.resume_file_id,
        entry,
        task.stage or "-",
        has_profile,
    )

    if entry == ENTRY_VECTOR:
        _run_vector(db, task, parsed)
        return
    if entry == ENTRY_PROFILE:
        _run_profile(db, task, parsed)
        return

    if entry == ENTRY_EXTRACT or parsed is None:
        parsed = _run_extract(db, task, file_row, parsed)

    if _run_dedup(db, task, parsed):
        return
    _run_profile(db, task, parsed)


# --------------------------------------------------------------------------- #
# 各阶段
# --------------------------------------------------------------------------- #
def _run_extract(
    db: Session, task: ParseTask, file_row: ResumeFile, parsed: ParsedResume | None
) -> ParsedResume:
    """提取 + Prompt A + 写 parsed_resumes（按 `resume_file_id` upsert，绝不插第二行）。"""
    _beat(db, task, stage=STAGE_EXTRACT)
    path = resolve_stored_path(file_row.stored_path)
    try:
        result = extract_text(path, ext=file_row.file_ext)
    except ExtractError as exc:
        store_pages(db, file_row.id, exc.pages)
        db.commit()
        raise ParseError(str(exc), stage=STAGE_EXTRACT) from exc

    store_pages(db, file_row.id, result.pages)
    task.truncated = 1 if result.truncated else 0
    trace_attempt(db, task.id, parse_input=result.text)
    _beat(db, task)  # 提取可能较慢，先刷一次心跳再进 LLM
    with parse_service.capture_calls(lambda event: append_model_call(db, task.id, event)):
        structured = parse_service.extract_structured(result.text)
    trace_attempt(db, task.id, parse_output=structured)

    parsed = parsed_dao.upsert(
        db,
        resume_file_id=file_row.id,
        structured_json=structured,
        raw_text=result.text,
        llm_model=settings.llm_model,
    )
    task.truncated = 1 if result.truncated else 0
    # 提取完成 → 断点推进到 profile：中途崩溃重试从查重开始，不再重复调 Prompt A
    _beat(db, task, stage=STAGE_PROFILE)
    logger.info(
        "解析任务 %s 提取完成：chars=%d truncated=%s parsed=%s",
        task.id,
        len(result.text),
        result.truncated,
        parsed.id,
    )
    return parsed


def _run_dedup(db: Session, task: ParseTask, parsed: ParsedResume) -> bool:
    """查重；命中则挂起（`duplicate_pending`）并返回 True。"""
    match = dedup_service.detect(db, parsed, structured=parsed.structured_json)
    if match is None:
        return False
    dedup_service.flag(db, parsed, match)  # 内部 commit：写/重置记录 + 任务转 duplicate_pending
    logger.info(
        "解析任务 %s 命中重复画像 %s（%s），任务挂起待处理（不建画像）",
        task.id,
        match.profile_id,
        match.reason,
    )
    return True


def _run_profile(db: Session, task: ParseTask, parsed: ParsedResume | None) -> None:
    """建画像 + 标签 + 能力 + 向量；`build_profile` 内部按已落库子步骤只补缺失。"""
    if parsed is None:
        raise ParseError("缺少 parsed_resumes 行，无法生成画像", stage=STAGE_PROFILE)

    _beat(db, task, stage=STAGE_PROFILE)
    # ProfileError 自带 stage=profile，直接向上抛
    result = profile_service.build_profile(db, parsed, structured=parsed.structured_json)

    if not result.ok:
        error = getattr(result.vector, "error", None) or "向量同步失败"
        # 画像/标签已提交，仅向量没写成（半写口径）→ failed(stage=vector) 可断点续跑
        raise ParseError(error, stage=STAGE_VECTOR)

    _success(db, task, parsed, profile_id=result.profile.id)


def _run_vector(db: Session, task: ParseTask, parsed: ParsedResume | None) -> None:
    """只补向量：零 LLM 调用（验收点「stage=vector 重试不产生新的 LLM 调用」）。"""
    profile_id = parsed.talent_profile_id if parsed is not None else None
    if profile_id is None:
        raise ParseError("stage=vector 但画像归属缺失，无法只补向量", stage=STAGE_PROFILE)

    profile = db.get(TalentProfile, profile_id)
    if profile is None:
        raise ParseError(f"画像 {profile_id} 不存在（可能已被删除）", stage=STAGE_PROFILE)

    _beat(db, task, stage=STAGE_VECTOR)
    # 经历类不落画像表：用聚合结果拼文本，保证与解析/合并期算出的 hash 一致
    structured = profile_service.aggregate_structured(db, profile.id)
    tags = profile_dao.get_tags(db, profile.id)
    vector = profile_service.sync_profile_vector(
        profile,
        structured=structured,
        tags=tags,
        budget=settings.vector_sync_timeout,
    )
    if vector is not None and not vector.ok:
        raise ParseError(vector.error or "向量同步失败", stage=STAGE_VECTOR)

    logger.info(
        "解析任务 %s 向量补齐完成：profile=%s embedding=%s",
        task.id,
        profile.id,
        "skipped(hash 未变)" if vector is None else "called",
    )
    _success(db, task, parsed, profile_id=profile.id)


# --------------------------------------------------------------------------- #
# 状态写入
# --------------------------------------------------------------------------- #
def _beat(db: Session, task: ParseTask, *, stage: str | None = None) -> None:
    """刷心跳（并可推进 stage）并提交：长任务被 runner 误判卡死的唯一防线。"""
    task.heartbeat_at = datetime.now()
    if stage:
        task.stage = stage
    db.commit()


def _success(db: Session, task: ParseTask, parsed: ParsedResume, *, profile_id: int) -> None:
    """success 必须同时具备画像归属与 `profiled_at/finished_at`（T-13 验收点）。"""
    if not parsed.talent_profile_id:
        raise ParseError("画像归属未回填，拒绝标记 success", stage=STAGE_PROFILE)

    now = datetime.now()
    task.status = "success"
    task.stage = None
    task.error = None
    task.profiled_at = now
    task.finished_at = now
    task.heartbeat_at = None
    db.commit()
    logger.info("解析任务 %s 成功：profile=%s", task.id, profile_id)


def _fail(db: Session, task: ParseTask, stage: str, message: str) -> None:
    """落 failed(stage, error)；`stage` 保留为断点（retry 从该阶段续跑）。"""
    stage = (stage or STAGE_EXTRACT)[:20]
    try:
        db.rollback()  # 丢掉本阶段未提交的脏改动，保住已提交的断点数据
    except Exception:  # noqa: BLE001 - rollback 失败也要把终态写上
        logger.warning("解析任务 %s rollback 失败", task.id, exc_info=True)

    row = db.get(ParseTask, task.id)
    if row is None:  # 极端情况：任务行被并发删除
        logger.error("解析任务 %s 行已不存在，无法写 failed", task.id)
        return

    row.status = "failed"
    row.stage = stage
    row.error = (message or "未知错误")[:ERROR_MAX_CHARS]
    row.finished_at = datetime.now()
    row.heartbeat_at = None
    db.commit()
    logger.warning("解析任务 %s 失败 stage=%s：%s", task.id, stage, row.error)


__all__ = [
    "ENTRY_DEDUP",
    "ENTRY_EXTRACT",
    "ENTRY_PROFILE",
    "ENTRY_VECTOR",
    "STAGE_EXTRACT",
    "STAGE_PROFILE",
    "STAGE_VECTOR",
    "handle_parse",
    "resolve_entry",
    "run_task",
]
