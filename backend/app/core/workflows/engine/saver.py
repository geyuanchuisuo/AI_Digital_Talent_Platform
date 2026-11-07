"""SQLAlchemy 检查点存储（T-D04）。

为什么自己实现而不是用官方 langgraph-checkpoint-sqlite/-postgres：
两者是 requirements 之外的额外依赖（归 A 管），而项目已有 SQLAlchemy + MySQL；
本实现一套代码覆盖 SQLite（测试）/ MySQL（生产）/ PostgreSQL（T-D02 副本就绪后
可直接切换）。接口按 langgraph-checkpoint 4.x 的 BaseCheckpointSaver 实现，
序列化用官方 JsonPlusSerializer，保证与官方 saver 的检查点语义一致。

表（`setup()` 幂等建表，Core 层管理，**不进 ORM 基线**——纯引擎内部结构）：
- workflow_checkpoints：thread_id/checkpoint_ns/checkpoint_id 唯一，blob 存序列化检查点
- workflow_checkpoint_writes：pending writes（task_id + idx 唯一）
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import (
    BigInteger,
    Column,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

_METADATA_TABLE = Table(
    "workflow_checkpoints",
    MetaData(),
    Column("thread_id", String(191), primary_key=True),
    Column("checkpoint_ns", String(191), primary_key=True, server_default=""),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("parent_checkpoint_id", String(64), nullable=True),
    Column("type", String(64), nullable=True),
    Column("checkpoint", LargeBinary, nullable=False),
    Column("metadata", LargeBinary, nullable=False),
    Column("metadata_type", String(64), nullable=False, server_default="msgpack"),
)

_WRITES_TABLE = Table(
    "workflow_checkpoint_writes",
    MetaData(),
    Column("thread_id", String(191), primary_key=True),
    Column("checkpoint_ns", String(191), primary_key=True, server_default=""),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("task_id", String(191), primary_key=True),
    Column("idx", BigInteger, primary_key=True),
    Column("task_path", String(191), nullable=True, server_default=""),
    Column("channel", String(191), nullable=False),
    Column("type", String(64), nullable=True),
    Column("blob", LargeBinary, nullable=False),
)


class SQLAlchemyCheckpointSaver(BaseCheckpointSaver):
    """同步实现为主；async 方法经 to_thread 委托（引擎一期为同步调用面）。"""

    def __init__(self, engine: Engine) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._engine = engine
        self._session_factory = sessionmaker(bind=engine)

    # ---- 生命周期 ----

    def setup(self) -> None:
        """幂等建表（等价官方 saver 的 setup()；D_0002 迁移也调用本方法）。"""
        for table in (_METADATA_TABLE, _WRITES_TABLE):
            table.metadata.create_all(self._engine, tables=[table], checkfirst=True)

    # ---- 内部工具 ----

    def _dump(self, value: Any) -> tuple[str | None, bytes]:
        return self.serde.dumps_typed(value)

    def _load(self, type_: str, blob: bytes) -> Any:
        # type 必须与 dumps_typed 的返回一致（json/msgpack），不可混用——
        # 对 msgpack blob 强走 json 解码会 UnicodeDecodeError
        return self.serde.loads_typed((type_, blob))

    @staticmethod
    def _cfg(config: dict | None) -> dict:
        return (config or {}).get("configurable") or {}

    def _fetch_tuple(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str | None) -> CheckpointTuple | None:
        with self._session_factory() as db:
            stmt = select(_METADATA_TABLE).where(
                _METADATA_TABLE.c.thread_id == thread_id,
                _METADATA_TABLE.c.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                stmt = stmt.where(_METADATA_TABLE.c.checkpoint_id == checkpoint_id)
            else:
                stmt = stmt.order_by(_METADATA_TABLE.c.checkpoint_id.desc()).limit(1)
            row = db.execute(stmt).first()
            if row is None:
                return None

            writes = list(
                db.execute(
                    select(_WRITES_TABLE).where(
                        _WRITES_TABLE.c.thread_id == thread_id,
                        _WRITES_TABLE.c.checkpoint_ns == checkpoint_ns,
                        _WRITES_TABLE.c.checkpoint_id == row.checkpoint_id,
                    ).order_by(_WRITES_TABLE.c.task_id, _WRITES_TABLE.c.idx)
                ).mappings()
            )

            base_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            }
            parent_config = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            )
            return CheckpointTuple(
                config=base_config,
                checkpoint=self._load(row.type, row.checkpoint),
                metadata=self._load(row.metadata_type, row.metadata) if row.metadata else {},
                parent_config=parent_config,
                pending_writes=[
                    (w["task_id"], w["channel"], self._load(w["type"], w["blob"])) for w in writes
                ],
            )

    # ---- BaseCheckpointSaver 同步面 ----

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        cfg = self._cfg(config)
        return self._fetch_tuple(
            cfg["thread_id"], cfg.get("checkpoint_ns", ""), cfg.get("checkpoint_id")
        )

    def list(self, config: dict | None, *, filter: dict | None = None,
             before: dict | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        cfg = self._cfg(config)
        with self._session_factory() as db:
            stmt = select(_METADATA_TABLE).order_by(_METADATA_TABLE.c.checkpoint_id.desc())
            if cfg.get("thread_id"):
                stmt = stmt.where(
                    _METADATA_TABLE.c.thread_id == cfg["thread_id"],
                    _METADATA_TABLE.c.checkpoint_ns == cfg.get("checkpoint_ns", ""),
                )
            if before:
                before_id = self._cfg(before).get("checkpoint_id")
                if before_id:
                    stmt = stmt.where(_METADATA_TABLE.c.checkpoint_id < before_id)
            if limit:
                stmt = stmt.limit(limit)
            rows = list(db.execute(stmt).mappings())
        for row in rows:
            tuple_ = self._fetch_tuple(row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
            if tuple_ is None:
                continue
            if filter and not all(
                tuple_.metadata.get(k) == v for k, v in filter.items()
            ):
                continue
            yield tuple_

    def put(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> dict:
        cfg = self._cfg(config)
        thread_id = cfg["thread_id"]
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        parent_id = cfg.get("checkpoint_id")
        ckpt_type, ckpt_blob = self._dump(checkpoint)
        meta_type, meta_blob = self._dump(metadata)

        with self._session_factory() as db:
            with db.begin():
                db.execute(
                    delete_existing(_METADATA_TABLE, thread_id, checkpoint_ns, checkpoint["id"])
                )
                db.execute(
                    insert(_METADATA_TABLE).values(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint["id"],
                        parent_checkpoint_id=parent_id,
                        type=ckpt_type,
                        checkpoint=ckpt_blob,
                        metadata=meta_blob,
                        metadata_type=meta_type,
                    )
                )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        cfg = self._cfg(config)
        thread_id = cfg["thread_id"]
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        checkpoint_id = cfg.get("checkpoint_id")
        if not checkpoint_id:
            raise ValueError("put_writes 需要 config.configurable.checkpoint_id")

        with self._session_factory() as db:
            with db.begin():
                for idx, (channel, value) in enumerate(writes):
                    type_, blob = self._dump(value)
                    db.execute(
                        delete_existing(
                            _WRITES_TABLE, thread_id, checkpoint_ns, checkpoint_id,
                            task_id=task_id, idx=idx,
                        )
                    )
                    db.execute(
                        insert(_WRITES_TABLE).values(
                            thread_id=thread_id,
                            checkpoint_ns=checkpoint_ns,
                            checkpoint_id=checkpoint_id,
                            task_id=task_id,
                            idx=idx,
                            task_path=task_path,
                            channel=channel,
                            type=type_,
                            blob=blob,
                        )
                    )

    def delete_thread(self, thread_id: str) -> None:
        with self._session_factory() as db:
            with db.begin():
                for table in (_METADATA_TABLE, _WRITES_TABLE):
                    db.execute(table.delete().where(table.c.thread_id == thread_id))

    # ---- async 面：to_thread 委托同步实现（引擎一期同步调用面，接口不缺位） ----

    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path: str = "") -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)


def delete_existing(table: Table, thread_id: str, ns: str, checkpoint_id: str,
                    **extra: Any):
    """方言无关的去重删除（MySQL/SQLite 通用；避免 ON DUPLICATE 方言分叉）。"""
    stmt = table.delete().where(
        table.c.thread_id == thread_id,
        table.c.checkpoint_ns == ns,
        table.c.checkpoint_id == checkpoint_id,
    )
    for col, val in extra.items():
        stmt = stmt.where(getattr(table.c, col) == val)
    return stmt


def make_saver(url: str) -> SQLAlchemyCheckpointSaver:
    """便捷工厂：`make_saver("sqlite:///path.db")` / 生产传 settings.database_url。"""
    saver = SQLAlchemyCheckpointSaver(create_engine(url))
    saver.setup()
    return saver
