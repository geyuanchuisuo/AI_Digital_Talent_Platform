"""T-38a 环境自检脚本（B0）。

检查项：Python / Node 版本、MySQL 连通性、Milvus 连通性与版本、
LLM 配置存在性 + 一次最小对话调用、Embedding 配置存在性 + 一次最小向量调用。

用法：
    python backend/scripts/check_env.py
    python backend/scripts/check_env.py --skip-llm     # 暂不校验 LLM/Embedding

配置来源：环境变量优先，其次读取 backend/.env（若存在）。
缺项时只提示，不中断其他检查。
"""

import json
import os
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "backend" / ".env"

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []


def record(level, item, detail):
    results.append((level, item, detail))
    print(f"{level} {item}：{detail}")


def load_env_file(path: Path):
    """极简 .env 解析：KEY=VALUE，忽略注释与空行；不覆盖已有环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get(key: str, default: str | None = None):
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


def check_python():
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 11):
        record(PASS, "Python", f"{text}（要求 ≥3.11）")
    else:
        record(FAIL, "Python", f"{text} 过低，要求 ≥3.11")


def check_node():
    node = shutil.which("node")
    if not node:
        record(FAIL, "Node", "未找到 node 命令，要求 ≥18")
        return
    import subprocess

    try:
        out = subprocess.check_output([node, "-v"], text=True, timeout=10).strip()
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "Node", f"执行 node -v 失败：{exc}")
        return
    major = int(out.lstrip("v").split(".")[0]) if out.lstrip("v").split(".")[0].isdigit() else 0
    if major >= 18:
        record(PASS, "Node", f"{out}（要求 ≥18）")
    else:
        record(FAIL, "Node", f"{out} 过低，要求 ≥18")


def check_mysql():
    host = get("MYSQL_HOST", "localhost")
    port = int(get("MYSQL_PORT", "3306"))
    user = get("MYSQL_USER", "root")
    password = get("MYSQL_PASSWORD")
    db = get("MYSQL_DB", "ai_talent")

    if password is None:
        record(FAIL, "MySQL", f"缺少 MYSQL_PASSWORD（backend/.env 或环境变量）；目标 {host}:{port}/{db}")
        return

    try:
        import pymysql
    except ImportError:
        record(FAIL, "MySQL", "未安装 pymysql，执行 pip install pymysql")
        return

    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            connect_timeout=5, read_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SHOW DATABASES LIKE %s", (db,))
            has_db = cur.fetchone() is not None
        conn.close()
        if has_db:
            record(PASS, "MySQL", f"{host}:{port} 连通，版本 {version}，库 {db} 已存在")
        else:
            record(WARN, "MySQL", f"{host}:{port} 连通，版本 {version}，库 {db} 尚未创建（T-03 init_db.py 会建）")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "MySQL", f"{host}:{port} 连接失败：{exc}")


def check_milvus():
    uri = get("MILVUS_URI", "http://localhost:19530")
    try:
        from pymilvus import MilvusClient
    except ImportError:
        record(FAIL, "Milvus", "未安装 pymilvus，执行 pip install pymilvus==3.0.2")
        return

    try:
        client = MilvusClient(uri=uri)
        version = client.get_server_version()
        record(PASS, "Milvus", f"{uri} 连通，服务端版本 {version}（pymilvus 客户端见下）")
        try:
            import pymilvus

            record(PASS, "pymilvus", f"{pymilvus.__version__}（T-37 已验证与 v2.6.24 兼容）")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "Milvus", f"{uri} 连接失败：{exc}")


def http_post_json(url: str, payload: dict, api_key: str, timeout: float):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_llm():
    base_url = get("LLM_BASE_URL")
    api_key = get("LLM_API_KEY")
    model = get("LLM_MODEL", "deepseek-chat")

    missing = [name for name, value in (
        ("LLM_BASE_URL", base_url),
        ("LLM_API_KEY", api_key),
    ) if value is None]
    if missing:
        record(FAIL, "LLM 配置", f"缺少 {'、'.join(missing)}（backend/.env 或环境变量），暂不发起最小调用")
        return

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 512,
    }
    try:
        started = time.monotonic()
        body = http_post_json(url, payload, api_key, timeout=60)
        elapsed = time.monotonic() - started
        choice = body["choices"][0]
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        reasoning = msg.get("reasoning_content") or ""
        finish = choice.get("finish_reason")
        if not content:
            record(
                FAIL,
                "LLM 最小调用",
                f"model={model} 返回 content 为空（finish_reason={finish}，"
                f"reasoning_content 长度={len(reasoning)}）；"
                f"推理模型思维链可能吃光 token，见开发日志 H-5，需加大 max_tokens",
            )
            return
        record(
            PASS,
            "LLM 最小调用",
            f"model={model}，{elapsed:.1f}s，finish_reason={finish}，"
            f"content={content[:30]!r}（非空断言通过）",
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        record(FAIL, "LLM 最小调用", f"HTTP {exc.code}：{detail}")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "LLM 最小调用", f"{type(exc).__name__}：{exc}")


def check_embedding():
    base_url = get("EMBEDDING_BASE_URL") or get("LLM_BASE_URL")
    api_key = get("EMBEDDING_API_KEY") or get("LLM_API_KEY")
    model = get("EMBEDDING_MODEL", "text-embedding-v3")
    expected_dim = int(get("EMBEDDING_DIM", "1024"))

    if not base_url or not api_key:
        record(FAIL, "Embedding 配置", "缺少 EMBEDDING_BASE_URL/EMBEDDING_API_KEY（可复用 LLM 配置），暂不发起最小调用")
        return

    url = base_url.rstrip("/") + "/embeddings"
    payload = {"model": model, "input": "环境自检"}
    try:
        body = http_post_json(url, payload, api_key, timeout=20)
        vector = body["data"][0]["embedding"]
        dim = len(vector)
        if dim == expected_dim:
            record(PASS, "Embedding 最小调用", f"model={model}，维度 {dim} 符合 EMBEDDING_DIM={expected_dim}")
        else:
            record(FAIL, "Embedding 维度", f"实际 {dim}，配置 EMBEDDING_DIM={expected_dim}，Milvus collection 会不匹配")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        record(FAIL, "Embedding 最小调用", f"HTTP {exc.code}：{detail}")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "Embedding 最小调用", f"{type(exc).__name__}：{exc}")


def main():
    skip_llm = "--skip-llm" in sys.argv
    print("=" * 64)
    print("AI数字化人才平台 · 环境自检（T-38a）")
    print(f"项目根目录：{ROOT}")
    print(f".env：{ENV_FILE}（{'存在' if ENV_FILE.exists() else '不存在，仅读环境变量'}）")
    print("=" * 64)

    load_env_file(ENV_FILE)

    check_python()
    check_node()
    check_mysql()
    check_milvus()
    if skip_llm:
        record(WARN, "LLM/Embedding", "已按 --skip-llm 跳过最小调用")
    else:
        check_llm()
        check_embedding()

    failed = [item for level, item, _ in results if level == FAIL]
    warned = [item for level, item, _ in results if level == WARN]

    print("=" * 64)
    if failed:
        print(f"结果：{FAIL} 未就绪，失败项 {len(failed)} 个：{'、'.join(failed)}")
        print("处理：按上面每项的提示补齐配置后重跑本脚本。")
        return 1
    if warned:
        print(f"结果：{WARN} 基本就绪，提示项 {len(warned)} 个：{'、'.join(warned)}")
        return 0
    print(f"结果：{PASS} 全部通过，可以开始 T-01。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
