"""T-31 续跑：解析段已在本轮真实完成，跳过上传/解析，补跑筛选 + 匹配批次性能段。

背景：完整脚本 perf_t31.py 在解析 21/22 时随会话中断被终止，进程已退出；
但 MySQL/Milvus 中 21 份画像与向量完好，parse_tasks 有完整时间戳。
本脚本复用这批真实数据，解析吞吐直接取 DB 实测，避免重跑 38 分钟解析。

产出：data/eval/perf_t31_report.json（与完整脚本同一路径 / 同一字段集合）
前置：后端 8000 在跑；本轮上传批次数据尚未被 cleanup 清理。
用法：PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -u scripts/perf_t31_resume.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import httpx
from sqlalchemy import text

from app.database import SessionLocal

EVAL_DIR = BASE_DIR / "data" / "eval"
REPORT_PATH = EVAL_DIR / "perf_t31_report.json"
API = "http://127.0.0.1:8000"

client: httpx.Client | None = None
TOKEN: str | None = None


def api(method: str, path: str, **kwargs):
    response = client.request(
        method, f"/api{path}", headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {response.status_code}: {response.text[:200]}")
    return response.json()["data"]


def poll_batch(kind: str, batch_id: int, timeout_sec: float = 60 * 60) -> tuple[float, dict]:
    started = time.monotonic()
    retried = False
    while time.monotonic() - started < timeout_sec:
        data = api("GET", f"/{kind}/batches/{batch_id}")
        print(f"    {kind}#{batch_id} {data['status']} {data['progress']}/{data['total']}", flush=True)
        if data["status"] in ("done", "failed"):
            if data["status"] == "failed":
                if not retried:
                    retried = True
                    print(f"    {kind}#{batch_id} failed，自动 retry 一次：{(data.get('error') or '')[:120]}", flush=True)
                    api("POST", f"/{kind}/batches/{batch_id}/retry")
                    time.sleep(10)
                    continue
                raise RuntimeError(f"批次失败（自动 retry 后仍失败）：{data['error']}")
            return time.monotonic() - started, data
        time.sleep(10)
    raise TimeoutError(f"{kind} 批次超时")


def hard_fail_count(kind: str, batch_id: int) -> int:
    data = api("GET", f"/{kind}/batches/{batch_id}/results", params={"page": 1, "page_size": 200})
    return sum(1 for item in data["items"] if item["hard_fail"])


def read_parse_facts() -> dict:
    """从 MySQL 读取本轮解析的真实起止时间与成败明细（不依赖已退出的脚本计时）。"""
    with SessionLocal() as db:
        n_success, t_start, t_end = db.execute(text(
            "SELECT COUNT(*), MIN(created_at), MAX(finished_at) "
            "FROM parse_tasks WHERE status = 'success'"
        )).one()
        total = db.execute(text("SELECT COUNT(*) FROM parse_tasks")).scalar()
        batch_id = db.execute(text("SELECT upload_batch_id FROM resume_files LIMIT 1")).scalar()
        failed = db.execute(text(
            "SELECT rf.filename, pt.error FROM parse_tasks pt "
            "JOIN resume_files rf ON rf.id = pt.resume_file_id WHERE pt.status = 'failed'"
        )).all()
    assert n_success >= 20, f"成功解析样本不足：{n_success}"
    wall = (t_end - t_start).total_seconds()
    return {
        "files": total,
        "run_mode": "resume：解析段沿用本轮真实实测（DB parse_tasks 时间戳），批次段续跑",
        "upload": {
            "upload_batch_id": batch_id,
            "accepted": total,
            "duplicates": 0,
            "elapsed": None,
        },
        "parse_concurrency_2": {
            "total": total,
            "success": n_success,
            "wall_seconds": round(wall, 1),
            "throughput_per_min": round(n_success / (wall / 60), 2),
            "source": "parse_tasks.created_at → finished_at（真实实测，并发 2）",
        },
        "parse_failures": [
            {"filename": f, "error": (e or "")[:200]} for f, e in failed
        ],
        "_upload_batch_id": batch_id,
    }


def main() -> int:
    global client, TOKEN

    report = read_parse_facts()
    batch_id = report.pop("_upload_batch_id")
    print(f"解析实测：{report['parse_concurrency_2']['success']}/{report['files']} 份，"
          f"{report['parse_concurrency_2']['wall_seconds']}s，"
          f"{report['parse_concurrency_2']['throughput_per_min']} 份/分钟", flush=True)
    print(f"上传批次：{batch_id}；失败样本 {len(report['parse_failures'])} 份（不纳入批次）", flush=True)

    client = httpx.Client(base_url=API, timeout=120)
    TOKEN = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"}).json()["data"]["token"]

    scope = {"type": "upload_batch", "upload_batch_id": batch_id}

    # 1) 筛选批次（并发热切换 2 → 4）
    print("1/2 筛选批次（并发 2 → 4 热切换）…", flush=True)
    screen = api("POST", "/screen/batches", json={"job_profile_id": 1, "scope": scope})
    report["screen_estimated_llm_calls"] = screen["estimated_llm_calls"]
    api("PATCH", "/resumes/queue/settings", json={"concurrency": 4})
    screen_seconds, sd = poll_batch("screen", screen["batch_id"])
    report["screen_batch"] = {
        "batch_id": screen["batch_id"],
        "total": screen["total"],
        "processed": sd["processed"],
        "skipped_deleted": sd["skipped_deleted"],
        "passed": sd["passed"],
        "pending_count": sd["pending_count"],
        "rejected": sd["rejected"],
        "actual_llm_calls": sd["processed"] - hard_fail_count("screen", screen["batch_id"]),
        "vector_degraded": sd["vector_degraded"],
        "wall_seconds": round(screen_seconds, 1),
    }
    api("PATCH", "/resumes/queue/settings", json={"concurrency": 2})
    print(f"    筛选完成：{screen_seconds:.1f}s，processed={sd['processed']}", flush=True)

    # 2) 匹配批次（并发 2）
    print("2/2 匹配批次（并发 2）…", flush=True)
    match = api("POST", "/match/batches", json={"job_profile_id": 1, "scope": scope})
    match_seconds, md = poll_batch("match", match["batch_id"])
    report["match_batch"] = {
        "batch_id": match["batch_id"],
        "total": match["total"],
        "processed": md["processed"],
        "hard_failed": md["hard_failed"],
        "vector_degraded": md["vector_degraded"],
        "wall_seconds": round(match_seconds, 1),
    }
    print(f"    匹配完成：{match_seconds:.1f}s，processed={md['processed']}", flush=True)

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nT-31 报告 → {REPORT_PATH}", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:1200], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
