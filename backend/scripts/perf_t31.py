"""T-31 性能与并发自测：真实 pipeline 批量吞吐（上传 22 份评测简历 → 解析 → 筛选 → 匹配）。

测量口径：
1. 解析吞吐：22 份 DOCX 一次批量上传 → 轮询队列全 success 的墙钟时间（并发 2 → 4 热切换对比）；
2. 批次吞吐：screen / match 批次（scope=upload_batch）从创建到 done 的墙钟时间、
   `estimated_llm_calls` vs 实际（结果行数 - 硬淘汰数）；
3. 过程数据全部落 JSON 报告 `data/eval/perf_t31_report.json`，结束后**清理本次新增的测试数据**
   （DB / Milvus / 上传文件），**保留运行前已存在的画像**（如 T-15 真实画像 562–566，2026-11-06 用户决策保留）。

前置：服务已启动（8000）且评测集已生成（scripts/build_eval_dataset.py）。
用法：`./.venv/Scripts/python.exe scripts/perf_t31.py`
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:  # 以 scripts/xxx.py 直接运行时，保证可 import app
    sys.path.insert(0, str(BASE_DIR))
EVAL_DIR = BASE_DIR / "data" / "eval"
REPORT_PATH = EVAL_DIR / "perf_t31_report.json"
API = "http://127.0.0.1:8000"
RESUME_DIR = EVAL_DIR / "resumes"

client = None
TOKEN = None

# 运行前快照：cleanup 只删本次新增，不碰快照里已有的数据（用户决策：保留 T-15 真实画像）
PRE_PROFILE_IDS: set[int] = set()
PRE_FILE_IDS: set[int] = set()
PRE_SCREEN_BATCH_IDS: set[int] = set()
PRE_MATCH_BATCH_IDS: set[int] = set()


def snapshot_pre_existing_state() -> None:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import MatchBatch, ResumeFile, ScreenBatch, TalentProfile

    with SessionLocal() as db:
        PRE_PROFILE_IDS.update(db.execute(select(TalentProfile.id)).scalars())
        PRE_FILE_IDS.update(db.execute(select(ResumeFile.id)).scalars())
        PRE_SCREEN_BATCH_IDS.update(db.execute(select(ScreenBatch.id)).scalars())
        PRE_MATCH_BATCH_IDS.update(db.execute(select(MatchBatch.id)).scalars())
    print(f"快照：已有画像 {len(PRE_PROFILE_IDS)} 个（{'保留' if PRE_PROFILE_IDS else '无'}）、"
          f"文件 {len(PRE_FILE_IDS)}、screen 批次 {len(PRE_SCREEN_BATCH_IDS)}、match 批次 {len(PRE_MATCH_BATCH_IDS)}")


def api(method: str, path: str, **kwargs):
    response = client.request(method, f"/api{path}", headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {response.status_code}: {response.text[:200]}")
    return response.json()["data"]


def poll_queue_until_all_done(timeout_sec: float, max_auto_retry: int = 2) -> tuple[float, int, list[dict]]:
    """轮询解析队列；LLM 偶发残缺 JSON 自动 retry，最终仍失败的样本只记录不中止整轮测试。

    返回 (墙钟秒, success 数, 最终失败条目列表)。
    """
    started = time.monotonic()
    retries: dict[str, int] = {}          # filename → 已自动 retry 次数
    while time.monotonic() - started < timeout_sec:
        data = api("GET", "/resumes/queue", params={"page": 1, "page_size": 50})
        items = [item for item in data["items"] if item["filename"].startswith("eval-")]
        done = sum(1 for item in items if item["status"] == "success")
        failed = [item for item in items if item["status"] == "failed"]
        print(f"    队列 success {done}/{len(items)}", end="\r")
        # 先算「已用尽重试次数」的最终失败项，再对本轮新失败项发起 retry（顺序不能反，
        # 否则刚 retry 的项会在同一轮被误判为「重试后仍失败」而提前抛错）。
        final_failed = [i for i in failed if retries.get(i["filename"], 0) >= max_auto_retry]
        for item in failed:
            name = item["filename"]
            if retries.get(name, 0) < max_auto_retry:
                retries[name] = retries.get(name, 0) + 1
                print(f"\n    自动 retry {retries[name]}/{max_auto_retry}：{name}（stage={item.get('stage')}）")
                try:
                    api("POST", f"/resumes/queue/{item['task_id']}/retry")
                except RuntimeError as exc:
                    print(f"    retry 接口异常：{exc}")
        if len(items) and done + len(final_failed) == len(items):
            return time.monotonic() - started, done, final_failed
        time.sleep(10)
    raise TimeoutError("解析超时")


def poll_batch(kind: str, batch_id: int, timeout_sec: float) -> tuple[float, dict]:
    started = time.monotonic()
    retried = False
    while time.monotonic() - started < timeout_sec:
        data = api("GET", f"/{kind}/batches/{batch_id}")
        print(f"    {kind}#{batch_id} {data['status']} {data['progress']}/{data['total']}", end="\r")
        if data["status"] in ("done", "failed"):
            if data["status"] == "failed":
                if not retried:
                    retried = True
                    print(f"\n    {kind}#{batch_id} failed，自动 retry 一次：{(data.get('error') or '')[:120]}")
                    api("POST", f"/{kind}/batches/{batch_id}/retry")
                    time.sleep(10)
                    continue
                raise RuntimeError(f"批次失败（自动 retry 后仍失败）：{data['error']}")
            return time.monotonic() - started, data
        time.sleep(10)
    raise TimeoutError(f"{kind} 批次超时")


def main() -> int:
    global client, TOKEN
    import httpx

    client = httpx.Client(base_url=API, timeout=120)
    login = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"}).json()
    TOKEN = login["data"]["token"]

    snapshot_pre_existing_state()

    files = sorted(RESUME_DIR.glob("*.docx"))
    assert len(files) >= 20, f"评测简历不足：{len(files)}（先跑 scripts/build_eval_dataset.py）"
    report: dict = {"files": len(files)}

    # 1) 上传（一次批量 → 单一 upload_batch_id）
    print(f"1/4 批量上传 {len(files)} 份 …")
    upload_started = time.monotonic()
    response = client.post(
        "/api/resumes/batch",
        headers={"Authorization": f"Bearer {TOKEN}"},
        files=[("files", (f.name, f.read_bytes())) for f in files],
    )
    upload_data = response.json()["data"]
    upload_batch_id = upload_data["upload_batch_id"]
    report["upload"] = {
        "accepted": upload_data["accepted"],
        "duplicates": len(upload_data.get("duplicates") or []),
        "upload_batch_id": upload_batch_id,
        "elapsed": round(time.monotonic() - upload_started, 1),
    }

    # 2) 解析吞吐（并发 2）
    print("2/4 解析（TaskRunner 并发 2）…")
    parse_seconds, parsed, parse_failures = poll_queue_until_all_done(timeout_sec=60 * 60)
    report["parse_concurrency_2"] = {
        "total": parsed + len(parse_failures),
        "success": parsed,
        "wall_seconds": round(parse_seconds, 1),
        "throughput_per_min": round(parsed / (parse_seconds / 60), 2),
    }
    if parse_failures:
        report["parse_failures"] = [
            {"filename": i["filename"], "error": (i.get("error") or "")[:200]} for i in parse_failures
        ]
        print(f"\n    注意：{len(parse_failures)} 份自动重试后仍失败，已记入报告，后续批次自然跳过")
    print(f"\n    解析吞吐：{report['parse_concurrency_2']['throughput_per_min']} 份/分钟（并发 2）")

    # 3) 筛选批次（并发热切换 2→4）
    print("3/4 筛选批次（并发 2 → 中途热切换 4）…")
    screen = api("POST", "/screen/batches", json={"job_profile_id": 1, "scope": {"type": "upload_batch", "upload_batch_id": upload_batch_id}})
    report["screen_estimated_llm_calls"] = screen["estimated_llm_calls"]
    # 并发热切换（D-C：下一个扫描周期生效）
    api("PATCH", "/resumes/queue/settings", json={"concurrency": 4})
    screen_seconds, screen_detail = poll_batch("screen", screen["batch_id"], timeout_sec=60 * 60)
    actual_llm = screen_detail["processed"] - _hard_fail_count("screen", screen["batch_id"])
    report["screen_batch"] = {
        "batch_id": screen["batch_id"],
        "total": screen["total"],
        "processed": screen_detail["processed"],
        "skipped_deleted": screen_detail["skipped_deleted"],
        "passed": screen_detail["passed"],
        "pending_count": screen_detail["pending_count"],
        "rejected": screen_detail["rejected"],
        "actual_llm_calls": actual_llm,
        "vector_degraded": screen_detail["vector_degraded"],
        "wall_seconds": round(screen_seconds, 1),
    }
    # 并发切回 2
    api("PATCH", "/resumes/queue/settings", json={"concurrency": 2})

    # 4) 匹配批次（并发 2）
    print("4/4 匹配批次（并发 2）…")
    match = api("POST", "/match/batches", json={"job_profile_id": 1, "scope": {"type": "upload_batch", "upload_batch_id": upload_batch_id}})
    match_seconds, match_detail = poll_batch("match", match["batch_id"], timeout_sec=60 * 60)
    report["match_batch"] = {
        "batch_id": match["batch_id"],
        "total": match["total"],
        "processed": match_detail["processed"],
        "hard_failed": match_detail["hard_failed"],
        "vector_degraded": match_detail["vector_degraded"],
        "wall_seconds": round(match_seconds, 1),
    }

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nT-31 报告 → {REPORT_PATH}")
    print(json.dumps({k: v for k, v in report.items() if k != "upload"}, ensure_ascii=False, indent=2)[:800])
    return 0


def _hard_fail_count(kind: str, batch_id: int) -> int:
    data = api("GET", f"/{kind}/batches/{batch_id}/results", params={"page": 1, "page_size": 200})
    return sum(1 for item in data["items"] if item["hard_fail"])


def cleanup() -> None:
    """清理 T-31 本次新增的测试数据；运行前快照里已有的画像/文件/批次一律保留。"""
    from sqlalchemy import delete as sa_delete, select, text

    from app.core.milvus_store import get_store
    from app.database import SessionLocal
    from app.models import (
        MatchBatch, MatchResult, ParsedResume, ParseTask, ResumeFile,
        ScreenBatch, ScreenResult, TalentDuplicate, TalentProfile, TalentTag,
    )

    with SessionLocal() as db:
        file_ids = [fid for fid in db.execute(select(ResumeFile.id)).scalars() if fid not in PRE_FILE_IDS]
        stored_paths = (
            db.execute(select(ResumeFile.stored_path).where(ResumeFile.id.in_(file_ids))).scalars().all()
            if file_ids else []
        )
        screen_ids = [bid for bid in db.execute(select(ScreenBatch.id)).scalars() if bid not in PRE_SCREEN_BATCH_IDS]
        match_ids = [bid for bid in db.execute(select(MatchBatch.id)).scalars() if bid not in PRE_MATCH_BATCH_IDS]
        profile_ids = [pid for pid in db.execute(select(TalentProfile.id)).scalars() if pid not in PRE_PROFILE_IDS]

        if screen_ids:
            db.execute(sa_delete(ScreenResult).where(ScreenResult.screen_batch_id.in_(screen_ids)))
            db.execute(sa_delete(ScreenBatch).where(ScreenBatch.id.in_(screen_ids)))
        if match_ids:
            db.execute(sa_delete(MatchResult).where(MatchResult.match_batch_id.in_(match_ids)))
            db.execute(sa_delete(MatchBatch).where(MatchBatch.id.in_(match_ids)))
        if file_ids:
            db.execute(sa_delete(ParseTask).where(ParseTask.resume_file_id.in_(file_ids)))
            db.execute(sa_delete(ParsedResume).where(ParsedResume.resume_file_id.in_(file_ids)))
            db.execute(sa_delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
        if profile_ids:
            db.execute(sa_delete(TalentTag).where(TalentTag.talent_profile_id.in_(profile_ids)))
            db.execute(sa_delete(TalentDuplicate).where(
                TalentDuplicate.profile_a_id.in_(profile_ids)
                | TalentDuplicate.profile_b_id.in_(profile_ids)
            ))
            db.execute(sa_delete(TalentProfile).where(TalentProfile.id.in_(profile_ids)))
        db.commit()
        remaining = {
            "profiles": db.execute(text("SELECT COUNT(*) FROM talent_profiles")).scalar(),
            "files": db.execute(text("SELECT COUNT(*) FROM resume_files")).scalar(),
        }
    for pid in profile_ids:
        get_store().delete(pid)
    for path in stored_paths:
        Path(path).unlink(missing_ok=True)
    print(f"cleanup：新增画像删除 {len(profile_ids)} 个（保留原有 {len(PRE_PROFILE_IDS)} 个：{sorted(PRE_PROFILE_IDS)}）；"
          f"新增文件删除 {len(file_ids)} 个（磁盘清理 {len(stored_paths)} 个）；批次删除 {len(screen_ids)+len(match_ids)} 个；"
          f"Milvus 向量删除 {len(profile_ids)} 个；剩余 profiles={remaining['profiles']} files={remaining['files']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "cleanup":
        # cleanup 是独立进程、无内存快照；--keep 显式声明必须保留的画像 ID（防误删真实画像）
        if "--keep" in args:
            keep = args[args.index("--keep") + 1]
            PRE_PROFILE_IDS.update(int(x) for x in keep.split(",") if x.strip())
            print(f"cleanup --keep：保留画像 {sorted(PRE_PROFILE_IDS)}")
        cleanup()
    else:
        sys.exit(main())
