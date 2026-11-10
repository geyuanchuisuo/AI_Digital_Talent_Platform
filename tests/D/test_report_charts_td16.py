"""PRD D16 图形进同快照 DOCX/PDF 用例（确定性渲染 + 真实嵌入）。

验收对照（执行表 D16）：
- 真实图形渲染与导出：bar/number 规格 → PNG（Pillow 自绘，零新增依赖），
  DOCX/PDF 导出物内嵌真实图形（非仅"图表数据"表）；
- 同快照数值/图表一致：渲染器纯函数、同输入字节级同输出；图形与"图表数据"表
  同源于同一 result_json.chart（构造保证，另做抽点核对）；
- 中文：CJK 字体探测（Windows 微软雅黑），缺失环境如实 skip 而非谎报；
- 长表/截断：>20 项规格截断渲染并标注；负值基线。

运行：pytest tests/D/test_report_charts_td16.py -v（纯函数，无外部依赖）。
"""

from __future__ import annotations

import io
import zipfile

import pytest

import app.models  # noqa: F401 先行完成全量模型注册（reports.service → app.models 链，避免半初始化环）
from app.modules.analytics.charts import build_chart
from app.modules.reports import chart_render

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _gap_table(rows_n: int = 6) -> dict:
    rows = [{"position_name": f"岗位{i}", "gap": i + 1} for i in range(rows_n)]
    return {"columns": ["position_name", "gap"], "rows": rows}


def _bar_chart(rows_n: int = 6):
    return build_chart(
        "job.demand_progress_gap", "需求缺口", None, _gap_table(rows_n), None,
    )


def _result(chart: dict | None) -> dict:
    return {
        "value": 12,
        "table": _gap_table(),
        "chart": chart or {"chart_type": "none", "reason": "暂无数据"},
        "interpretation": {"segments": [{"kind": "fact", "key": "k", "text": "缺口合计 12"}]},
    }


def _docx_bytes(result: dict) -> bytes:
    from app.modules.reports.service import _build_docx

    return _build_docx(1, "job.demand_progress_gap", result, {"department": "研发部"},
                       None, "test")


def _pdf_bytes(result: dict) -> bytes:
    from app.modules.reports.service import _build_pdf

    return _build_pdf(1, "job.demand_progress_gap", result, {"department": "研发部"},
                      None, "test")


def _docx_media(docx: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return [n for n in z.namelist() if n.startswith("word/media/")]


def _pdf_image_count(pdf: bytes) -> int:
    try:
        import pymupdf
    except ImportError:  # 兼容旧包名
        import fitz as pymupdf  # type: ignore[no-redef]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return sum(len(page.get_images()) for page in doc)


# ---- 渲染器 ----

def test_render_bar_deterministic_and_png_magic():
    chart = _bar_chart()
    a = chart_render.render_chart_png(chart, title="需求缺口")
    b = chart_render.render_chart_png(chart, title="需求缺口")
    assert a is not None and a.startswith(PNG_MAGIC)
    assert a == b, "同输入必须字节级同输出（同快照可复现）"


def test_render_bar_cjk_font_available():
    if chart_render._font_path() is None:
        pytest.skip("本机未找到 CJK 候选字体（边界：部署环境须保证其一存在）")
    # 中文类目名渲染不崩溃且出图
    chart = _bar_chart()
    png = chart_render.render_chart_png(chart, title="需求缺口")
    assert png is not None and len(png) > 1000


def test_render_number_card():
    card = {"chart_type": "number", "label": "在招岗位", "value": 12}
    png = chart_render.render_chart_png(card, title="jobs.active")
    assert png is not None and png.startswith(PNG_MAGIC)


def test_render_none_and_unknown_return_none():
    assert chart_render.render_chart_png({"chart_type": "none", "reason": "暂无数据"}) is None
    assert chart_render.render_chart_png({"chart_type": "pie"}) is None
    assert chart_render.render_chart_png(None) is None


def test_render_caps_long_series():
    chart = _bar_chart(30)  # build_chart 已截到 20；直喂 30 项规格再验渲染上限
    chart["data"] = [{"name": f"岗位{i}", "value": i + 1} for i in range(30)]
    chart["truncated_series"] = True
    png = chart_render.render_chart_png(chart, title="t")
    assert png is not None and png.startswith(PNG_MAGIC)


def test_render_handles_negative_values():
    chart = {"chart_type": "bar", "x_field": "name", "y_field": "value",
             "data": [{"name": "甲", "value": 5}, {"name": "乙", "value": -3},
                      {"name": "丙", "value": 0}],
             "truncated_series": False}
    png = chart_render.render_chart_png(chart, title="t")
    assert png is not None and png.startswith(PNG_MAGIC)


# ---- DOCX / PDF 真实嵌入 ----

def test_docx_embeds_bar_png_with_data_table():
    docx = _docx_bytes(_result(_bar_chart()))
    media = _docx_media(docx)
    assert media and any(n.endswith(".png") for n in media), "DOCX 必须内嵌图表 PNG"
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "图表" in xml and "图表数据" in xml, "图形与数值附录并存"
    # 同快照一致性抽点：图表数据表首行的值来自同一 chart.data
    first = _bar_chart()["data"][0]
    assert first["name"] in xml and str(first["value"]) in xml


def test_docx_embeds_number_card():
    card = {"chart_type": "number", "label": "在招岗位", "value": 12}
    docx = _docx_bytes(_result(card))
    assert _docx_media(docx), "数值卡同样以图形嵌入"


def test_pdf_embeds_chart_image():
    pdf = _pdf_bytes(_result(_bar_chart()))
    assert _pdf_image_count(pdf) >= 1, "PDF 必须内嵌图表图像"
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore[no-redef]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "图表" in text and "图表数据" in text and "解读" in text


def test_no_chart_no_media():
    docx = _docx_bytes(_result(None))
    assert _docx_media(docx) == [], "none 型不嵌图（与 T-D11 行为一致）"
    assert _pdf_image_count(_pdf_bytes(_result(None))) == 0


def test_docx_content_deterministic():
    """整包含 core.xml 时间戳不字节确定；报告正文 document.xml 必须确定。"""
    with zipfile.ZipFile(io.BytesIO(_docx_bytes(_result(_bar_chart())))) as a, \
            zipfile.ZipFile(io.BytesIO(_docx_bytes(_result(_bar_chart())))) as b:
        assert a.read("word/document.xml") == b.read("word/document.xml")
