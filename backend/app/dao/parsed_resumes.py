"""`parsed_resumes` 数据访问（T-13）。

约定与其余 DAO 一致：**不 commit**，事务边界由 worker/service 决定；不做业务判断。

唯一硬约束（数据库 §4.3）：`resume_file_id` 是 UNIQUE，重试时**按键整体更新
`structured_json/raw_text/llm_model`，严禁插第二行**。`talent_profile_id` 不在这里改
（它是"画像归属"，只由 `profile_service.build_profile()` / `dedup_service` 在同一事务里回填）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ParsedResume


def get_by_resume_file(db: Session, resume_file_id: int) -> ParsedResume | None:
    return db.execute(
        select(ParsedResume).where(ParsedResume.resume_file_id == resume_file_id)
    ).scalar_one_or_none()


def upsert(
    db: Session,
    *,
    resume_file_id: int,
    structured_json: dict[str, Any],
    raw_text: str,
    llm_model: str | None = None,
) -> ParsedResume:
    """按 `resume_file_id` 存在则整体更新、不存在则插入（断点续跑的安全写法）。

    - **不重置 `talent_profile_id`**：重跑提取时画像可能已存在（keep 后台补齐、
      `stage=profile` 续跑），清空它会制造"孤儿画像 + 重复建画像"；
    - 赋新 dict 而不是原地改，保证 SQLAlchemy 能识别 JSON 列的变更。
    """
    row = get_by_resume_file(db, resume_file_id)
    if row is None:
        row = ParsedResume(
            resume_file_id=resume_file_id,
            talent_profile_id=None,
            structured_json=structured_json,
            raw_text=raw_text,
            llm_model=llm_model,
        )
        db.add(row)
    else:
        row.structured_json = structured_json
        row.raw_text = raw_text
        row.llm_model = llm_model
    db.flush()
    return row


__all__ = ["get_by_resume_file", "upsert"]
