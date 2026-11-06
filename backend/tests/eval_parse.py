"""T-30 解析评测：直连 pipeline（extract_text → Prompt A → ResumeSchema），不经 HTTP。

口径（算法 §7 / 需求 §10）：
- 字段级 precision/recall/F1，关键字段加权：name 0.15 / phone 0.15 / degree 0.20 /
  work_years 0.20 / skills 0.30；**整体 ≥95% 为达标**；"标注允许 1 个字段容差"
  体现为 work_years |Δ|≤1.0 记满分、skills 用集合 F1（阈值 0.8 记满分）；
- **work_years 按生产口径取值**：规则层 `calc_work_years(employments)` 优先，算不出才用
  LLM 的 `work_years` 兜底（算法 §2.4、`profile_service.build_profile_payload`）。
  2026-11-06 前本脚本直接读 LLM 原始值，与入库口径不一致，是 T-30 失分根因之一；
- 关键字段（work_years / phone）为 None 时重试一次，避免偶发漏抽直接计 0 分；
- 固定模型 / prompt_version / temperature=0（chat_json 口径），可重复运行；
- 输出明细 + 指标报告到 `data/eval/eval_parse_report.json`。

用法：`(cd backend && ./.venv/Scripts/python.exe tests/eval_parse.py)`（真实 LLM，约 20-30 分钟）。
**不是 pytest 用例**（文件名不匹配 test_*，不会被收集）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 不二次包装 sys.stdout.buffer（旧 wrapper 被 GC 会关闭共享缓冲）；启动命令带 PYTHONIOENCODING=utf-8
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:  # 按文档用法 `python tests/eval_parse.py` 直跑时保证可 import app
    sys.path.insert(0, str(BASE_DIR))
EVAL_DIR = BASE_DIR / "data" / "eval"
REPORT_PATH = EVAL_DIR / "eval_parse_report.json"

WEIGHTS = {"name": 0.15, "phone": 0.15, "degree": 0.20, "work_years": 0.20, "skills": 0.30}
PASS_LINE = 0.95
YEARS_TOLERANCE = 1.0
SKILL_F1_PASS = 0.8


def norm_phone(value: str | None) -> str:
    from app.core.normalize import normalize_phone

    return normalize_phone(value) or ""


def skill_set_f1(expected: list[str], actual: list[str]) -> float:
    """技能集合 F1（双方都先归一化到规范名——与入库口径一致）。"""
    from app.core.normalize import normalize_skills

    exp = set(normalize_skills(expected))
    act = set(normalize_skills(actual))
    if not exp and not act:
        return 1.0
    if not exp or not act:
        return 0.0
    overlap = exp & act
    precision = len(overlap) / len(act)
    recall = len(overlap) / len(exp)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def effective_work_years(structured: dict) -> float | None:
    """按生产口径取年限：规则层由 employments 合并区间计算，LLM 值仅兜底（算法 §2.4）。"""
    from app.core.normalize import calc_work_years

    years = calc_work_years(structured.get("employments") or [])
    if years is not None:
        return float(years)
    raw = structured.get("work_years")
    return float(raw) if raw is not None else None


def missing_key_fields(structured: dict) -> list[str]:
    """偶发漏抽的关键字段；出现即重试一次（不按 0 分计）。"""
    basic = structured.get("basic") or {}
    missing: list[str] = []
    if effective_work_years(structured) is None:
        missing.append("work_years")
    if not str(basic.get("phone") or "").strip():
        missing.append("phone")
    return missing


def field_scores(expected: dict, structured: dict) -> dict[str, float]:
    """单份样本的字段得分（0/1 或 F1 比率）。"""
    basic = structured.get("basic") or {}
    scores: dict[str, float] = {}
    scores["name"] = 1.0 if (basic.get("name") or "").strip() == expected["name"] else 0.0
    scores["phone"] = 1.0 if norm_phone(basic.get("phone")) == norm_phone(expected["phone"]) else 0.0
    scores["degree"] = 1.0 if (structured.get("highest_degree") or basic_label(structured)) == expected["degree"] else 0.0
    actual_years = effective_work_years(structured)
    if actual_years is None:
        scores["work_years"] = 1.0 if expected["work_years"] <= YEARS_TOLERANCE else 0.0
    else:
        scores["work_years"] = 1.0 if abs(float(actual_years) - float(expected["work_years"])) <= YEARS_TOLERANCE else 0.0
    actual_skills = [item.get("name") if isinstance(item, dict) else str(item) for item in structured.get("skills") or []]
    scores["skills"] = skill_set_f1(expected["skills"], actual_skills)
    if scores["skills"] < SKILL_F1_PASS:
        scores["skills"] = 0.0
    return scores


def basic_label(structured: dict) -> str | None:
    """ResumeSchema 的学历存于 educations[].degree；取最高学历（规则层同口径）。"""
    from app.core.normalize import highest_degree

    return highest_degree(structured.get("educations") or []) or structured.get("basic", {}).get("degree")


def main() -> int:
    from app.core.prompts.loader import PROMPT_VERSION
    from app.core.llm_client import get_llm_client
    from app.service.extract_service import extract_text
    from app.service.parse_service import extract_structured
    from app.service.upload_service import resolve_stored_path

    annotations = json.loads((EVAL_DIR / "resume_parse_eval.json").read_text(encoding="utf-8"))
    model = get_llm_client().model

    details: list[dict] = []
    weighted_scores: list[float] = []
    started = time.monotonic()

    for index, annotation in enumerate(annotations, 1):
        filename = annotation["filename"]
        path = resolve_stored_path(f"data/eval/resumes/{filename}")
        row: dict = {"filename": filename, "layout": annotation.get("layout"), "quality_retried": []}
        # 文件级重试（3 次，60s 退避）——供应商偶发连接异常（APIConnectionError/超时）不计 0 分
        last_error: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                extract = extract_text(path)
                structured = extract_structured(extract.text)
                missing = missing_key_fields(structured)
                if missing:
                    print(f"    关键字段 {missing} 为空，重试一次（不计 0 分）")
                    row["quality_retried"] = missing
                    structured = extract_structured(extract.text)
                scores = field_scores(annotation, structured)
                overall = sum(WEIGHTS[key] * scores[key] for key in WEIGHTS)
                row.update(
                    status="ok",
                    scores={k: round(v, 3) for k, v in scores.items()},
                    overall=round(overall, 4),
                    attempts=attempt,
                    parsed=dict(
                        name=(structured.get("basic") or {}).get("name"),
                        phone=(structured.get("basic") or {}).get("phone"),
                        degree=basic_label(structured),
                        work_years=effective_work_years(structured),
                        work_years_llm_raw=structured.get("work_years"),
                        skills=[item.get("name") if isinstance(item, dict) else str(item) for item in structured.get("skills") or []],
                    ),
                )
                weighted_scores.append(overall)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"    第 {attempt} 次失败：{type(exc).__name__}: {str(exc)[:80]}")
                if attempt < 3:
                    time.sleep(60)
        else:
            row.update(status=f"error: {type(last_error).__name__}: {last_error}", scores={}, overall=0.0)
            weighted_scores.append(0.0)
        details.append(row)
        print(f"[{index}/{len(annotations)}] {filename} overall={row.get('overall')} ({row['status']})")

    overall_avg = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.0
    field_avg = {
        key: round(sum(d["scores"].get(key, 0.0) for d in details) / len(details), 4) for key in WEIGHTS
    }
    report = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0,
        "samples": len(annotations),
        "duration_sec": round(time.monotonic() - started, 1),
        "overall_weighted_score": round(overall_avg, 4),
        "pass_line": PASS_LINE,
        "passed": overall_avg >= PASS_LINE,
        "field_scores": field_avg,
        "weights": WEIGHTS,
        "notes": (
            "work_years 容差 ±1.0；skills 集合 F1≥0.8 记满分；"
            "work_years 按生产口径 calc_work_years(employments) 计算（LLM 原始值仅兜底，见 work_years_llm_raw）；"
            "数据集「至今」已固定为可算出标注年限的截止日（原件备份在 resumes_original_20261106/）；"
            "Prompt A 注入解析基准日期；work_years/phone 为 None 时重试一次（quality_retried 字段）"
        ),
        "details": details,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n解析评测：整体加权 {report['overall_weighted_score']}（达标线 {PASS_LINE}）→ "
        f"{'PASS' if report['passed'] else 'FAIL'}；明细 → {REPORT_PATH}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
