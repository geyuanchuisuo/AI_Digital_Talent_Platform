"""建库建表 + 种子数据（T-03，幂等）。

输入文档：`docs/数据库设计.md` v1.2 §1 / §9。
运行：`./.venv/Scripts/python.exe scripts/init_db.py`（在 `main/backend` 目录；脚本已处理 sys.path）

做三件事，全部可重复执行：
1. `CREATE DATABASE IF NOT EXISTS ai_talent`（utf8mb4 / utf8mb4_unicode_ci）
2. `Base.metadata.create_all(checkfirst=True)` —— 13 张表，一期不用 Alembic
3. 种子：hr/hr123456（bcrypt）、示例岗位 version=1、sys_settings.task_concurrency

幂等口径（§9 第 4 条）：已存在的数据跳过，**不删除不覆盖**；
`sys_settings` 已存在则保留现值（用户在页面改过并发后重跑 init_db 不会被 .env 重置）。
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import bcrypt  # noqa: E402
import pymysql  # noqa: E402
from sqlalchemy import func, inspect, select  # noqa: E402

import app.models  # noqa: E402,F401 - 触发 13 张表注册到 Base.metadata
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import JobProfile, SysSetting, User  # noqa: E402
from app.models.enums import SETTING_TASK_CONCURRENCY  # noqa: E402

SEED_USERNAME = "hr"
SEED_PASSWORD = "hr123456"
SEED_JOB_TITLE = "高级后端工程师"


def create_database() -> bool:
    """幂等建库；返回 True 表示本次新建、False 表示已存在。"""
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES LIKE %s", (settings.mysql_db,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(
                    f"CREATE DATABASE `{settings.mysql_db}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        return not exists
    finally:
        conn.close()


def create_tables() -> list[str]:
    """幂等建表（checkfirst=True）；返回库中实际表名。"""
    Base.metadata.create_all(bind=engine, checkfirst=True)
    return sorted(inspect(engine).get_table_names())


def seed(session) -> list[str]:
    """种子数据；返回本次新建项，重复执行返回空列表。"""
    created: list[str] = []

    user_count = session.scalar(
        select(func.count()).select_from(User).where(User.username == SEED_USERNAME)
    )
    if not user_count:
        session.add(
            User(
                username=SEED_USERNAME,
                password_hash=bcrypt.hashpw(
                    SEED_PASSWORD.encode("utf-8"), bcrypt.gensalt()
                ).decode("ascii"),
                display_name="HR 管理员",
                role="hr",
                is_active=1,
            )
        )
        created.append(f"user:{SEED_USERNAME}")

    job_count = session.scalar(
        select(func.count())
        .select_from(JobProfile)
        .where(JobProfile.title == SEED_JOB_TITLE, JobProfile.version == 1)
    )
    if not job_count:
        session.add(
            JobProfile(
                title=SEED_JOB_TITLE,
                department="研发部",
                jd_text="负责 AI 数字化人才平台后端服务的设计、开发与维护。",
                requirements_text="本科及以上学历，3 年以上后端开发经验；熟悉 Python、FastAPI、MySQL。",
                min_degree="本科",
                min_work_years=Decimal("3.0"),
                must_skills_json=["python", "fastapi", "mysql"],
                bonus_skills_json=["milvus", "docker"],
                hard_rule_switches_json={
                    "degree": True,
                    "work_years": True,
                    "must_skills": True,
                },
                pass_threshold=Decimal("75.00"),
                pending_threshold=Decimal("60.00"),
                weights_json={"skills": 40, "experience": 30, "education": 15, "others": 15},
                version=1,
                is_active=1,
            )
        )
        created.append(f"job_profile:{SEED_JOB_TITLE}@v1")

    setting_count = session.scalar(
        select(func.count())
        .select_from(SysSetting)
        .where(SysSetting.setting_key == SETTING_TASK_CONCURRENCY)
    )
    if not setting_count:
        session.add(
            SysSetting(
                setting_key=SETTING_TASK_CONCURRENCY,
                setting_value=str(settings.task_concurrency),
                value_type="int",
                description="TaskRunner 全局任务并发（1-5）",
            )
        )
        created.append(f"sys_settings:{SETTING_TASK_CONCURRENCY}")

    session.commit()
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="幂等建库建表 + 种子数据")
    parser.add_argument(
        "--no-seed", action="store_true", help="只建库建表，不写种子（供测试/迁移使用）"
    )
    args = parser.parse_args(argv)

    db_created = create_database()
    tables = create_tables()
    created: list[str] = []
    if not args.no_seed:
        with SessionLocal() as session:
            created = seed(session)

    print(f"数据库 {settings.mysql_db}：{'本次新建' if db_created else '已存在'}")
    print(f"表数量：{len(tables)} → {', '.join(tables)}")
    print(f"本次种子新增：{created or '无（已存在，保留现值）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
