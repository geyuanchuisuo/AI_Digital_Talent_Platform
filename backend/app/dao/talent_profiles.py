"""`talent_profiles` / `talent_tags` 数据访问（T-16）。

约定与其余 DAO 一致：
- **不 commit**：事务边界由 service 决定，这里只 `add` / `flush` / `execute`；
- 不做业务判断（要不要跳过 LLM、要不要重算向量都不在这里）。

`talent_tags` 的写入口只有 `replace_tags()`：先删后插（同事务），沿用
`uk_profile_tag` 兜底幂等；T-18 的 PATCH 也复用它（传 `keep_manual=True` 保留人工标签）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import TalentProfile, TalentTag


def get(db: Session, profile_id: int) -> TalentProfile | None:
    return db.get(TalentProfile, profile_id)


def create(db: Session, **fields: Any) -> TalentProfile:
    row = TalentProfile(**fields)
    db.add(row)
    db.flush()
    return row


def apply_fields(db: Session, profile: TalentProfile, values: dict[str, Any]) -> list[str]:
    """把 `values` 逐字段赋给画像行；返回**实际发生变化**的字段名（供调用方决定是否重算向量）。"""
    changed: list[str] = []
    for key, value in values.items():
        if not hasattr(profile, key):
            raise AttributeError(f"talent_profiles 没有字段 {key}")
        if getattr(profile, key) != value:
            setattr(profile, key, value)
            changed.append(key)
    if changed:
        db.flush()
    return changed


def get_tags(db: Session, profile_id: int) -> list[str]:
    """按 tag 名取标签（画像文本拼接用；排序交给 `core/profile_text`）。"""
    rows = db.execute(
        select(TalentTag.tag).where(TalentTag.talent_profile_id == profile_id)
    ).scalars().all()
    return list(rows)


def get_tag_rows(db: Session, profile_id: int) -> list[TalentTag]:
    return list(
        db.execute(select(TalentTag).where(TalentTag.talent_profile_id == profile_id)).scalars().all()
    )


def replace_tags(
    db: Session,
    profile_id: int,
    tags: Sequence[tuple[str, str]],
    *,
    source: str = "llm",
    keep_manual: bool = False,
) -> int:
    """整体重建标签（同事务）：`tags` 为 `(tag, tag_type)` 序列，调用方已归一化去重。

    - `keep_manual=True` 时保留 `source='manual'` 的标签不动（T-18 PATCH 用）；
    - 返回写入行数。
    """
    stmt = delete(TalentTag).where(TalentTag.talent_profile_id == profile_id)
    if keep_manual:
        stmt = stmt.where(TalentTag.source != "manual")
    db.execute(stmt)

    rows = [
        TalentTag(talent_profile_id=profile_id, tag=tag, tag_type=tag_type, source=source)
        for tag, tag_type in tags
    ]
    if rows:
        db.add_all(rows)
    db.flush()
    return len(rows)


def replace_tag_rows(
    db: Session, profile_id: int, rows: Sequence[tuple[str, str, str]]
) -> int:
    """整体重建标签，**逐行带 source**（T-17 merge 用）。

    与 `replace_tags()` 的区别：后者给所有行同一个 `source`，无法在"并集重建"时
    保留已有人工标签的 `manual` 标记。merge 必须保留来源，否则 HR 手工加的标签
    会被降级成 `llm`，后续 `keep_manual=True` 的清理会把它误删。

    `rows` 为 `(tag, tag_type, source)`，调用方已归一化去重、并对同名标签选定来源。
    """
    db.execute(delete(TalentTag).where(TalentTag.talent_profile_id == profile_id))
    tag_rows = [
        TalentTag(talent_profile_id=profile_id, tag=tag, tag_type=tag_type, source=source)
        for tag, tag_type, source in rows
    ]
    if tag_rows:
        db.add_all(tag_rows)
    db.flush()
    return len(tag_rows)


def delete_tags(db: Session, profile_id: int) -> int:
    """测试/清理用：物理删除标签行。"""
    result = db.execute(delete(TalentTag).where(TalentTag.talent_profile_id == profile_id))
    return int(result.rowcount or 0)


def list_by_ids(db: Session, profile_ids: Iterable[int]) -> list[TalentProfile]:
    ids = list(profile_ids)
    if not ids:
        return []
    return list(
        db.execute(select(TalentProfile).where(TalentProfile.id.in_(ids))).scalars().all()
    )


# --------------------------------------------------------------------------- #
# T-18 画像列表（仅追加，既有函数未动）
# --------------------------------------------------------------------------- #
def _like_pattern(keyword: str) -> str:
    """转义 LIKE 通配符，关键词按字面匹配（`%`/`_` 是用户输入不是模式）。"""
    return "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_page(
    db: Session,
    *,
    deleted: bool = False,
    keyword: str | None = None,
    tag: str | None = None,
    tag_type: str | None = None,
    degree: str | None = None,
    min_years: Decimal | float | None = None,
    max_years: Decimal | float | None = None,
    allowed_ids: tuple[int,...] | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[TalentProfile], int]:
    """画像分页列表（T-18）。

    - `deleted=False`（默认）只看未删除；`deleted=True` 回收站视角只取已软删，
      按 `deleted_at DESC` 排序（接口 §5）；
    - `keyword` 对 name / current_title / skills_json（CAST 成字符）模糊匹配；
    - `tag` 精确匹配走 `talent_tags`（EXISTS，不 join 出重复行）；`tag_type` 可单独过滤；
    - 年限区间过滤天然排除 `work_years IS NULL`；
    - 排序：默认 `updated_at DESC`，回收站 `deleted_at DESC`，同秒内按 id DESC 稳定。
    """
    conds = [TalentProfile.deleted_at.is_not(None) if deleted else TalentProfile.deleted_at.is_(None)]
    if allowed_ids is not None:
        conds.append(TalentProfile.id.in_(allowed_ids))
    if keyword:
        pattern = _like_pattern(keyword)
        conds.append(
            or_(
                TalentProfile.name.like(pattern),
                TalentProfile.current_title.like(pattern),
                func.cast(TalentProfile.skills_json, CHAR).like(pattern),
            )
        )
    if degree:
        conds.append(TalentProfile.degree == degree)
    if min_years is not None:
        conds.append(TalentProfile.work_years >= Decimal(str(min_years)))
    if max_years is not None:
        conds.append(TalentProfile.work_years <= Decimal(str(max_years)))
        if min_years is None:
            conds.append(TalentProfile.work_years.is_not(None))
    if tag or tag_type:
        tag_conds = [TalentTag.talent_profile_id == TalentProfile.id]
        if tag:
            tag_conds.append(TalentTag.tag == tag)
        if tag_type:
            tag_conds.append(TalentTag.tag_type == tag_type)
        conds.append(select(TalentTag.id).where(*tag_conds).exists())

    rows = list(
        db.execute(
            select(TalentProfile)
            .where(*conds)
            .order_by(
                TalentProfile.deleted_at.desc() if deleted else TalentProfile.updated_at.desc(),
                TalentProfile.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    total = int(
        db.execute(select(func.count(TalentProfile.id)).where(*conds)).scalar_one()
    )
    return rows, total


def tag_rows_for_profiles(db: Session, profile_ids: Iterable[int]) -> dict[int, list[TalentTag]]:
    """一次取回多个画像的标签行（列表页防 N+1），按画像 id 分组、组内按行 id 升序。"""
    ids = list(profile_ids)
    if not ids:
        return {}
    rows = list(
        db.execute(
            select(TalentTag)
            .where(TalentTag.talent_profile_id.in_(ids))
            .order_by(TalentTag.talent_profile_id, TalentTag.id)
        ).scalars().all()
    )
    grouped: dict[int, list[TalentTag]] = {}
    for row in rows:
        grouped.setdefault(row.talent_profile_id, []).append(row)
    return grouped
