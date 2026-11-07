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
    """按 seed() 中四次 scalar 的固定顺序返回 count（hr/personal/job/setting）。"""

    def __init__(self, counts: tuple[int, int, int, int]):
        self._counts = iter(counts)
        self.added: list[object] = []
        self.committed = False

    def scalar(self, statement):  # noqa: ARG002 - 顺序固定，无需解析 SQL
        return next(self._counts)

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


def test_seed_creates_all_items_on_empty_db():
    session = FakeSession((0, 0, 0, 0))
    created = init_db.seed(session)
    assert len(created) == 4
    assert session.committed

    user = next(x for x in session.added if isinstance(x, User) and x.username == "hr")
    personal_user = next(
        x for x in session.added if isinstance(x, User) and x.username == "personal"
    )
    profile = next(x for x in session.added if isinstance(x, TalentProfile))
    job = next(x for x in session.added if isinstance(x, JobProfile))
    setting = next(x for x in session.added if isinstance(x, SysSetting))

    # 个人端演示账号：role=personal；person_id 绑定值由 t03 真实库用例验证
    # （假 session 不持久化，flush 后 ORM 对象 id 仍为 None）
    assert personal_user.role == "personal"
    assert personal_user.person_id is None or isinstance(personal_user.person_id, int)
    assert bcrypt.checkpw(b"personal123456", personal_user.password_hash.encode("ascii"))
    assert profile.name == "演示个人用户"

    assert user.username == "hr" and user.role == "hr" and user.is_active == 1
    assert bcrypt.checkpw(b"hr123456", user.password_hash.encode("ascii"))
    assert not bcrypt.checkpw(b"wrong", user.password_hash.encode("ascii"))

    assert job.title == "高级后端工程师" and job.version == 1
    assert job.min_degree == "本科"
    assert job.must_skills_json == ["python", "fastapi", "mysql"]
    assert job.bonus_skills_json == ["milvus", "docker"]
    assert job.hard_rule_switches_json == {
        "degree": True, "work_years": True, "must_skills": True,
    }
    assert job.pass_threshold == Decimal("75.00")
    assert job.pending_threshold == Decimal("60.00")
    assert sum(job.weights_json.values()) == 100

    assert setting.setting_key == SETTING_TASK_CONCURRENCY
    assert setting.value_type == "int"
    assert 1 <= int(setting.setting_value) <= 5


def test_seed_is_idempotent_when_everything_exists():
    """§9 第 4 条：重复执行跳过已存在数据，不删除不覆盖。"""
    session = FakeSession((1, 1, 1, 1))
    assert init_db.seed(session) == []
    assert session.added == []
    assert session.committed


def test_seed_only_fills_missing_items():
    session = FakeSession((1, 1, 0, 1))
    created = init_db.seed(session)
    assert len(created) == 1 and "job_profile" in created[0]
    assert len(session.added) == 1 and isinstance(session.added[0], JobProfile)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "1"),
        (False, "0"),
        (0, "0"),
        (42, "42"),
        (Decimal("75.00"), "75.00"),
        (b"\x00\xff", "X'00ff'"),
        (datetime(2026, 11, 5, 13, 30, 0), "'2026-11-05 13:30:00'"),
        ("O'Brien", "'O''Brien'"),
        ("line1\nline2", "'line1\\nline2'"),
        ("a\\b", "'a\\\\b'"),
        ('{"k": "v"}', "'{\"k\": \"v\"}'"),
    ],
)
def test_sql_literal(value, expected):
    assert backup_db.sql_literal(value) == expected


def test_sql_literal_quote_roundtrip_via_pymysql_rules():
    """含分号/引号/换行的字符串必须在 INSERT 里保持为单个语句。"""
    literal = backup_db.sql_literal("a;b'c\nd")
    sql = f"INSERT INTO `t` (`c`) VALUES ({literal});\nSELECT 1;"
    statements = list(backup_db.iter_statements(sql))
    assert len(statements) == 2
    assert statements[0].startswith("INSERT INTO")
    assert statements[1] == "SELECT 1"


def test_iter_statements_strips_comments_and_backticks():
    sql = (
        "-- header comment\n"
        "DROP TABLE IF EXISTS `weird;table`;\n"
        "CREATE TABLE `t` (`c` VARCHAR(10));\n"
        "# hash comment\n"
        "INSERT INTO `t` VALUES ('x;y');\n"
    )
    statements = list(backup_db.iter_statements(sql))
    assert len(statements) == 3
    assert statements[0].startswith("DROP TABLE")
    assert "weird;table" in statements[0]
    assert statements[2].startswith("INSERT INTO")


def test_restore_refuses_production_database():
    from app.config import settings

    with pytest.raises(ValueError, match="拒绝恢复到生产库"):
        backup_db.restore(Path("whatever.sql"), settings.mysql_db)
    with pytest.raises(ValueError, match="拒绝恢复到生产库"):
        # 未在磁盘上的文件也应先做库名校验（顺序无关，安全优先）
        backup_db.restore(Path("/not/exists.sql"), settings.mysql_db)
