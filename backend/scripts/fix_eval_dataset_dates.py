"""T-30 数据集修复：把评测简历正文里的「至今」固定为可算出标注年限的截止日。

根因（2026-11-06 定位）：
1. `build_eval_dataset.py` 的事实表里 `work_years` 是 ground truth，但 LLM 撰写正文时
   把「工作年限：X 年」这一行漏写了（`_verify` 没校验该字段），正文只剩日期区间；
2. 区间终点写成「至今」，是相对时间——标注按数据集生成时的历史时点算，
   评测日实算会大 1.1~2.9 年，导致 T-30 work_years 仅 0.682、总分 0.9181 未达线；
3. 生产链路的年限由规则层 `calc_work_years(employments)` 算（LLM 值仅兜底），
   `is_ongoing(end)` 按 `date.today()` 截止，同样会漂移。

修法：对每份简历，按「最早入职年月 + 标注年限」反推一个固定截止日，
把正文（含表格单元格）里所有「至今」替换为该日期。这样正文中所有区间
合并后的月数恰好等于标注年限，评测与生产口径都自洽，且标注 / match_eval 无需改动。

- 原件先备份到 `data/eval/resumes_original_20261106/`，不删任何原始文件；
- 同步修 `build_eval_dataset.py::_facts_text`，保证将来重建数据集同样自洽。

用法：`./.venv/Scripts/python.exe scripts/fix_eval_dataset_dates.py`
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
EVAL_DIR = BASE_DIR / "data" / "eval"
RESUME_DIR = EVAL_DIR / "resumes"
BACKUP_DIR = EVAL_DIR / "resumes_original_20261106"

sys.path.insert(0, str(BASE_DIR / "scripts"))
from build_eval_dataset import FACT_SHEETS, cutoff_for  # noqa: E402


def replace_in_docx(path: Path, cutoff: str) -> int:
    """遍历段落 + 表格所有 run，把「至今」换成截止日；返回替换次数。"""
    from docx import Document

    doc = Document(path)

    def replace_runs(paragraph) -> int:
        count = 0
        for run in paragraph.runs:
            if "至今" in run.text:
                count += run.text.count("至今")
                run.text = run.text.replace("至今", cutoff)
        # 文本可能被拆到多个 run：整段兜底（会丢失段内混合格式，评测集不关心）
        if count == 0 and "至今" in paragraph.text:
            full = paragraph.text
            count += full.count("至今")
            for run in paragraph.runs:
                run.text = ""
            if paragraph.runs:
                paragraph.runs[0].text = full.replace("至今", cutoff)
            else:
                paragraph.add_run(full.replace("至今", cutoff))
        return count

    total = 0
    for paragraph in doc.paragraphs:
        total += replace_runs(paragraph)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    total += replace_runs(paragraph)
    doc.save(path)
    return total


def main() -> int:
    assert len(list(RESUME_DIR.glob("*.docx"))) >= 20, "评测简历不足，先跑 build_eval_dataset.py"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"备份原件 → {BACKUP_DIR}")
    for src in sorted(RESUME_DIR.glob("*.docx")):
        dst = BACKUP_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)

    print("\n替换「至今」→ 固定截止日：")
    ok, warn = 0, []
    for sheet in FACT_SHEETS:
        path = RESUME_DIR / sheet["filename"]
        if not path.exists():
            warn.append(f"缺失 {sheet['filename']}")
            continue
        cutoff = cutoff_for(sheet["facts"])
        n = replace_in_docx(path, cutoff)
        flag = "" if n else "  ⚠ 未找到「至今」"
        if n == 0:
            warn.append(f"{sheet['filename']} 无「至今」")
        else:
            ok += 1
        print(f"  {sheet['filename'][:40]:42s} 标注={sheet['facts']['work_years']:<5} 截止={cutoff}  替换 {n} 处{flag}")

    print(f"\n完成：{ok}/{len(FACT_SHEETS)} 份已固定截止日；备份 {len(list(BACKUP_DIR.glob('*.docx')))} 份")
    if warn:
        print("提示：" + "；".join(warn))
    return 0 if not warn else 1


if __name__ == "__main__":
    sys.exit(main())
