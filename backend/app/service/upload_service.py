"""批量上传与批次汇总（T-10，需求 F1-1 / 接口 §4）。

职责边界（架构 §5.2 明确要求）：**落盘 + 建行都在本文件**，
`api/resumes.py` 只接收 `UploadFile`、调用本服务、返回 202，不碰文件系统与 MySQL。
`api → service → dao → models` 的依赖方向也在此体现：本文件只调 dao，不写裸 SQL 之外的
ORM 查询（批次汇总的聚合查询放在 `dao/resume_files.py`）。

实现要点与原因：
1. **先校验再落盘**：扩展名 → 大小 → magic bytes → SHA-256，任一步失败都不产生任何文件与行。
2. **重复不报错**（接口 §4 + 决策 D-H）：`resume_files` 行永不物理删除，所以同一份内容
   重传必然命中 `uk_file_hash`；响应 202 + `duplicates`，其中 `can_requeue` 让前端对
   `cancelled`/`failed` 的任务给"重新入队"入口。
3. **逐文件提交**：一次上传 20 份，第 18 份遇到数据库异常时，前 17 份按已受理返回，
   不必整批回滚（否则前端无法知道哪些已入队）。异常文件会删掉刚写的磁盘文件，不留孤儿。
4. **并发重传**：`uk_file_hash` 是最后一道闸。两个请求同时传同一文件时，后到的
   `commit()` 会抛 `IntegrityError`，此时按"命中重复"处理而不是 500。
5. **批次不是表（决策 D-M）**：一次调用只生成一个 ULID，原样写进每行
   `resume_files.upload_batch_id`；`list_batches()` 由 dao 侧 GROUP BY 现算。
   数据库里没有 `upload_batches`，**也不要新增**（一期 13 张表冻结）。
6. **文件名不做编码转码（H-13，不是缺陷）**：`UploadFile.filename` 由 multipart 头
   按 UTF-8（浏览器/TestClient）解出后原样保存；Git Bash 的 curl 会按 GBK 发字节，
   命令行验证时出现乱码属工具侧问题，禁止在服务端加 latin-1→GBK 猜测——
   那会把合法的西欧文件名转坏。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ulid import ULID

from app.config import BASE_DIR, settings
from app.core.errors import BizError
from app.dao import parse_tasks as parse_task_dao
from app.dao import resume_files as resume_file_dao
from app.utils.files import (
    display_name,
    normalize_ext,
    read_limited,
    safe_join,
    sha256_hex,
    validate_content,
)

logger = logging.getLogger("app.upload")

DUPLICATE_REASON = "文件内容已存在"
#: 400 错误文案里最多逐项列几个文件（列全部会让 message 过长，且前端也不好展示）
MAX_REASON_ITEMS = 10


def _max_bytes() -> int:
    return settings.upload_max_mb * 1024 * 1024


def _store_bytes(data: bytes, ext: str) -> tuple[Path, str]:
    """落盘并返回 (绝对路径, 入库用相对路径)。

    目录按日期分片（`uploads/2026-11-05/`），文件名恒为 UUID——用户提供的文件名
    只进 `resume_files.filename` 字段供展示，**永不参与路径拼接**（路径穿越防护）。
    """
    root = settings.upload_dir_path
    day = datetime.now().strftime("%Y-%m-%d")
    target_dir = safe_join(root, day)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = safe_join(root, day, f"{uuid.uuid4().hex}.{ext}")
    path.write_bytes(data)
    return path, _relative_stored_path(path)


def _relative_stored_path(path: Path) -> str:
    """入库路径用相对 `backend/` 的 POSIX 形式，换机器/换盘符也能解析。

    `stored_path` 是 VARCHAR(500)，相对路径不会超长；超出 `backend/` 的场景（自定义
    UPLOAD_DIR 指向外部盘）退回绝对路径，由 T-34 手册说明迁移方式。
    """
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_stored_path(stored_path: str) -> Path:
    """相对路径 → 绝对路径（T-18 下载原文时用）。绝对路径原样返回。"""
    path = Path(stored_path)
    return path if path.is_absolute() else (BASE_DIR / path)


def _duplicate_item(db: Session, filename: str, resume_file_id: int) -> dict:
    task = parse_task_dao.get_by_resume_file(db, resume_file_id)
    return {
        "filename": filename,
        "reason": DUPLICATE_REASON,
        "resume_file_id": resume_file_id,
        "task_id": task.id if task else None,
        "task_status": task.status if task else None,
        "can_requeue": bool(task and task.status in parse_task_dao.REQUEUEABLE_STATUSES),
    }


def _reject_all_message(rejected: list[dict]) -> str:
    """400 的 message：错误体没有 data 字段（接口 §1.1），所以把逐项原因拼进 message。"""
    parts = [f"{item['filename']}（{item['reason']}）" for item in rejected[:MAX_REASON_ITEMS]]
    detail = "、".join(parts)
    if len(rejected) > MAX_REASON_ITEMS:
        detail += f" 等 {len(rejected)} 个文件"
    return f"无任何有效文件：{detail}" if detail else "无任何有效文件"


def upload_options():
    from app.service.extract_service import SUPPORTED_EXTS
    allowed = [ext for ext in settings.upload_allowed_ext_list if ext in SUPPORTED_EXTS]
    if not settings.ocr_enabled:
        allowed = [ext for ext in allowed if ext not in {"png", "jpg", "jpeg"}]
    return {"allowed_ext": allowed, "max_mb": settings.upload_max_mb,
            "ocr_enabled": settings.ocr_enabled, "max_pages": settings.ocr_max_pages}


def save_batch(db: Session, files: list[UploadFile], uploaded_by: int) -> dict:
    """校验 + 去重 + 落盘 + 建行；返回 `BatchUploadData` 的 dict（由 api 层包 `ok()`）。"""
    # 整批共用一个 ULID = 批次标识；不落批次表（D-M），汇总时按该字段 GROUP BY
    upload_batch_id = str(ULID())
    allowed_ext = set(upload_options()["allowed_ext"])
    max_bytes = _max_bytes()

    accepted = 0
    duplicates: list[dict] = []
    rejected: list[dict] = []

    for upload in files:
        filename = display_name(getattr(upload, "filename", None))
        ext = normalize_ext(filename)

        if not ext or ext not in allowed_ext:
            rejected.append(
                {"filename": filename, "reason": f"非法文件类型，仅支持 {'/'.join(sorted(allowed_ext))}"}
            )
            continue

        data, overflow = read_limited(upload.file, max_bytes)
        if overflow:
            rejected.append({"filename": filename, "reason": f"文件超过 {settings.upload_max_mb}MB"})
            continue
        if not data:
            rejected.append({"filename": filename, "reason": "文件为空"})
            continue

        invalid_reason = validate_content(ext, data)
        if invalid_reason:
            rejected.append({"filename": filename, "reason": invalid_reason})
            continue

        digest = sha256_hex(data)
        existing = resume_file_dao.get_by_hash(db, digest)
        if existing is not None:
            duplicates.append(_duplicate_item(db, filename, existing.id))
            continue

        path: Path | None = None
        try:
            path, stored_path = _store_bytes(data, ext)
            row = resume_file_dao.create(
                db,
                filename=filename,
                stored_path=stored_path,
                file_hash=digest,
                file_ext=ext,
                file_size=len(data),
                upload_batch_id=upload_batch_id,
                uploaded_by=uploaded_by,
            )
            parse_task_dao.create_pending(db, row.id)
            db.commit()
        except IntegrityError:
            # 并发上传同一文件：唯一键兜底，按重复处理而不是 500
            db.rollback()
            if path is not None:
                path.unlink(missing_ok=True)
            existing = resume_file_dao.get_by_hash(db, digest)
            if existing is None:
                raise
            duplicates.append(_duplicate_item(db, filename, existing.id))
            continue
        except Exception:
            db.rollback()
            if path is not None:
                path.unlink(missing_ok=True)
            logger.exception("upload_failed filename=%s batch=%s", filename, upload_batch_id)
            raise

        accepted += 1

    if accepted == 0 and not duplicates:
        # 全部非法/超限：400（接口 §4「无任何有效文件」）；全部重复不算错误，仍 202
        raise BizError(400, _reject_all_message(rejected))

    return {
        "upload_batch_id": upload_batch_id,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "queue_total": parse_task_dao.count_pending(db),
    }


def list_batches(db: Session, *, offset: int, limit: int) -> tuple[list[dict], int]:
    """上传批次汇总（接口 §4）；`total` 是**批次数**，与 items 一一对应。"""
    return resume_file_dao.batch_summaries(db, offset=offset, limit=limit), resume_file_dao.count_batches(db)
