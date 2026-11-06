"""Milvus 向量库适配层（T-05）· 全项目唯一向量读写入口。

实现口径：
- `docs/架构设计.md` v1.4 §5.3：
  * **仅支持 Standalone**（`MILVUS_URI=http://localhost:19530`，本项目独立实例，禁止指向其他项目）；
  * Collection `talent_vectors`：`talent_profile_id` INT64 主键（非自增）、
    `vector` FLOAT_VECTOR（维度 = `EMBEDDING_DIM`）、`text_hash` VARCHAR(64)；
  * 启动自检：collection 不存在按配置维度创建；**存在但维度不符必须报错**（不能静默写入）；
  * 语义检索 COSINE topK 默认 20、上限 100；
  * 软删除时 delete 向量（失败不阻塞，返回 `vector_deleted:false`）。
- `docs/运行配置.md` v1.3：`MILVUS_URI` / `MILVUS_COLLECTION` / `VECTOR_SYNC_TIMEOUT` / `EMBEDDING_DIM`
- 开发日志 Layer 3 T-05：**按生产 schema 补验 upsert 语义**（T-37 只验了 `auto_id=True`+insert）：
  本文件用 `auto_id=False` + `talent_profile_id` 主键，同 PK 二次 upsert 是**覆盖**而非新增。

同步路径限时（§5.1 半写口径）：
`sync_vector_by_text()` 把「embedding + upsert」整体放进后台线程，主线程最多等
`VECTOR_SYNC_TIMEOUT`（1.5s）；超时即返回 `ok=False`，调用方返回 200 + `vector_status:"failed"`，
**不阻塞请求**。超时后台线程仍会自然结束（embedding 客户端自身有 `EMBEDDING_TIMEOUT` 上限）。

⚠️ 公共部分（开发日志 §1.3），修改前需用户确认。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.config import settings

logger = logging.getLogger(__name__)

TEXT_HASH_MAX_LEN = 64
SEARCH_TOP_K_DEFAULT = 20
SEARCH_TOP_K_MAX = 100
SYNC_WORKERS = 4  # 同步写入的后台线程池（与 TaskRunner 线程池无关）

_sync_executor = ThreadPoolExecutor(max_workers=SYNC_WORKERS, thread_name_prefix="vector-sync")


class MilvusStoreError(RuntimeError):
    """Milvus 读写失败的统一异常（T-19 据此映射 503）。"""


class CollectionDimMismatch(MilvusStoreError):
    """collection 已存在但向量维度 / 主键结构与配置不符。"""


@dataclass(frozen=True)
class SearchHit:
    talent_profile_id: int
    score: float  # COSINE 相似度（越大越相似）
    text_hash: str | None


@dataclass(frozen=True)
class VectorSyncResult:
    """同步写入结果（调用方据此决定 `vector_status` 与响应体）。"""

    ok: bool
    profile_id: int
    text_hash: str
    vector_status: str  # "synced" / "failed"
    elapsed: float
    error: str | None = None


class MilvusStore:
    """可注入 client 的 Milvus 封装（单测用假 client，无需真实 Milvus）。"""

    def __init__(
        self,
        *,
        uri: str | None = None,
        collection: str | None = None,
        dim: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.uri = uri or settings.milvus_uri
        self.collection = collection or settings.milvus_collection
        self.dim = dim if dim is not None else settings.embedding_dim
        self._client = client
        self._ensured = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 连接与自检
    # ------------------------------------------------------------------ #
    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self.uri)
        return self._client

    def ensure_collection(self, *, force: bool = False) -> None:
        """启动自检：不存在则按 `EMBEDDING_DIM` 建；存在则校验维度与主键，不符抛错。"""
        with self._lock:
            if self._ensured and not force:
                return
            try:
                exists = bool(self.client.has_collection(self.collection))
            except Exception as exc:  # noqa: BLE001
                raise MilvusStoreError(f"连接 Milvus 失败（{self.uri}）：{type(exc).__name__}: {exc}") from exc

            if not exists:
                self._create_collection()
                logger.info("Milvus collection 已创建：%s（dim=%d, metric=COSINE）", self.collection, self.dim)
            else:
                self._verify_collection()
            self._ensured = True

    def _create_collection(self) -> None:
        from pymilvus import DataType, MilvusClient

        # 生产 schema：主键显式给定，auto_id=False（与 T-37 验证用的 auto_id=True 不同）
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("talent_profile_id", DataType.INT64, is_primary=True, auto_id=False)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("text_hash", DataType.VARCHAR, max_length=TEXT_HASH_MAX_LEN)
        index = MilvusClient.prepare_index_params()
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        try:
            self.client.create_collection(self.collection, schema=schema, index_params=index)
            self.client.load_collection(self.collection)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"创建 collection {self.collection} 失败：{type(exc).__name__}: {exc}") from exc

    def _describe(self) -> dict:
        try:
            return dict(self.client.describe_collection(collection_name=self.collection))
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"读取 collection 结构失败：{type(exc).__name__}: {exc}") from exc

    def _verify_collection(self) -> None:
        description = self._describe()
        fields = {field.get("name"): field for field in description.get("fields", [])}

        vector_field = fields.get("vector")
        if vector_field is None:
            raise CollectionDimMismatch(
                f"collection {self.collection} 缺少 vector 字段，"
                f"请执行 scripts/rebuild_vectors.py --recreate 重建"
            )
        actual_dim = int((vector_field.get("params") or {}).get("dim") or 0)
        if actual_dim != self.dim:
            raise CollectionDimMismatch(
                f"collection {self.collection} 向量维度={actual_dim}，"
                f"与 EMBEDDING_DIM={self.dim} 不一致；确认模型后执行 "
                f"scripts/rebuild_vectors.py --recreate 重建"
            )

        pk_field = fields.get("talent_profile_id")
        if pk_field is None or not pk_field.get("is_primary"):
            raise CollectionDimMismatch(
                f"collection {self.collection} 主键不是 talent_profile_id（疑似旧版/异构 collection），"
                f"请删库重建"
            )
        if "text_hash" not in fields:
            raise CollectionDimMismatch(
                f"collection {self.collection} 缺少 text_hash 字段，请删库重建"
            )

    def collection_dim_status(self) -> tuple[bool, str | None]:
        """供 `/health` 判定：返回 (是否可用, 说明)。

        只在「collection 存在且结构与配置冲突」时返回 False；
        collection 尚未创建（首次写入前）不算故障——服务连通即可。
        """
        try:
            if not self.client.has_collection(self.collection):
                return True, None
            description = self._describe()
            fields = {field.get("name"): field for field in description.get("fields", [])}
            vector_field = fields.get("vector") or {}
            actual_dim = int((vector_field.get("params") or {}).get("dim") or 0)
            if actual_dim and actual_dim != self.dim:
                return False, f"collection 维度 {actual_dim} ≠ EMBEDDING_DIM {self.dim}"
            return True, None
        except Exception as exc:  # noqa: BLE001 - health 不因该项异常而 500
            logger.debug("collection 结构检查跳过：%s", exc)
            return True, None

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def upsert(self, profile_id: int, vector: Sequence[float], text_hash: str) -> None:
        """按主键 upsert：同 `talent_profile_id` 二次写入是**覆盖**（不新增行）。"""
        if len(vector) != self.dim:
            raise CollectionDimMismatch(
                f"写入向量维度={len(vector)} 与 EMBEDDING_DIM={self.dim} 不一致"
            )
        self.ensure_collection()
        row = {"talent_profile_id": int(profile_id), "vector": list(vector), "text_hash": text_hash}
        try:
            self.client.upsert(collection_name=self.collection, data=[row])
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"Milvus upsert 失败：{type(exc).__name__}: {exc}") from exc

    def upsert_many(self, rows: Iterable[tuple[int, Sequence[float], str]]) -> int:
        """批量 upsert（rebuild 用）；返回写入条数。"""
        self.ensure_collection()
        payload = [
            {"talent_profile_id": int(pid), "vector": list(vec), "text_hash": text_hash}
            for pid, vec, text_hash in rows
        ]
        if not payload:
            return 0
        for item in payload:
            if len(item["vector"]) != self.dim:
                raise CollectionDimMismatch(
                    f"写入向量维度={len(item['vector'])} 与 EMBEDDING_DIM={self.dim} 不一致"
                )
        try:
            self.client.upsert(collection_name=self.collection, data=payload)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"Milvus 批量 upsert 失败：{type(exc).__name__}: {exc}") from exc
        return len(payload)

    def delete(self, profile_id: int) -> None:
        """删除单条向量（软删除用）；不吞异常，调用方决定是否降级。"""
        self.ensure_collection()
        try:
            self.client.delete(collection_name=self.collection, ids=[int(profile_id)])
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"Milvus delete 失败：{type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(self, vector: Sequence[float], top_k: int = SEARCH_TOP_K_DEFAULT) -> list[SearchHit]:
        """COSINE topK 检索（默认 20、上限 100，架构 §5.3）。

        `consistency_level="Strong"`：Standalone 支持强一致读，保证「upsert 后立刻检索」
        （restore/revector/验收）能读到刚写入的向量；默认 Bounded 会读到旧快照，造成
        「刚同步却搜不到」的假故障。
        """
        if not vector:
            raise MilvusStoreError("检索向量为空")
        limit = max(1, min(int(top_k), SEARCH_TOP_K_MAX))
        self.ensure_collection()
        try:
            raw = self.client.search(
                collection_name=self.collection,
                data=[list(vector)],
                limit=limit,
                output_fields=["text_hash"],
                search_params={"metric_type": "COSINE"},
                consistency_level="Strong",
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"Milvus search 失败：{type(exc).__name__}: {exc}") from exc

        hits: list[SearchHit] = []
        for item in raw[0] if raw else []:
            entity = item.get("entity") or {}
            # 主键别名随 pymilvus 版本不同：可能是 "id"，也可能直接用字段名 "talent_profile_id"
            raw_id = item.get("id")
            if raw_id is None:
                raw_id = item.get("talent_profile_id")
            if raw_id is None:
                raw_id = entity.get("talent_profile_id")
            if raw_id is None:
                raise MilvusStoreError(f"Milvus 检索结果缺少主键：{item}")
            hits.append(
                SearchHit(
                    talent_profile_id=int(raw_id),
                    score=float(item.get("distance") or 0.0),
                    text_hash=entity.get("text_hash"),
                )
            )
        return hits

    def count(self) -> int:
        """当前 collection 有效行数（运维/验收用）。

        用 `count(*)` 聚合查询而非 `get_collection_stats`：Milvus upsert 是
        「delete + insert」，统计值在 compaction 前会把墓碑行也算进去（同 PK upsert
        会显示 2 行）；`count(*)` + Strong 一致性才是逻辑行数。
        """
        self.ensure_collection()
        try:
            rows = self.client.query(
                collection_name=self.collection,
                filter="talent_profile_id >= 0",
                output_fields=["count(*)"],
                consistency_level="Strong",
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"读取 collection 统计失败：{type(exc).__name__}: {exc}") from exc
        if not rows:
            return 0
        row = rows[0]
        return int(row.get("count(*)") or row.get("count") or 0)

    def query_hash(self, profile_id: int) -> str | None:
        """读取单条向量的 text_hash（验收/排障用）；不存在返回 None。"""
        self.ensure_collection()
        try:
            rows = self.client.query(
                collection_name=self.collection,
                filter=f"talent_profile_id == {int(profile_id)}",
                output_fields=["text_hash"],
                consistency_level="Strong",
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"Milvus query 失败：{type(exc).__name__}: {exc}") from exc
        return rows[0].get("text_hash") if rows else None

    def drop(self) -> None:
        """删除整个 collection（`rebuild_vectors --recreate` 用，破坏性操作）。"""
        try:
            if self.client.has_collection(self.collection):
                self.client.drop_collection(self.collection)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"删除 collection 失败：{type(exc).__name__}: {exc}") from exc
        self._ensured = False

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 单例
# --------------------------------------------------------------------------- #
_default_store: MilvusStore | None = None
_store_lock = threading.Lock()


def get_store() -> MilvusStore:
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = MilvusStore()
        return _default_store


def reset_store() -> None:
    """测试用：丢弃默认单例。"""
    global _default_store
    with _store_lock:
        _default_store = None


# --------------------------------------------------------------------------- #
# vector_status 读写（数据库侧）
# --------------------------------------------------------------------------- #
def set_vector_status(
    profile_id: int,
    status: str,
    *,
    text_hash: str | None = None,
    synced_at: datetime | None = None,
    session: Any | None = None,
) -> None:
    """更新 `talent_profiles.vector_status` / `vector_synced_at` / `profile_text_hash`。

    - 成功：status='synced'、写入 text_hash 与时间戳；
    - 失败：status='failed'，**保留原有 profile_text_hash**（它代表「上次成功同步的文本」，
      restore/rebuild 据此判断是否需要重嵌）。
    """
    from sqlalchemy import update

    from app.models.talent import TalentProfile

    values: dict[str, Any] = {"vector_status": status}
    if synced_at is not None:
        values["vector_synced_at"] = synced_at
    if text_hash is not None and status == "synced":
        values["profile_text_hash"] = text_hash

    if session is not None:
        session.execute(update(TalentProfile).where(TalentProfile.id == profile_id).values(**values))
        return

    from app.database import SessionLocal

    with SessionLocal() as db:
        db.execute(update(TalentProfile).where(TalentProfile.id == profile_id).values(**values))
        db.commit()


# --------------------------------------------------------------------------- #
# 同步写入 + 整体限时（§5.1 半写口径）
# --------------------------------------------------------------------------- #
def sync_vector_by_text(
    profile_id: int,
    text: str,
    *,
    budget: float | None = None,
    persist_status: bool = True,
) -> VectorSyncResult:
    """同步写入路径：embedding + upsert，整体受 `VECTOR_SYNC_TIMEOUT` 约束。

    - 超时/失败**不抛异常**，返回 `ok=False`（调用方按半写口径返回 200 + `vector_status:"failed"`）；
    - `revector` 等「无业务写入」场景若要 503，调用方自行判断 `result.ok` 后抛 BizError(503)。
    """
    from app.core.embedding_client import embed_one
    from app.core.profile_text import compute_text_hash

    text_hash = compute_text_hash(text)
    total_budget = settings.vector_sync_timeout if budget is None else budget
    started = time.monotonic()

    def _work() -> None:
        vector = embed_one(text)
        get_store().upsert(profile_id, vector, text_hash)

    future: Future = _sync_executor.submit(_work)
    error: str | None = None
    try:
        future.result(timeout=max(0.0, total_budget))
    except FutureTimeout:
        error = f"向量同步超时（预算 {total_budget}s）"
        logger.warning("画像 %s %s，已按半写口径置 failed", profile_id, error)
    except Exception as exc:  # noqa: BLE001 - 统一收敛为失败结果
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("画像 %s 向量同步失败：%s", profile_id, error)

    elapsed = round(time.monotonic() - started, 3)
    ok = error is None
    status = "synced" if ok else "failed"

    if persist_status:
        try:
            set_vector_status(
                profile_id,
                status,
                text_hash=text_hash if ok else None,
                synced_at=datetime.now() if ok else None,
            )
        except Exception:  # noqa: BLE001 - 状态写失败不改变本次结果，但必须留痕
            logger.exception("画像 %s 写入 vector_status=%s 失败", profile_id, status)

    return VectorSyncResult(
        ok=ok,
        profile_id=profile_id,
        text_hash=text_hash,
        vector_status=status,
        elapsed=elapsed,
        error=error,
    )


def delete_profile_vector(profile_id: int) -> bool:
    """软删除时删向量：**失败不阻塞**（架构 §5.3 / 数据库 §5.1），返回是否删除成功。"""
    try:
        get_store().delete(profile_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("画像 %s 向量删除失败（不阻塞软删）：%s", profile_id, exc)
        return False


# --------------------------------------------------------------------------- #
# 按主键取向量（T-23 匹配路径：岗位向量 × 画像向量 COSINE，§4.2）
# --------------------------------------------------------------------------- #
def get_profile_vector(profile_id: int) -> list[float] | None:
    """取指定画像的向量；画像无向量返回 None；Milvus 异常抛 `MilvusStoreError`（调用方降级）。

    仅匹配（F3）使用——筛选（F1）`use_vector=False` 永不调用本函数（决策 D-A）。
    """
    try:
        rows = get_store().client.query(
            collection_name=get_store().collection,
            filter=f"talent_profile_id == {int(profile_id)}",
            output_fields=["vector"],
            limit=1,
            consistency_level="Strong",
        )
    except Exception as exc:  # noqa: BLE001 - 统一收敛为 MilvusStoreError 供调用方降级
        raise MilvusStoreError(f"Milvus query 向量失败：{type(exc).__name__}: {exc}") from exc
    if not rows:
        return None
    vector = rows[0].get("vector") if isinstance(rows[0], dict) else None
    if vector is None and rows[0].get("entity"):
        vector = rows[0]["entity"].get("vector")
    return list(vector) if vector is not None else None
