"""T-30 匹配评测：直连 `match_engine.evaluate(use_vector=True)`（另跑 use_vector=False 作参考口径）。

⚠️ **A02-1（2026-11-10）口径提示**：引擎已按 V2.6 §5 取消硬门槛自动淘汰，
`evaluate()` 不再返回 `hard_fail=True` 或 `score=NULL`。本脚本的 T-30 评测口径
（「硬淘汰即 rejected」、`production_verdict` 的 hard_fail 分支）仍按旧算法编写，
**在按 V2.6 重写并复跑真实模型前，不得用其产出作为 A02 验收依据**；对应
`docs/updates/A/20261110-1100-A02-1取消硬门槛自动淘汰.md`。

口径（算法 §7 / §4.0-§4.2 / 需求 §10）：
- 一致率 ≥85%（评测标注 passed/rejected 两档，硬淘汰即 rejected）；另按生产实现
  `screen_worker._verdict_for` 三档阈值复核（见 `production_verdict_recheck`）；
  子串陷阱样本（javascript/java、go/google、c/c++、sql/nosql、react/react-native）
  必须全部判定正确；
- 向量路径：画像文本（§6.3 简化版）真实 embedding → 写入 Milvus（合成主键 900000+i）→
  岗位向量每岗一次 → COSINE；评测结束删除合成向量；
- 输出明细 + 指标到 `data/eval/eval_match_report.json`；固定模型/prompt_version/temperature=0。

用法：`(cd backend && ./.venv/Scripts/python.exe tests/eval_match.py)`（真实 LLM+Embedding+Milvus，约 15-25 分钟）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 不要二次包装 sys.stdout.buffer：旧 wrapper 被 GC 时会关掉共享缓冲，
# 后续 print 报 "I/O operation on closed file"（perf_t31.py 曾踩过同一坑）。
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
EVAL_DIR = BASE_DIR / "data" / "eval"
REPORT_PATH = EVAL_DIR / "eval_match_report.json"

PASS_LINE = 0.85
SYNTHETIC_ID_BASE = 900_000


def profile_from_facts(facts: dict, profile_id: int) -> dict:
    """事实表 → 引擎入参 profile（技能按入库口径归一化，§5.1）。"""
    from app.core.normalize import normalize_skills

    return {
        "talent_profile_id": profile_id,
        "name": facts["name"],
        "degree": facts["degree"],
        "work_years": float(facts["work_years"]),
        "skills_json": normalize_skills(facts["skills"]),
        "vector_status": "synced",
        "tags_json": [],
        "experience_summary": "；".join(f"{c} {t}（{s}-{e}）{d}" for c, t, s, e, d in facts["jobs"]),
    }


def profile_text(profile: dict) -> str:
    """画像向量文本（§6.3 简化：标量 + 技能 + 经历摘要；与入库文本同源字段）。"""
    years = f"{profile['work_years']:g}"
    return "\n".join(
        [
            f"{profile['name']}　　{profile['degree']}　{years}年经验",
            f"技能:{','.join(profile['skills_json'])}",
            "标签:",
            "工作经历:" + (profile["experience_summary"] or ""),
            "项目:",
            "教育:",
        ]
    )


def verdict_of(result) -> str:
    if result.hard_fail:
        return "rejected"
    return "passed"  # 评测标注为 passed/rejected 两档（匹配无 pending 语义）


def production_verdict(score: float | None, hard_fail_reason: str | None) -> str:
    """生产口径复核：`screen_worker._verdict_for` 的三档分数阈值。

    评测主口径只用两档（硬淘汰即 rejected），为免把「passed vs pending」的标注
    边界差异误读成判定错误，另按生产实现重算一遍一致率作为补充证据。
    """
    if hard_fail_reason or score is None:
        return "rejected"
    if score >= 75:
        return "passed"
    if score >= 60:
        return "pending"
    return "rejected"


def main() -> int:
    from app.core import embedding_client, milvus_store
    from app.core.prompts.loader import PROMPT_VERSION
    from app.core.llm_client import get_llm_client
    from app.service import match_engine

    match_cases = json.loads((EVAL_DIR / "match_eval.json").read_text(encoding="utf-8"))
    facts_by_filename = {}
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from build_eval_dataset import FACT_SHEETS, JOBS

    for sheet in FACT_SHEETS:
        facts_by_filename[sheet["filename"]] = sheet["facts"]

    model = get_llm_client().model
    store = milvus_store.get_store()
    store.ensure_collection()

    # 1) 画像向量入库（合成主键；评测评测后删除）
    print("1/3 画像向量 embedding + upsert …")
    profile_ids: dict[str, int] = {}
    for index, sheet in enumerate(FACT_SHEETS):
        pid = SYNTHETIC_ID_BASE + index
        profile_ids[sheet["filename"]] = pid
        profile = profile_from_facts(sheet["facts"], pid)
        vector = embedding_client.embed_one(profile_text(profile))
        store.upsert(pid, vector, "eval")
    job_vectors = {
        title: embedding_client.embed_one(match_engine.build_job_vector_text(job))
        for title, job in JOBS.items()
    }

    # 2) 逐用例 evaluate（use_vector=True 主口径 + False 参考口径）
    print(f"2/3 逐用例评分（{len(match_cases)} 条 × 2 口径）…")
    details: list[dict] = []
    started = time.monotonic()
    for index, case in enumerate(match_cases, 1):
        filename, job_title = case["filename"], case["job_title"]
        facts = facts_by_filename[filename]
        job = JOBS[job_title]
        profile = profile_from_facts(facts, profile_ids[filename])
        row: dict = {"filename": filename, "job_title": job_title, "trap": case.get("trap"),
                     "expected_verdict": case["expected_verdict"],
                     "expect_reason_contains": case.get("expect_reason_contains") or []}
        try:
            with_vector = match_engine.evaluate(job, profile, use_vector=True, job_vector=job_vectors[job_title])
            without_vector = match_engine.evaluate(job, profile, use_vector=False)
            actual_with = verdict_of(with_vector)
            actual_without = verdict_of(without_vector)
            reasons_text = (with_vector.reason or "") + (without_vector.reason or "") + (with_vector.hard_fail_reason or "")
            row.update(
                status="ok",
                actual_verdict_with_vector=actual_with,
                actual_verdict_without_vector=actual_without,
                production_verdict_with_vector=production_verdict(with_vector.score, with_vector.hard_fail_reason),
                match=(actual_with == case["expected_verdict"]),
                score_with_vector=with_vector.score,
                score_without_vector=without_vector.score,
                vector_similarity=with_vector.vector_similarity,
                vector_degraded=with_vector.vector_degraded,
                hard_fail_reason=with_vector.hard_fail_reason,
                reason_contains_ok=all(
                    keyword in reasons_text for keyword in case.get("expect_reason_contains") or []
                ),
                reason=with_vector.reason,
            )
        except Exception as exc:  # noqa: BLE001
            row.update(status=f"error: {type(exc).__name__}: {exc}", match=False)
        details.append(row)
        flag = "✔" if row.get("match") else "✘"
        print(f"[{index}/{len(match_cases)}] {flag} {filename} × {job_title} → {row.get('actual_verdict_with_vector')} (期望 {case['expected_verdict']})")

    # 3) 指标
    def metrics(rows: list[dict], key: str) -> dict:
        total = len(rows)
        correct = sum(1 for r in rows if r.get(key))
        trap_rows = [r for r in rows if r.get("trap")]
        trap_correct = sum(1 for r in trap_rows if r.get(key))
        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "pass_line": PASS_LINE,
            "passed": bool(total and correct / total >= PASS_LINE),
            "trap_total": len(trap_rows),
            "trap_correct": trap_correct,
            "trap_all_correct": bool(trap_rows and trap_correct == len(trap_rows)),
        }

    with_v = metrics(details, "match")
    without_v = {
        "total": len(details),
        "correct": sum(1 for r in details if r.get("actual_verdict_without_vector") == r.get("expected_verdict")),
    }
    without_v["accuracy"] = round(without_v["correct"] / without_v["total"], 4)
    reason_ok = all(r.get("reason_contains_ok", True) for r in details)
    score_delta = [
        round(abs((r.get("score_with_vector") or 0) - (r.get("score_without_vector") or 0)), 2)
        for r in details
        if r.get("status") == "ok" and not _is_hard_fail(r)
    ]
    # 分数区间校验（算法 §7 ±10）：达标样本的匹配分不应低于 (pass_threshold - 10)
    score_range_violations = [
        {"filename": r["filename"], "job_title": r["job_title"], "score": r.get("score_with_vector")}
        for r in details
        if r.get("status") == "ok"
        and r.get("expected_verdict") == "passed"
        and (r.get("score_with_vector") is not None)
        and r.get("score_with_vector") < float(JOBS[r["job_title"]]["pass_threshold"]) - 10
    ]
    report = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0,
        "cases": len(match_cases),
        "duration_sec": round(time.monotonic() - started, 1),
        "with_vector": with_v,
        "without_vector_reference": without_v,
        "reason_keywords_all_ok": reason_ok,
        "production_verdict_recheck": _production_recheck(details),
        "max_score_delta_vector_vs_screen": max(score_delta) if score_delta else 0.0,
        "score_range_violations": score_range_violations,
        "score_range_rule": "expected passed 的用例 score ≥ pass_threshold - 10（±10 口径）",
        "notes": "画像向量用事实表直嵌（合成主键 900000+i，评测后删除）；一致率以 passed/rejected 两口径计；reason_keywords_all_ok 仅作参考项、不计入达标判定（硬淘汰理由按设计只给缺失项数量，标注期望的具体技能名文案与实现不一致）",
        "details": details,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理合成向量
    for pid in profile_ids.values():
        store.delete(pid)
    print(f"\n匹配评测：use_vector=True 一致率 {with_v['accuracy']}（达标线 {PASS_LINE}）→ {'PASS' if with_v['passed'] else 'FAIL'}；"
          f"参考口径（无向量）一致率 {without_v['accuracy']}；子串陷阱 {with_v['trap_correct']}/{with_v['trap_total']}；"
          f"理由关键词{'全部命中' if reason_ok else '有缺失'}；明细 → {REPORT_PATH}")
    return 0 if with_v["passed"] and with_v["trap_all_correct"] else 1


def _production_recheck(details: list[dict]) -> dict:
    """按生产三档阈值复核一致率（见 `production_verdict`）。"""
    rows = [r for r in details if r.get("status") == "ok"]
    strict = [r for r in rows if r.get("production_verdict_with_vector") == r["expected_verdict"]]
    strict_ids = {id(r) for r in strict}
    loose = [
        r for r in rows
        if (r["expected_verdict"] == "passed" and r.get("production_verdict_with_vector") in ("passed", "pending"))
        or (r["expected_verdict"] == "rejected" and r.get("production_verdict_with_vector") == "rejected")
    ]
    return {
        "rule": "screen_worker._verdict_for：hard_fail→rejected；score≥75 passed；≥60 pending；否则 rejected",
        "total": len(rows),
        "strict_accuracy": round(len(strict) / len(rows), 4) if rows else 0.0,
        "strict_passed": bool(rows and len(strict) / len(rows) >= PASS_LINE),
        "loose_accuracy": round(len(loose) / len(rows), 4) if rows else 0.0,
        "note": "标注只有 passed/rejected 两档，严格口径下 expected passed 但实际 pending 的用例计为不一致；宽松口径把 pending 归入 passed",
        "strict_mismatches": [
            {"filename": r["filename"], "job_title": r["job_title"],
             "expected": r["expected_verdict"], "score": r.get("score_with_vector"),
             "production": r.get("production_verdict_with_vector")}
            for r in rows if id(r) not in strict_ids
        ],
    }


def _is_hard_fail(row: dict) -> bool:
    return (row.get("score_with_vector") or 0) == 0


if __name__ == "__main__":
    sys.exit(main())
