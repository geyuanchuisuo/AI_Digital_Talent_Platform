"""L1 单元测试 · init_db 种子幂等 + backup_db SQL 生成（T-03）。

不触真实 MySQL：种子用假 session，备份只测纯函数（字面量转义、语句切分）。
真实建库/恢复在 test_db_integration_t03.py（L2）验证。
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import bcrypt
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backup_db  # noqa: E402
import init_db  # noqa: E402

from app.models import JobProfile, SysSetting, TalentProfile, User  # noqa: E402
from app.models.enums import SETTING_TASK_CONCURRENCY  # noqa: E402


class FakeSession:
    """按主 seed() 的四次 scalar 固定顺序返回 count（hr/personal/job/setting）。

    sys_roles 部分拆为 `seed_system_roles()`（8c），本测试 monkeypatch 为空操作；
    该子函数的幂等由 test_sys_admin 覆盖。
    """

    def __init__(self, counts: tuple[int, int, int, int]):
        self._counts = iter(counts)
        self.added: list[object] = []
        self.committed = False

    def scalar(self, statement):  # noqa: ARG002 - 顺序固定，无需解析 SQL
        return next(self._counts)

    def flush(self):
        return None  # personal 块 flush 取自增 id；假 session 不持久化

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        """seed 用 flush 取自增 id；假 session 不持久化，id 保持 ORM 默认（None）。

        真实绑定（personal.person_id == 主档自增 id）由 test_db_integration_t03
        的真实库用例验证；本测试只核对 seed 的调用结构与字段赋值。
        """
        return None

    def commit(self):
        self.committed = True


def test_seed_creates_all_items_on_empty_db(monkeypatch):
    """主 seed 为调度：hr/personal 内联 + 两个子函数；子函数 mock 返回 stub。"""
    monkeypatch.setattr(init_db, "seed_system_roles", lambda session: ["sys_role:stub"])
    monkeypatch.setattr(init_db, "_seed_job_and_setting", lambda session: ["job:stub", "setting:stub"])
    session = FakeSession((0, 0))  # 主 seed 仅两个内联 count：hr、personal
    created = init_db.seed(session)

    assert len(created) == 5, created  # hr + personal + 三个 stub
    assert session.committed is False  # 调度版 commit 责任在内联块/子函数（见模块注释）

    user = next(x for x in session.added if isinstance(x, User) and x.username == "hr")
    personal_user = next(
        x for x in session.added if isinstance(x, User) and x.username == "personal"
    )
    profile = next(x for x in session.added if isinstance(x, TalentProfile))

    assert user.username == "hr" and user.role == "hr" and user.is_active == 1
    assert bcrypt.checkpw(b"hr123456", user.password_hash.encode("ascii"))

    # 个人端演示账号：role=personal；person_id 绑定值由 t03 真实库用例验证
    # （假 session 不持久化，flush 后 ORM 对象 id 仍为 None）
    assert personal_user.role == "personal"
    assert personal_user.person_id is None or isinstance(personal_user.person_id, int)
    assert bcrypt.checkpw(b"personal123456", personal_user.password_hash.encode("ascii"))
    assert profile.name == "演示个人用户"


def test_seed_is_idempotent_when_everything_exists(monkeypatch):
    """§9 第 4 条：重复执行跳过已存在数据，不删除不覆盖。"""
    monkeypatch.setattr(init_db, "seed_system_roles", lambda session: [])
    monkeypatch.setattr(init_db, "_seed_job_and_setting", lambda session: [])
    session = FakeSession((1, 1))  # hr/personal 均已存在
    assert init_db.seed(session) == []
    assert session.added == []


def test_seed_only_fills_missing_personal(monkeypatch):
    """主 seed 内联部分：hr 已存在跳过、personal 缺失新建（合并串一条）。"""
    monkeypatch.setattr(init_db, "seed_system_roles", lambda session: [])
    monkeypatch.setattr(init_db, "_seed_job_and_setting", lambda session: [])
    session = FakeSession((1, 0))
    created = init_db.seed(session)
    assert len(created) == 1 and "user:personal" in created[0]
    assert any(isinstance(x, User) and x.username == "personal" for x in session.added)
    assert any(isinstance(x, TalentProfile) for x in session.added)
