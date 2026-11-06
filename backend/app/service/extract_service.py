"""简历文本提取（T-11，需求 F1-1 / 架构 §6.1）。

职责单一：**文件 → 纯文本**。不做 LLM 结构化（T-13）、不碰数据库、不改
`parse_tasks.truncated`（由 T-13 拿到 `ExtractResult` 后落库），因此
`tasks/parse_worker.py` 可以直接调用，单测也不需要 MySQL/Milvus。

口径来源（改实现前先看这三处，不要另立一套）：
- `docs/LLM提示词设计.md`：简历文本 ≤ **15000 字符**，超长在提取层截断，
  LLM 不感知截断（截断部分忽略）。
- `docs/架构设计.md` §8：大简历超上下文 → 截断并记录 `truncated`。
- `docs/需求文档.md` F1-1 + todolist T-11：**空文本判失败**（扫描件/纯图片
  PDF 不做 OCR）。

实现要点：
1. **PDF 用 pypdf，DOCX 用 python-docx**（架构 §3 选型，不引 OCR、不调外部服务）。
2. **DOCX 取全**：正文段落 + 表格（含嵌套一层）+ 页眉页脚，正文按文档真实顺序
   输出（`w:p` / `w:tbl` 交替），表格一行一条、单元格用制表符分隔——比"段落全
   在前、表格全在后"更接近人读的顺序，LLM 提取经历时不容易错位。
3. **统一归一化**：CRLF/CR → LF、NBSP → 空格、去 BOM、去行尾空白、连续空行
   压成一个，最后 `strip()`。归一化后再判空、再截断——截断长度才是"喂给 LLM 的
   字符数"，与 `truncated` 语义一致。
4. **失败一律抛 `ExtractError`**（中文原因），由 T-13 捕获后落
   `failed(stage=extract, error=str(e))`；不在这里写数据库，也不吞异常返回空串。
5. **不猜编码、不做 OCR、不修 PDF 结构**：`pypdf` 抽不出文字就是空文本，明确失败，
   比返回半截乱码更容易排查。
6. **已知边界（不是缺陷）**：Word 的文本框（`w:txbxContent`）、SmartArt、图片内文字
   不提取；PDF 只取文本层，扫描件不 OCR。简历正文/表格/页眉页脚已覆盖，遇到
   "内容明明有却提不到"先确认是不是文本框或图片。
"""

from __future__ import annotations

import logging
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
SUPPORTED_EXTS: tuple[str, ...] = ("pdf", "docx")

EMPTY_TEXT_MESSAGE = "未能提取到文本：可能是扫描件/纯图片，或文件内容为空"
ENCRYPTED_PDF_MESSAGE = "PDF 已加密，无法提取文本"


class ExtractError(RuntimeError):
    """提取失败；`str(exc)` 直接作为 `parse_tasks.error` 落库。"""


@dataclass(frozen=True)
class ExtractResult:
    """提取结果：`text` 已归一化并截断，`truncated` 供 T-13 写 `parse_tasks`。"""

    text: str
    truncated: bool
    raw_length: int  # 截断前的字符数，仅用于日志/排查，不入库


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

    raw = _extract_pdf(file_path) if suffix == "pdf" else _extract_docx(file_path)
    text = normalize_text(raw)
    if not text:
        raise ExtractError(EMPTY_TEXT_MESSAGE)

    if len(text) > max_chars:
        logger.info(
            "resume_text_truncated path=%s raw=%d limit=%d", file_path.name, len(text), max_chars
        )
        return ExtractResult(text=text[:max_chars], truncated=True, raw_length=len(text))
    return ExtractResult(text=text, truncated=False, raw_length=len(text))


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


def _extract_pdf(path: Path) -> str:
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

        pages: list[str] = []
        for index, page in enumerate(reader.pages):
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - 单页坏不影响其他页，最后按空文本判定
                logger.warning("pdf_page_extract_failed file=%s page=%d", path.name, index + 1, exc_info=True)
                pages.append("")
    except ExtractError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise ExtractError(f"PDF 解析失败：{exc}") from exc

    return "\n\n".join(page for page in pages if page.strip())


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
