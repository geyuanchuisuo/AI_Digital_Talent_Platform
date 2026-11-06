"""T-14 L1：队列管理的纯逻辑单测（不碰 DB / 网络）。

覆盖：
1. 并发数 1–5 校验（越界必须 400，而不是 Pydantic 的 422——接口 §4）；
2. 队列 item 的按钮显隐矩阵：`can_retry` 仅 failed，`can_requeue` 仅 cancelled/failed；
3. `truncated` 归一为 bool、`created_at` 取文件上传时间、`talent_profile_id` 透传；
4. 非法 status 在查库前就被拒（400）。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.errors import BizError
from app.service import queue_service


def make_task(status: str, **overrides):
    values = {
        "id": 12,
        "resume_file_id": 9,
        "status": status,
        "stage": "vector",
        "retries": 1,
        "error": None,
        "truncated": 0,
        "started_at": None,
        "finished_at": None,
        "profiled_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_file():
    return SimpleNamespace(
        filename="张三.pdf",
        upload_batch_id="01JABCDEFGHJKMNPQRSTVWXYZ",
        created_at=datetime(2026, 11, 5, 10, 0, 0),
    )


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_validate_concurrency_accepts_range(value: int) -> None:
    assert queue_service.validate_concurrency(value) == value


@pytest.mark.parametrize("value", [0, 6, -1, 99])
def test_validate_concurrency_rejects_out_of_range(value: int) -> None:
    with pytest.raises(BizError) as ctx:
        queue_service.validate_concurrency(value)
    assert ctx.value.code == 400


@pytest.mark.parametrize("value", [True, False])
def test_validate_concurrency_rejects_bool(value: bool) -> None:
    with pytest.raises(BizError):
        queue_service.validate_concurrency(value)


@pytest.mark.parametrize(
    ("status", "can_retry", "can_requeue"),
    [
        ("pending", False, False),
        ("running", False, False),
        ("success", False, False),
        ("failed", True, True),
        ("duplicate_pending", False, False),
        ("cancelled", False, True),
    ],
)
def test_item_button_flags(status: str, can_retry: bool, can_requeue: bool) -> None:
    item = queue_service.build_item(make_task(status), make_file(), talent_profile_id=5)
    assert item["can_retry"] is can_retry
    assert item["can_requeue"] is can_requeue


def test_item_field_mapping() -> None:
    item = queue_service.build_item(
        make_task("failed", stage="vector", retries=2, error="Milvus 连接失败", truncated=1),
        make_file(),
        talent_profile_id=None,
    )
    assert item["task_id"] == 12
    assert item["resume_file_id"] == 9
    assert item["filename"] == "张三.pdf"
    assert item["status"] == "failed"
    assert item["stage"] == "vector"
    assert item["retries"] == 2
    assert item["truncated"] is True
    assert item["created_at"] == datetime(2026, 11, 5, 10, 0, 0)
    assert item["talent_profile_id"] is None


def test_list_queue_rejects_unknown_status_before_db_query() -> None:
    """status 白名单校验必须发生在查库之前（db=None 也能验证）。"""
    with pytest.raises(BizError) as ctx:
        queue_service.list_queue(
            None,  # type: ignore[arg-type] - 非法 status 不应触达 db
            status="all",
            upload_batch_id=None,
            page=1,
            page_size=20,
            offset=0,
        )
    assert ctx.value.code == 400
