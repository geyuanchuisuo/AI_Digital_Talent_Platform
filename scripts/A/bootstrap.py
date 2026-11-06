#!/usr/bin/env python3
"""一键交付环境初始化（板块 A）。

在仓库根目录执行：
    python scripts/A/bootstrap.py            # 自动创建环境并自检
    python scripts/A/bootstrap.py --start    # 自动创建环境 + 自检 + 启动前后端
    python scripts/A/bootstrap.py --no-docker  # 复用客户已有的 MySQL / Milvus
Windows 也可直接双击 scripts/A/bootstrap.cmd；Linux/macOS 用 scripts/A/bootstrap.sh。

自动完成（幂等，重复执行不会覆盖客户已填的配置或数据）：
  1. 生成 backend/.env（随机 JWT_SECRET、真实供应商默认地址、MySQL/Milvus 连接）
  2. 生成 frontend/.env.local（API 基址）
  3. 按需用 Docker 拉起 MySQL + Milvus（端口已占用则自动跳过）
  4. 建 backend/.venv 并装 requirements.txt；frontend 执行 npm ci
  5. init_db.py 幂等建库建表 + seed（hr/hr123456）+ 示例岗位
  6. check_env.py 自检；Milvus collection 由后端启动时 lifespan 自动创建
  7. --start 时启动 uvicorn(--workers 1) 与 next dev，并等待 Ctrl+C

外部大模型的 API Key 不能写进 Git，需客户在根 .env 填一次（脚本会提示）。
不填也能把环境完整建好，只是简历解析 / AI 评分 / 向量检索不可用。
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
COMPOSE = ROOT / "deploy" / "milvus" / "docker-compose.yml"
ROOT_ENV = ROOT / ".env"
ROOT_ENV_EXAMPLE = ROOT / ".env.example"
BACKEND_ENV = BACKEND / ".env"
BACKEND_ENV_EXAMPLE = BACKEND / ".env.example"
FRONTEND_ENV = FRONTEND / ".env.local"
FRONTEND_ENV_EXAMPLE = FRONTEND / ".env.local.example"

DEFAULT_MYSQL_PASSWORD = "aitalent_2026"
PLACEHOLDER_MARKERS = ("your-", "sk-your", "change-me", "请填写", "placeholder")


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[bootstrap][警告] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[bootstrap][失败] {msg}", flush=True)
    sys.exit(1)


def run(cmd, cwd: Path | None = None, env: dict | None = None, check: bool = True):
    """跨平台执行命令；Windows 下 npm/uvicorn 等是 .cmd，需要 shell。"""
    use_shell = os.name == "nt"
    display = " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    log(f"$ {display}")
    merged = os.environ.copy()
    if env:
        merged.update({k: str(v) for k, v in env.items() if v is not None})
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged, shell=use_shell, check=check)


def parse_env(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略注释、空行与行内注释；不读任何敏感文件进日志。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        # 去掉行内注释（模板里 VECTOR_SYNC_TIMEOUT=5  # ... 这种）
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        value = value.strip('"').strip("'")
        if key.strip():
            out[key.strip()] = value
    return out


def is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    low = value.lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def wait_port(port: int, timeout: int, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            log(f"{label} 端口 {port} 已就绪")
            return True
        time.sleep(2)
    return False


def ensure_root_env() -> dict[str, str]:
    """根 .env 是客户唯一需要碰的文件；不存在时从模板复制一份。"""
    if not ROOT_ENV.exists():
        if not ROOT_ENV_EXAMPLE.exists():
            die("缺少根 .env.example，无法生成客户配置")
        ROOT_ENV.write_text(ROOT_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        log("已从 .env.example 生成根 .env（客户只需填模型 Key）")
    cfg = parse_env(ROOT_ENV)
    # 环境变量优先级最高，方便 CI / 容器注入
    for key in list(cfg) + [
        "LLM_API_KEY", "EMBEDDING_API_KEY", "MYSQL_PASSWORD", "MYSQL_HOST",
        "MYSQL_PORT", "MYSQL_DB", "MILVUS_URI", "MILVUS_PORT",
        "MILVUS_METRICS_PORT", "MILVUS_COLLECTION", "COMPOSE_PROJECT_NAME",
        "APP_PORT", "FRONTEND_PORT",
    ]:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def write_backend_env(values: dict[str, str], note: str) -> None:
    header = [
        "# 由 scripts/A/bootstrap.py 自动生成，请勿提交（.gitignore 已忽略 .env）",
        f"# {note}；{time.strftime('%Y-%m-%d %H:%M:%S')}；修改配置请优先改仓库根 .env 后重跑 bootstrap",
        "",
    ]
    body = [f"{k}={v}" for k, v in values.items()]
    BACKEND_ENV.write_text("\n".join(header + body) + "\n", encoding="utf-8")


def customer_overrides(cfg: dict[str, str]) -> dict[str, str]:
    """客户在根 .env / 环境变量里「显式填过」的键；空值与占位值不算。"""
    return {k: v for k, v in cfg.items() if v != "" and not is_placeholder(v)}


def derived_runtime(cfg: dict[str, str]) -> dict[str, str]:
    """由端口配置派生连接串，避免客户改了端口却仍连默认端口。

    典型场景：客户机 3306/19530 被别的项目占用，需在根 .env 指定
    MYSQL_PORT / MILVUS_PORT，让 bootstrap 起隔离端口的容器并连到它们。
    """
    out: dict[str, str] = {}
    milvus_port = cfg.get("MILVUS_PORT", "")
    explicit_uri = cfg.get("MILVUS_URI", "")
    if milvus_port and (not explicit_uri or is_placeholder(explicit_uri)):
        out["MILVUS_URI"] = f"http://localhost:{milvus_port}"
    return out


def ensure_backend_env(cfg: dict[str, str]) -> dict[str, str]:
    """从 backend/.env.example 取默认值 + 根 .env 覆盖 + 自动生成项，写出 backend/.env。

    重跑时只同步客户显式改过的键，保留已生成的 JWT_SECRET / MYSQL_PASSWORD 等自动项，
    这样「填完 Key 再重跑一次即可生效」成立，又不会覆盖客户配置。
    """
    overrides = customer_overrides(cfg)
    overrides.update(derived_runtime(cfg))
    if BACKEND_ENV.exists():
        existing = parse_env(BACKEND_ENV)
        changed = {k: v for k, v in overrides.items() if existing.get(k) != v}
        if changed:
            existing.update(changed)
            write_backend_env(existing, f"已同步 {len(changed)} 项客户配置：{', '.join(sorted(changed))}")
            log(f"backend/.env 已同步 {len(changed)} 项客户配置（自动生成的密钥保持不变）")
        else:
            log("backend/.env 已存在，保留客户现有配置（不覆盖）")
        return existing
    values = parse_env(BACKEND_ENV_EXAMPLE)
    values.update(overrides)
    values.setdefault("MYSQL_PASSWORD", DEFAULT_MYSQL_PASSWORD)
    if is_placeholder(values.get("MYSQL_PASSWORD")):
        values["MYSQL_PASSWORD"] = DEFAULT_MYSQL_PASSWORD
    if is_placeholder(values.get("JWT_SECRET")):
        values["JWT_SECRET"] = secrets.token_urlsafe(48)
        log("已生成随机 JWT_SECRET（写入 backend/.env，不进 Git）")
    # 前端端口允许客户覆盖；后端自身端口也允许
    values.setdefault("APP_PORT", "8000")
    write_backend_env(values, "首次生成")
    log(f"已生成 backend/.env（{len(values)} 项配置）")
    return values


def ensure_frontend_env(backend_env: dict[str, str], cfg: dict[str, str]) -> None:
    port = cfg.get("APP_PORT") or backend_env.get("APP_PORT") or "8000"
    expected_base = f"http://localhost:{port}"
    if FRONTEND_ENV.exists():
        existing = parse_env(FRONTEND_ENV)
        if existing.get("NEXT_PUBLIC_API_BASE") == expected_base:
            log("frontend/.env.local 已存在，保留客户现有配置（不覆盖）")
            return
        existing["NEXT_PUBLIC_API_BASE"] = expected_base
        FRONTEND_ENV.write_text(
            "# 由 scripts/A/bootstrap.py 自动生成，请勿提交（API 基址已随 APP_PORT 同步）\n"
            + "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
            encoding="utf-8",
        )
        log(f"frontend/.env.local 的 API 基址已同步为 {expected_base}")
        return
    values = parse_env(FRONTEND_ENV_EXAMPLE) if FRONTEND_ENV_EXAMPLE.exists() else {}
    values["NEXT_PUBLIC_API_BASE"] = expected_base
    values.setdefault("NEXT_PUBLIC_POLL_INTERVAL_MS", "2000")
    values.setdefault("NEXT_PUBLIC_UPLOAD_MAX_MB", "20")
    FRONTEND_ENV.write_text(
        "# 由 scripts/A/bootstrap.py 自动生成，请勿提交\n"
        + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n",
        encoding="utf-8",
    )
    log(f"已生成 frontend/.env.local（API -> {expected_base}）")


def venv_python() -> Path:
    name = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return BACKEND / ".venv" / name


def ensure_infra(backend_env: dict[str, str], no_docker: bool) -> None:
    mysql_port = int(backend_env.get("MYSQL_PORT") or "3306")
    # 端口优先取显式 MILVUS_PORT，否则从 MILVUS_URI 解析，最后才用 19530
    raw_milvus_port = backend_env.get("MILVUS_PORT") or ""
    if raw_milvus_port:
        milvus_port = int(raw_milvus_port)
    else:
        uri = backend_env.get("MILVUS_URI", "")
        try:
            milvus_port = int(uri.rsplit(":", 1)[1].split("/")[0])
        except (ValueError, IndexError):
            milvus_port = 19530
    need_mysql = not port_open(mysql_port)
    need_milvus = not port_open(milvus_port)
    if not need_mysql and not need_milvus:
        log(f"检测到本机 {mysql_port}/{milvus_port} 已有服务，跳过容器创建")
        return
    if no_docker:
        warn(f"--no-docker 已指定，但端口 {mysql_port if need_mysql else milvus_port} 未就绪；后续建库/向量可能失败")
        return
    if shutil.which("docker") is None:
        die("本机没有可用的 MySQL / Milvus，且未安装 Docker。请安装 Docker Desktop 后重跑，或自行准备 MySQL 8 与 Milvus 2.6 再用 --no-docker")
    services: list[str] = []
    if need_mysql:
        services.append("mysql")
    if need_milvus:
        services += ["etcd", "minio", "standalone"]
    env = {"MYSQL_ROOT_PASSWORD": backend_env.get("MYSQL_PASSWORD", DEFAULT_MYSQL_PASSWORD),
           "MYSQL_DB": backend_env.get("MYSQL_DB", "ai_talent"),
           "MYSQL_PORT": str(mysql_port),
           "MILVUS_PORT": str(milvus_port),
           # 默认 metrics 端口随 Milvus 端口联动（19530→9091），避免隔离演练时撞上已有容器
           "MILVUS_METRICS_PORT": backend_env.get("MILVUS_METRICS_PORT", str(milvus_port - 10439))}
    # 仅当客户显式指定项目名时才注入，默认保持 compose 原有项目名与卷名不变
    project = backend_env.get("COMPOSE_PROJECT_NAME")
    if project:
        env["COMPOSE_PROJECT_NAME"] = project
        env["CONTAINER_PREFIX"] = project
    run(["docker", "compose", "-f", str(COMPOSE), "up", "-d", *services], cwd=ROOT, env=env)
    log(f"已启动容器服务：{', '.join(services)}（数据卷持久化，重跑不会丢数据）")
    if need_mysql and not wait_port(mysql_port, 180, "MySQL"):
        die(f"MySQL 端口 {mysql_port} 在 180 秒内未就绪，请查看 docker compose logs mysql")
    if need_milvus and not wait_port(milvus_port, 240, "Milvus"):
        die(f"Milvus 端口 {milvus_port} 在 240 秒内未就绪，请查看 docker compose logs standalone")
    # MySQL 端口通不代表完成初始化，init_database() 会重试


def ensure_python_deps() -> Path:
    py = venv_python()
    if not py.exists():
        log("创建 backend/.venv ...")
        run([sys.executable, "-m", "venv", str(BACKEND / ".venv")], cwd=BACKEND)
    else:
        log("backend/.venv 已存在，跳过创建")
    log("安装后端依赖（requirements.txt）...")
    run([str(py), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")], cwd=BACKEND)
    return py


def ensure_node_deps() -> None:
    if (FRONTEND / "node_modules").exists():
        log("frontend/node_modules 已存在，跳过 npm ci")
        return
    if shutil.which("npm") is None and shutil.which("npm.cmd") is None:
        die("未找到 npm，请先安装 Node.js 20+ 后重跑")
    log("安装前端依赖（npm ci，按 package-lock.json 锁定版本）...")
    run(["npm", "ci"], cwd=FRONTEND)


def init_database(py: Path) -> None:
    log("初始化数据库（建库 / 13 张表 / seed 账号与示例岗位，幂等）...")
    last = None
    for attempt in range(1, 6):
        result = run([str(py), "scripts/init_db.py"], cwd=BACKEND, check=False)
        if result.returncode == 0:
            log("数据库初始化完成（hr/hr123456）")
            return
        last = result.returncode
        warn(f"第 {attempt}/5 次初始化失败（MySQL 可能仍在启动），5 秒后重试")
        time.sleep(5)
    die(f"init_db.py 连续 5 次失败（退出码 {last}），请检查 MySQL 地址/密码与容器日志")


def self_check(py: Path, backend_env: dict[str, str]) -> None:
    real_key = not is_placeholder(backend_env.get("LLM_API_KEY"))
    args = [str(py), "scripts/check_env.py"]
    if not real_key:
        args.append("--skip-llm")
    log("运行环境自检" + ("（已配置模型 Key，执行完整检查）" if real_key else "（未配置真实 Key，跳过 LLM/Embedding 最小调用）"))
    result = run(args, cwd=BACKEND, check=False)
    if result.returncode != 0:
        warn(f"check_env.py 返回 {result.returncode}；请按上方 FAIL 项处理，环境文件与依赖已建好")


def start_services(py: Path, cfg: dict[str, str]) -> None:
    backend_port = int(cfg.get("APP_PORT") or "8000")
    frontend_port = int(cfg.get("FRONTEND_PORT") or "3000")
    procs = []
    try:
        log("启动后端 uvicorn（--workers 1 为硬约束）...")
        procs.append(subprocess.Popen(
            [str(py), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(backend_port), "--workers", "1"],
            cwd=str(BACKEND), shell=(os.name == "nt")))
        log("启动前端 Next.js dev ...")
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        # package.json 的 dev 已带 -p 3000；只有自定义端口时才直接调 next，避免 -p 重复
        if frontend_port == 3000:
            fe_cmd = [npm_cmd, "run", "dev"]
        else:
            fe_cmd = [npm_cmd, "exec", "next", "dev", "-p", str(frontend_port)]
        procs.append(subprocess.Popen(fe_cmd, cwd=str(FRONTEND), shell=(os.name == "nt")))
        log(f"前端 http://localhost:{frontend_port}　后端 http://localhost:{backend_port}/api/health")
        log("按 Ctrl+C 停止服务")
        while True:
            time.sleep(1)
            if any(p.poll() is not None for p in procs):
                warn("有服务进程提前退出，请查看上方日志")
                break
    except KeyboardInterrupt:
        log("正在停止服务 ...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="一键交付环境初始化")
    parser.add_argument("--start", action="store_true", help="环境建好后直接启动前后端")
    parser.add_argument("--no-docker", action="store_true", help="不自动创建容器，复用已有 MySQL/Milvus")
    args = parser.parse_args()

    log(f"仓库根目录：{ROOT}")
    if sys.version_info < (3, 11):
        die(f"Python {sys.version.split()[0]} 过低，需要 3.11+（requirements.txt 约定）")

    cfg = ensure_root_env()
    backend_env = ensure_backend_env(cfg)
    ensure_frontend_env(backend_env, cfg)

    if is_placeholder(backend_env.get("LLM_API_KEY")) and is_placeholder(cfg.get("LLM_API_KEY")):
        warn("根 .env 未填真实 LLM_API_KEY：环境仍会完整建好，但简历解析 / AI 评分 / 向量检索不可用")
        warn("请编辑仓库根 .env 填入讯飞 Spark 的 Key（如有独立 Embedding Key 也填），再重跑本脚本即可生效")

    ensure_infra(backend_env, args.no_docker)
    py = ensure_python_deps()
    ensure_node_deps()
    init_database(py)
    self_check(py, backend_env)

    log("环境创建完成 ✅")
    log("登录账号：hr / hr123456（seed，生产环境请立即修改）")
    if not args.start:
        log("启动服务：python scripts/A/bootstrap.py --start（或 Windows 双击 scripts/A/bootstrap.cmd 选带 --start 的命令）")
    else:
        start_services(py, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
