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

import app.models  # noqa: E402,F401 - 触发 34 张表注册到 Base.metadata
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import JobProfile, SysSetting, TalentProfile, User  # noqa: E402
from app.modules.system.models import SysMenu, SysRole, SysUserRole  # noqa: E402
from app.models.enums import SETTING_TASK_CONCURRENCY  # noqa: E402

SEED_USERNAME = "hr"
SEED_PASSWORD = "hr123456"
SEED_JOB_TITLE = "高级后端工程师"
SEED_PERSONAL_USERNAME = "personal"
SEED_PERSONAL_PASSWORD = "personal123456"
# 8c：基础角色（RBAC 最小集；data_scope 三值见 system/models.py）
SEED_ROLES = (
    ("system_admin", "系统管理员", "all"),
    ("hr", "HR/招聘", "all"),
    ("dept_manager", "部门经理", "dept"),
    ("trainer", "培训管理员/讲师", "dept"),
    ("employee", "员工", "self"),
    ("candidate", "候选人", "self"),
)
SEED_PERSONAL_PASSWORD = "personal123456"


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


def stamp_alembic_head() -> bool | None:
    """空库/重建库场景：create_all 建好全部表后把 alembic 状态标到 head。

    仅针对「表由 create_all 建齐」的场景；已有旧库（13 表缺 C 表）不要跑本脚本
    兜升级，按 docs/modules/A/迁移链说明.md 用 stamp 基线 + upgrade head。alembic 不可用
    （未安装或目录缺失）时返回 None 静默跳过，不阻塞建库。
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        return None

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not alembic_ini.exists():
        return None
    try:
        cfg = Config(str(alembic_ini))
        cfg.set_main_option("script_location", str(alembic_ini.parent / "migrations"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.stamp(cfg, "head")
        return True
    except Exception as exc:  # 迁移链异常不掩盖建表结果，但要有痕迹
        print(f"alembic stamp 跳过：{exc}")
        return False


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

    # ---- 个人端演示账号（REQ-B-20261106-02 第 5 项：绑定样例，虚构数据）----
    personal_count = session.scalar(
        select(func.count()).select_from(User).where(User.username == SEED_PERSONAL_USERNAME)
    )
    if not personal_count:
        demo_profile = TalentProfile(
            name="演示个人用户",
            phone="13800001234",
            email="demo-personal@example.com",
            degree="本科",
            current_title="后端工程师",
            work_years=Decimal("3.0"),
            skills_json=["python", "mysql"],
            source="manual",
        )
        session.add(demo_profile)
        session.flush()  # 取自增 id 作为 person_id
        session.add(
            User(
                username=SEED_PERSONAL_USERNAME,
                password_hash=bcrypt.hashpw(
                    SEED_PERSONAL_PASSWORD.encode("utf-8"), bcrypt.gensalt()
                ).decode("ascii"),
                display_name="演示个人账号",
                role="personal",
                person_id=demo_profile.id,
                is_active=1,
            )
        )
        created.append(
            f"user:{SEED_PERSONAL_USERNAME}(role=personal, person_id={demo_profile.id}) + talent_profile:演示个人用户"
        )

    created.extend(seed_system_roles(session))
    created.extend(_seed_job_and_setting(session))
    return created


def seed_system_roles(session) -> list[str]:
    """基础角色与绑定（8c：RBAC 最小集，幂等按 code；独立函数便于单测 mock）。"""
    created: list[str] = []
    role_ids: dict[str, int] = {}
    for code, name, scope in SEED_ROLES:
        exists = session.scalar(select(func.count()).select_from(SysRole).where(SysRole.code == code))
        if not exists:
            role = SysRole(code=code, name=name, data_scope=scope, status="active")
            session.add(role)
            session.flush()
            created.append(f"sys_role:{code}")
        role_ids[code] = session.scalar(select(SysRole.id).where(SysRole.code == code))

    # hr 账号绑定 hr 角色（sys_user_roles 多职责最小映射；重复执行幂等）
    hr_user_id = session.scalar(select(User.id).where(User.username == SEED_USERNAME))
    if hr_user_id and role_ids.get("hr"):
        bound = session.scalar(
            select(func.count()).select_from(SysUserRole).where(
                SysUserRole.user_id == hr_user_id, SysUserRole.role_id == role_ids["hr"]
            )
        )
        if not bound:
            session.add(SysUserRole(user_id=hr_user_id, role_id=role_ids["hr"]))
            created.append("sys_user_role:hr->hr")
    return created


def _seed_job_and_setting(session) -> list[str]:
    """岗位与运行配置种子（原主 seed 尾部；拆分事故恢复——2026-11-08）。"""
    created: list[str] = []
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

    stamped = stamp_alembic_head()

    print(f"数据库 {settings.mysql_db}：{'本次新建' if db_created else '已存在'}")
    print(f"表数量：{len(tables)} → {', '.join(tables)}")
    print(f"本次种子新增：{created or '无（已存在，保留现值）'}")
    if stamped is not None:
        print(f"alembic stamp head：{'成功' if stamped else '跳过（alembic/迁移目录不可用，升级场景请按 docs/modules/A/迁移链说明.md 手工 stamp）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
