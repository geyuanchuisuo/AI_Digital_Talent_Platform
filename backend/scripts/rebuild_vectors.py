"""向量全量重建（T-05）。

输入文档：`docs/架构设计.md` v1.4 §5.3、`docs/算法与判定规则.md` v1.2 §6.3、
`docs/数据库设计.md` v1.2 §10。

用途：备份恢复、`EMBEDDING_MODEL`/维度变更、批量 `vector_status=failed` 补偿。
判定口径（§5.3「按 `profile_text_hash` 变化决定是否重新 embedding」）：
1. 用 §6.3 口径重算画像文本 → SHA-256；
2. 与 `talent_profiles.profile_text_hash` 相同 **且** `vector_status='synced'` → 跳过（零 embedding 调用）；
3. 其余（hash 变化 / failed / pending）→ embedding + upsert，成功后写 `profile_text_hash`、
   `vector_status='synced'`、`vector_synced_at`。

用法（`main/backend` 目录，一律用 venv 解释器）：
    ./.venv/Scripts/python.exe scripts/rebuild_vectors.py --dry-run          # 只看差异，不调用任何外部服务
    ./.venv/Scripts/python.exe scripts/rebuild_vectors.py                    # 增量重建（只处理差异项）
    ./.venv/Scripts/python.exe scripts/rebuild_vectors.py --force            # 全量重嵌（忽略 hash 相同）
    ./.venv/Scripts/python.exe scripts/rebuild_vectors.py --recreate --yes   # 删 collection 重建（维度变更后用）

⚠️ `--recreate` 会清空整个 collection，必须同时给 `--yes`，且不可与 `--dry-run` 同时使用。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, update  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.embedding_client import EMBEDDING_BATCH_SIZE, embed_texts  # noqa: E402
from app.core.milvus_store import MilvusStore, get_store  # noqa: E402
from app.core.profile_text import build_profile_text_for_row, compute_text_hash  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.resume import ParsedResume  # noqa: E402
from app.models.talent import TalentProfile, TalentTag  # noqa: E402


@dataclass
class Candidate:
    profile_id: int
    text: str
    text_hash: str
    stored_hash: str | None
    vector_status: str

    @property
    def needs_embedding(self) -> bool:
        return self.text_hash != (self.stored_hash or "")


@dataclass
class RebuildSummary:
    total_profiles: int = 0
    skipped_same_hash: int = 0
    to_embed: int = 0
    embedded: int = 0
    upserted: int = 0
    failed: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_profiles": self.total_profiles,
            "skipped_same_hash": self.skipped_same_hash,
            "to_embed": self.to_embed,
            "embedded": self.embedded,
            "upserted": self.upserted,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "errors": list(self.errors),
        }


def _dedupe(items: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """同画像可能聚合了多份 parsed_resumes（merge 后），按业务键去重保持稳定。"""
    seen: set[tuple] = set()
    result: list[dict] = []
    for item in items:
        key = tuple(str(item.get(name) or "") for name in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def load_candidates(db: Any, *, limit: int | None = None) -> list[Candidate]:
    """遍历未删除画像，按 §6.3 重算文本并组装候选（不调用 Embedding）。"""
    stmt = (
        select(TalentProfile)
        .where(TalentProfile.deleted_at.is_(None))
        .order_by(TalentProfile.id)
    )
    if limit:
        stmt = stmt.limit(int(limit))
    profiles = list(db.execute(stmt).scalars().all())
    if not profiles:
        return []

    profile_ids = [profile.id for profile in profiles]

    tags_map: dict[int, list[str]] = {}
    for pid, tag in db.execute(
        select(TalentTag.talent_profile_id, TalentTag.tag).where(TalentTag.talent_profile_id.in_(profile_ids))
    ):
        tags_map.setdefault(int(pid), []).append(tag)

    structured_map: dict[int, dict[str, list[dict]]] = {}
    for pid, structured in db.execute(
        select(ParsedResume.talent_profile_id, ParsedResume.structured_json).where(
            ParsedResume.talent_profile_id.in_(profile_ids)
        )
    ):
        bucket = structured_map.setdefault(int(pid), {"employments": [], "projects": [], "educations": []})
        data = structured or {}
        bucket["employments"].extend(data.get("employments") or [])
        bucket["projects"].extend(data.get("projects") or [])
        bucket["educations"].extend(data.get("educations") or [])

    candidates: list[Candidate] = []
    for profile in profiles:
        aggregated = structured_map.get(profile.id, {})
        structured = {
            "employments": _dedupe(aggregated.get("employments", []), ("company", "title", "start")),
            "projects": _dedupe(aggregated.get("projects", []), ("name", "role")),
            "educations": _dedupe(aggregated.get("educations", []), ("school", "major", "degree")),
        }
        text = build_profile_text_for_row(profile, tags=tags_map.get(profile.id), structured=structured)
        candidates.append(
            Candidate(
                profile_id=int(profile.id),
                text=text,
                text_hash=compute_text_hash(text),
                stored_hash=profile.profile_text_hash,
                vector_status=profile.vector_status,
            )
        )
    return candidates


def plan(candidates: list[Candidate], *, force: bool = False) -> tuple[list[Candidate], int]:
    """返回 (需要重建的候选, 因 hash 相同而跳过的数量)。"""
    if force:
        return list(candidates), 0
    todo = [item for item in candidates if item.needs_embedding or item.vector_status != "synced"]
    return todo, len(candidates) - len(todo)


def run_rebuild(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    force: bool = False,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    recreate: bool = False,
    collection: str | None = None,
    dim: int | None = None,
    store: MilvusStore | None = None,
) -> RebuildSummary:
    """执行重建；`dry_run=True` 时只统计差异，不调用 Embedding / Milvus。"""
    summary = RebuildSummary(dry_run=dry_run)
    milvus = store or (
        MilvusStore(collection=collection, dim=dim) if (collection or dim) else get_store()
    )

    with SessionLocal() as db:
        candidates = load_candidates(db, limit=limit)
        summary.total_profiles = len(candidates)
        todo, skipped = plan(candidates, force=force)
        summary.skipped_same_hash = skipped
        summary.to_embed = len(todo)

        if dry_run or not todo:
            return summary

        if recreate:
            milvus.drop()
            print(f"[recreate] collection {milvus.collection} 已删除并按 dim={milvus.dim} 重建")
        milvus.ensure_collection()

        step = max(1, int(batch_size))
        for start in range(0, len(todo), step):
            chunk = todo[start : start + step]
            try:
                vectors = embed_texts([item.text for item in chunk])
            except Exception as exc:  # noqa: BLE001 - 单批失败不影响其余批
                summary.failed += len(chunk)
                summary.errors.append(f"批次 {start // step + 1} embedding 失败：{type(exc).__name__}: {exc}")
                _mark_failed(db, [item.profile_id for item in chunk])
                continue

            summary.embedded += len(chunk)
            try:
                milvus.upsert_many(
                    [(item.profile_id, vector, item.text_hash) for item, vector in zip(chunk, vectors)]
                )
            except Exception as exc:  # noqa: BLE001
                summary.failed += len(chunk)
                summary.errors.append(f"批次 {start // step + 1} upsert 失败：{type(exc).__name__}: {exc}")
                _mark_failed(db, [item.profile_id for item in chunk])
                continue

            summary.upserted += len(chunk)
            _mark_synced(db, chunk)

    return summary


def _mark_synced(db: Any, chunk: list[Candidate]) -> None:
    now = datetime.now()
    for item in chunk:
        db.execute(
            update(TalentProfile)
            .where(TalentProfile.id == item.profile_id)
            .values(
                profile_text_hash=item.text_hash,
                vector_status="synced",
                vector_synced_at=now,
            )
        )
    db.commit()


def _mark_failed(db: Any, profile_ids: list[int]) -> None:
    if not profile_ids:
        return
    db.execute(
        update(TalentProfile)
        .where(TalentProfile.id.in_(profile_ids))
        .values(vector_status="failed")
    )
    db.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="向量全量重建（T-05）")
    parser.add_argument("--dry-run", action="store_true", help="只统计差异，不调用 Embedding / Milvus")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条画像（调试用）")
    parser.add_argument("--force", action="store_true", help="忽略 hash 相同，强制全部重嵌")
    parser.add_argument("--batch-size", type=int, default=EMBEDDING_BATCH_SIZE, help="每批 embedding 条数")
    parser.add_argument("--recreate", action="store_true", help="先删除 collection 再按 EMBEDDING_DIM 重建（危险）")
    parser.add_argument("--yes", action="store_true", help="配合 --recreate 使用，确认清空向量")
    parser.add_argument("--collection", default=None, help="覆盖 MILVUS_COLLECTION（测试/临时库用）")
    parser.add_argument("--dim", type=int, default=None, help="覆盖 EMBEDDING_DIM")
    args = parser.parse_args(argv)

    if args.recreate and args.dry_run:
        print("❌ --recreate 不能与 --dry-run 同时使用")
        return 2
    if args.recreate and not args.yes:
        print("❌ --recreate 会清空整个 collection；确认后请加 --yes 重新执行")
        return 2

    print("=" * 64)
    print("向量全量重建 · collection=%s dim=%s dry_run=%s force=%s"
          % (args.collection or settings.milvus_collection, args.dim or settings.embedding_dim, args.dry_run, args.force))
    print("=" * 64)

    summary = run_rebuild(
        dry_run=args.dry_run,
        limit=args.limit,
        force=args.force,
        batch_size=args.batch_size,
        recreate=args.recreate,
        collection=args.collection,
        dim=args.dim,
    )

    print(f"画像总数        ：{summary.total_profiles}")
    print(f"hash 相同跳过   ：{summary.skipped_same_hash}")
    print(f"待重建          ：{summary.to_embed}")
    if summary.dry_run:
        print("（dry-run：未调用 Embedding / Milvus，未改数据库）")
        return 0
    print(f"已 embedding    ：{summary.embedded}")
    print(f"已 upsert       ：{summary.upserted}")
    print(f"失败            ：{summary.failed}")
    for error in summary.errors:
        print(f"  - {error}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
