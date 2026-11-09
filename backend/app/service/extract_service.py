"""F02 逐页提取：PDF 文本层优先，图片与扫描页走本地 OCR。

完整原页文本/来源/质量供持久化证据使用；只有模型输入受 15000 字符限制。
混合文件逐页处理，失败保留已处理页和错误，不能静默丢页冒称成功。
DOCX 保留正文/表格/页眉页脚顺序，使用文档文本定位，不虚构物理分页。
本层不访问数据库或大模型；OCR 受开关、页数、像素与进程超时限制。
"""

from __future__ import annotations

import logging
from io import BytesIO
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger("app.extract")

#: 简历文本上限：LLM提示词设计「简历文本 ≤ 15000 字符」。
#: 运行配置 v1.3 没有对应键，因此**不伪造** `TEXT_MAX_CHARS`；真要可配，
#: 先按开发日志 §1.3 在运行配置登记，再把这里换成 settings 读取。
DEFAULT_TEXT_MAX_CHARS = 15000

#: 与 `settings.upload_allowed_ext` 对齐（上传阶段已拦，这里再兜一层，防脏数据）
SUPPORTED_EXTS: tuple[str, ...] = ("pdf", "docx", "png", "jpg", "jpeg")

EMPTY_TEXT_MESSAGE = "未能提取到文本：文件可能为空白、模糊或内容无法识别"
ENCRYPTED_PDF_MESSAGE = "PDF 已加密，无法提取文本"


class ExtractError(RuntimeError):
    """提取失败；`str(exc)` 直接作为 `parse_tasks.error` 落库。"""

    def __init__(self, message: str, *, pages=()):
        super().__init__(message)
        self.pages = tuple(pages)


@dataclass(frozen=True)
class ExtractedPage:
    page_no: int
    text: str
    source_type: str = "text"
    confidence: float | None = None
    quality_status: str = "ready"
    error_message: str | None = None


@dataclass(frozen=True)
class ExtractResult:
    """提取结果：`text` 已归一化并截断，`truncated` 供 T-13 写 `parse_tasks`。"""

    text: str
    truncated: bool
    raw_length: int  # 截断前的字符数，仅用于日志/排查，不入库
    pages: tuple[ExtractedPage, ...] = ()


def extract_text(
    path: Path | str,
    *,
    ext: str | None = None,
    max_chars: int = DEFAULT_TEXT_MAX_CHARS,
) -> ExtractResult:
    """提取 `path` 的纯文本。

    `ext` 缺省时取文件后缀；T-13 传 `resume_files.file_ext` 更准（避免存量行后缀丢失）。
    """
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")

    file_path = Path(path)
    suffix = (ext or file_path.suffix.lstrip(".")).strip().lower().lstrip(".")
    if suffix not in SUPPORTED_EXTS:
        raise ExtractError(f"不支持的文件类型：{suffix or '无扩展名'}")
    if not file_path.is_file():
        raise ExtractError(f"简历文件不存在：{file_path}")

    from app.config import settings
    deadline = time.monotonic() + settings.ocr_timeout_seconds
    page_truncated = False
    if suffix == "pdf":
        pages, page_truncated = _extract_pdf_pages(file_path, deadline)
    elif suffix == "docx":
        pages = [ExtractedPage(1, normalize_text(_extract_docx(file_path)), "document")]
    else:
        pages = [_image_page(file_path, deadline)]
    raw = "\n\n".join(page.text for page in pages if page.text.strip())
    text = normalize_text(raw)
    if not text:
        raise ExtractError(EMPTY_TEXT_MESSAGE, pages=pages)

    if len(text) > max_chars:
        logger.info(
            "resume_text_truncated path=%s raw=%d limit=%d", file_path.name, len(text), max_chars
        )
        return ExtractResult(text=text[:max_chars], truncated=True, raw_length=len(text), pages=tuple(pages))
    return ExtractResult(text=text, truncated=page_truncated, raw_length=len(text), pages=tuple(pages))


# ---------------------------------------------------------------- 归一化


def normalize_text(raw: str) -> str:
    """CRLF→LF、NBSP→空格、去 BOM、去行尾空白、连续空行压缩、首尾 strip。

    **不要在这里做全角/半角或简繁转换**：那是归一化模块（T-23 排期）的职责，
    提取层动字符会改变 `raw_text` 原文，影响回溯。
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


# ---------------------------------------------------------------- PDF


def _ocr_image(image: bytes, timeout: float) -> tuple[str, float | None]:
    from app.config import BASE_DIR, settings
    from app.modules.files.ocr import OCRFailure, recognize
    directory = Path(settings.ocr_model_dir)
    if not directory.is_absolute():
        directory = BASE_DIR / directory
    try:
        return recognize(image, timeout=timeout, directory=directory)
    except (OCRFailure, ImportError) as exc:
        raise ExtractError(str(exc)) from exc


def _recognized_page(image: bytes, number: int, deadline: float) -> ExtractedPage:
    from app.config import settings
    if not settings.ocr_enabled:
        raise ExtractError("图片或扫描页需要 OCR，但当前环境未启用 OCR")
    text, confidence = _ocr_image(image, deadline - time.monotonic())
    text = normalize_text(text)
    if not text:
        from PIL import Image
        with Image.open(BytesIO(image)) as original:
            blank = original.convert("L").getextrema()[0] >= 250
        if not blank:
            raise ExtractError("OCR 未识别出该页有效文字，请核对或上传更清晰的资料")
        return ExtractedPage(number, "", "blank", quality_status="empty")
    if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
        confidence = None
    quality = "empty" if not text else ("low_confidence" if confidence is None or confidence < 0.8 else "ready")
    return ExtractedPage(number, text, "ocr", confidence, quality)


def _image_page(path: Path, deadline: float) -> ExtractedPage:
    from app.config import settings
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ExtractError("图片像素超过安全上限（4000 万）")
            image = ImageOps.exif_transpose(image).convert("RGB")
            max_pixels = max(1, settings.ocr_max_page_pixels)
            scale = min(1, math.sqrt(max_pixels / (image.width * image.height)))
            image.thumbnail((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            output = BytesIO()
            image.save(output, format="PNG")
        return _recognized_page(output.getvalue(), 1, deadline)
    except (ExtractError, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        error = str(exc) if isinstance(exc, ExtractError) else f"图片解析失败：{exc}"
        raise ExtractError(error, pages=[ExtractedPage(1, "", "ocr", quality_status="failed", error_message=error)]) from exc


def _render_page(path: Path, index: int) -> bytes:
    from app.config import settings
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
    except ImportError as exc:
        raise ExtractError("PDF 页渲染依赖缺失，请安装仓库声明的 PyMuPDF") from exc
    with fitz.open(path) as document:
        page = document[index]
        area = max(1, page.rect.width * page.rect.height)
        scale = min(150 / 72, math.sqrt(max(1, settings.ocr_max_page_pixels) / area))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def _has_scan_body(path: Path, index: int) -> bool:
    """A dominant scanned image must not be hidden by a searchable watermark."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(path) as document:
        page = document[index]
        area = max(1, page.rect.get_area())
        return any((fitz.Rect(item["bbox"]) & page.rect).get_area() / area >= 0.35
                   for item in page.get_image_info())


def _extract_pdf_pages(path: Path, deadline: float) -> tuple[list[ExtractedPage], bool]:
    from app.config import settings
    pages: list[ExtractedPage] = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # 简历常见"仅限制编辑"的 PDF：空密码能解就继续，解不开才算失败
            try:
                unlocked = bool(reader.decrypt(""))
            except Exception:  # noqa: BLE001 - 加密算法不支持等，统一按"打不开"处理
                unlocked = False
            if not unlocked:
                raise ExtractError(ENCRYPTED_PDF_MESSAGE)

        limit = max(1, settings.ocr_max_pages)
        truncated = len(reader.pages) > limit
        for index, page in enumerate(reader.pages[:limit]):
            try:
                text = normalize_text(page.extract_text() or "")
                has_images = bool(page.images)
                scan_body = has_images and _has_scan_body(path, index)
                if text and not scan_body and (not has_images or len(text) >= 20):
                    pages.append(ExtractedPage(index + 1, text))
                elif not text and not has_images and not settings.ocr_enabled:
                    pages.append(ExtractedPage(index + 1, "", quality_status="empty"))
                else:
                    if not settings.ocr_enabled:
                        raise ExtractError("扫描页需要 OCR，但当前环境未启用 OCR")
                    pages.append(_recognized_page(_render_page(path, index), index + 1, deadline))
            except Exception as exc:
                error = f"第 {index + 1} 页处理失败：{exc}"
                pages.append(ExtractedPage(index + 1, "", "ocr", quality_status="failed", error_message=error))
                raise ExtractError(error, pages=pages) from exc
    except ExtractError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise ExtractError(f"PDF 解析失败：{exc}") from exc

    return pages, truncated


# ---------------------------------------------------------------- DOCX


def _extract_docx(path: Path) -> str:
    try:
        document = Document(str(path))
    except (PackageNotFoundError, OSError, ValueError, KeyError) as exc:
        # 后缀改名的 zip / 损坏文件都会走到这里（上传层 magic bytes 之外的兜底）
        raise ExtractError(f"DOCX 解析失败：{exc}") from exc

    blocks: list[str] = []
    seen_header: set[str] = set()

    # 页眉：多节常重复同一段文字，按内容去重，避免同一句出现 3 次
    for section in document.sections:
        for block in _part_blocks(section.header):
            if block not in seen_header:
                seen_header.add(block)
                blocks.append(block)

    # 正文：按 w:p / w:tbl 的真实顺序
    for block in _iter_body_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(text)
        else:
            table_text = _table_text(block)
            if table_text:
                blocks.append(table_text)

    # 页脚：同理去重（页脚通常是"第 X 页"之类，重复不影响，但没必要重复三遍）
    seen_footer: set[str] = set()
    for section in document.sections:
        for block in _part_blocks(section.footer):
            if block not in seen_footer:
                seen_footer.add(block)
                blocks.append(block)

    return "\n".join(blocks)


def _iter_body_blocks(document) -> Iterator[Paragraph | Table]:
    """按文档顺序产出正文段落与表格（python-docx 无现成 API，只能看 `w:body` 子节点）。"""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _part_blocks(part) -> list[str]:
    """页眉/页脚：段落 + 表格（顺序不重要，内容取全即可）。"""
    blocks = [p.text.strip() for p in part.paragraphs if p.text.strip()]
    for table in part.tables:
        text = _table_text(table)
        if text:
            blocks.append(text)
    return blocks


def _table_text(table: Table, depth: int = 0) -> str:
    """表格 → 一行一条记录，单元格用 `\\t` 分隔。

    Word 的合并单元格会让 `row.cells` 重复返回同一内容，这里去掉**相邻**重复，
    避免"姓名\t姓名\t姓名"这种噪音；嵌套表格最多再取一层（简历里极少见）。
    """
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            if depth < 1:
                nested = [_table_text(inner, depth + 1) for inner in cell.tables]
                text = "\n".join([text, *[item for item in nested if item]]).strip()
            if text and (not cells or cells[-1] != text):
                cells.append(text)
        if cells:
            rows.append("\t".join(cells))
    return "\n".join(rows)
