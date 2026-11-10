"""报告导出服务（T-D11）——同快照确定性构建，导出前重新校验权限 + 审计落库。

同快照保证（验收②）：构建输入**只有**快照 result_json（value/table/interpretation/
chart）与元数据——不触达任何统计函数、不重新问数（测试用哨兵断言统计函数未被调用）。

中文：DOCX 设东亚字体（微软雅黑）；PDF 用 reportlab 内置 CID 字体 STSong-Light
（无需外部字体文件）。长表分页：PDF 走 platypus Table（repeatRows 表头逐页重复）；
DOCX 由 Word 自身分页。空数据/截断/解读缺失：如实写入「（暂无数据）/（数据已截断）/
（无解读）」。
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.auth.scope import account_is_management
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.reports.models import SnapshotExportAudit

RESULT_NOT_FOUND = "记录不存在"
_EXPORT_FORBIDDEN = "仅结果创建者或管理侧可导出"
_FORMAT_NOT_SUPPORTED = "仅支持 docx / pdf 格式"


def _snapshot_or_404(db: Session, snapshot_id: int):
    from app.modules.analytics.models import AnalyticsQuerySnapshot

    return db.get(AnalyticsQuerySnapshot, snapshot_id)


def _check_export_permission(user, snap) -> None:
    """导出前重新校验权限：管理侧（A06 角色口径 system_admin/hr）或快照创建者。

    替代原 `can_view_all_persons` 判断——使 dept_manager/trainer/employee/
    candidate/personal 即使误带管理侧数据范围也无法导出他人快照；system_admin
    经 sys_user_roles 绑定后也能正确放行（修复原 users.role 映射误拒）。
    """
    with SessionLocal() as db:
        if account_is_management(db, user):
            return
    if snap.created_by == getattr(user, "id", None):
        return
    raise BizError(403, _EXPORT_FORBIDDEN)


async def export_snapshot(user, snapshot_id: int, fmt: str) -> tuple[bytes, str, str]:
    """导出快照报告（async 门面；重活放线程池）。返回 (bytes, filename, media_type)。"""
    return await asyncio.to_thread(
        _export_snapshot_sync, user, snapshot_id, fmt
    )


def _export_snapshot_sync(user, snapshot_id: int, fmt: str) -> tuple[bytes, str, str]:
    """导出快照报告。返回 (bytes, filename, media_type)；每一步审计落库。"""
    fmt = (fmt or "").lower()
    if fmt not in ("docx", "pdf"):
        raise BizError(422, _FORMAT_NOT_SUPPORTED)

    with SessionLocal() as db:
        snap = _snapshot_or_404(db, snapshot_id)
        if snap is None:
            raise BizError(404, RESULT_NOT_FOUND)
        _check_export_permission(user, snap)
        result = dict(snap.result_json or {})
        params = dict(snap.params_json or {})
        data_time = snap.data_time
        source = snap.source

    if fmt == "docx":
        payload = _build_docx(snap.id, snap.metric_id, result, params, data_time, source)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        payload = _build_pdf(snap.id, snap.metric_id, result, params, data_time, source)
        media = "application/pdf"

    _audit(user, snap.id, fmt)
    filename = f"snapshot-{snapshot_id}-{snap.metric_id.replace('.', '_')}.{fmt}"
    return payload, filename, media


def _audit(user, snapshot_id: int, fmt: str) -> None:
    with SessionLocal() as db:
        db.add(SnapshotExportAudit(
            snapshot_id=snapshot_id, format=fmt, created_by=getattr(user, "id", None),
        ))
        db.commit()


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


# --------------------------------------------------------------------------- #
# DOCX（python-docx；东亚字体显式设置，Word 自身长表分页）
# --------------------------------------------------------------------------- #

def _docx_document():
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "微软雅黑")
    return doc


def _docx_table(doc, columns: list[str], rows: list[dict]) -> None:
    from docx.shared import Pt

    t = doc.add_table(rows=1, cols=len(columns))
    t.style = "Table Grid"
    for i, col in enumerate(columns):
        t.rows[0].cells[i].text = str(col)
    for row in rows:
        cells = t.add_row().cells
        for i, col in enumerate(columns):
            cells[i].text = str(row.get(col, ""))
    doc.add_paragraph()


def _build_docx(
    snapshot_id: int, metric_id: str, result: dict, params: dict,
    data_time, source: str,
) -> bytes:
    doc = _docx_document()
    doc.add_heading(f"问数报告：{metric_id}", level=1)
    doc.add_paragraph(f"数据时间：{_fmt_dt(data_time)}｜数据来源：{source}｜快照 #{snapshot_id}")
    if params:
        doc.add_paragraph(f"参数：{params}")

    if result.get("note"):
        doc.add_paragraph(f"说明：{result['note']}")

    value = result.get("value")
    doc.add_paragraph().add_run(f"指标值：{value}").bold = True

    table = result.get("table") or {}
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    if columns and rows:
        doc.add_heading("数据明细", level=2)
        _docx_table(doc, columns, rows)
    else:
        doc.add_paragraph("（无数据行）")

    chart = result.get("chart") or {}
    if chart.get("data"):
        doc.add_heading("图表数据", level=2)
        chart_rows = [{"name": p["name"], "value": p["value"]} for p in chart["data"]]
        _docx_table(doc, ["name", "value"], chart_rows)
        if chart.get("truncated_series"):
            doc.add_paragraph("（图表数据已截断）")

    interp = result.get("interpretation") or {}
    segments = interp.get("segments") or []
    doc.add_heading("解读", level=2)
    if segments:
        for seg in segments:
            kind = "事实" if seg.get("kind") == "fact" else "推断"
            p = doc.add_paragraph()
            p.add_run(f"[{kind}] ").bold = True
            p.add_run(str(seg.get("text", "")))
    else:
        doc.add_paragraph("（无解读）")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# PDF（reportlab 内置 CID 中文字体 + platypus 长表自动分页）
# --------------------------------------------------------------------------- #

def _build_pdf(
    snapshot_id: int, metric_id: str, result: dict, params: dict,
    data_time, source: str,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    body = ParagraphStyle("body", fontName="STSong-Light", fontSize=10, leading=15)
    heading = ParagraphStyle("heading", fontName="STSong-Light", fontSize=14,
                             leading=20, spaceBefore=10, spaceAfter=6)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"问数报告：{metric_id}")
    story: list = []

    story.append(Paragraph(f"问数报告：{metric_id}", heading))
    story.append(Paragraph(
        f"数据时间：{_fmt_dt(data_time)}｜数据来源：{source}｜快照 #{snapshot_id}", body))
    if params:
        story.append(Paragraph(f"参数：{params}", body))
    story.append(Spacer(1, 6))

    if result.get("note"):
        story.append(Paragraph(f"说明：{result['note']}", body))

    value = result.get("value")
    story.append(Paragraph(f"指标值：{value}", body))

    table = result.get("table") or {}
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    if columns and rows:
        story.append(Paragraph("数据明细", heading))
        data = [[str(c) for c in columns]] + [
            [str(r.get(c, "")) for c in columns] for r in rows
        ]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("（无数据行）", body))

    chart = result.get("chart") or {}
    if chart.get("data"):
        story.append(Paragraph("图表数据", heading))
        cdata = [["name", "value"]] + [
            [str(p["name"]), str(p["value"])] for p in chart["data"]
        ]
        ct = Table(cdata, repeatRows=1)
        ct.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(ct)
        if chart.get("truncated_series"):
            story.append(Paragraph("（图表数据已截断）", body))

    interp = result.get("interpretation") or {}
    segments = interp.get("segments") or []
    story.append(Paragraph("解读", heading))
    if segments:
        for seg in segments:
            kind = "事实" if seg.get("kind") == "fact" else "推断"
            story.append(Paragraph(f"[{kind}] {seg.get('text', '')}", body))
    else:
        story.append(Paragraph("（无解读）", body))

    doc.build(story)
    return buf.getvalue()
