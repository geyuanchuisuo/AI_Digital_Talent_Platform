"""T-11 文本提取器单测（L1：只读写项目内临时文件，不触 MySQL/Milvus/LLM）。

样例说明：
- PDF 用**手写最小 PDF**（Helvetica + WinAnsi）合成，不引 reportlab/fpdf 之类新依赖；
  中文 PDF 的文本层质量留给真实简历联调（架构 §8「中文 PDF 文本层质量差」已知风险）。
- DOCX 用 python-docx 合成，覆盖正文/表格/页眉页脚与中文。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter

from app.service.extract_service import (
    DEFAULT_TEXT_MAX_CHARS,
    EMPTY_TEXT_MESSAGE,
    ENCRYPTED_PDF_MESSAGE,
    ExtractError,
    extract_text,
    normalize_text,
)


@pytest.fixture()
def sample_dir():
    """项目内临时目录（H-7：系统 Temp 可能拒绝访问，不用 pytest 的 tmp_path）。"""
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        yield Path(directory)


# ---------------------------------------------------------------- 合成样例


def _write_pdf(path: Path, lines: list[str]) -> Path:
    """最小可用单页 PDF：页面内容流里逐行 `Tj`，可用 pypdf 抽回文本。"""
    content = ["BT", "/F1 12 Tf", "72 770 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        if index:
            content.append("0 -18 Td")
        content.append(f"({escaped}) Tj")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")  # WinAnsi：样例只用 ASCII

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    data = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(data)
    total = len(objects) + 1
    data += f"xref\n0 {total}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(data))
    return path


def _write_docx(
    path: Path,
    *,
    paragraphs: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
    header: str | None = None,
    footer: str | None = None,
) -> Path:
    document = Document()
    if header:
        document.sections[0].header.paragraphs[0].text = header
    if footer:
        document.sections[0].footer.paragraphs[0].text = footer
    for text in paragraphs or []:
        document.add_paragraph(text)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row in enumerate(table_rows):
            for col_index, value in enumerate(row):
                table.rows[row_index].cells[col_index].text = value
    document.save(path)
    return path


# ---------------------------------------------------------------- PDF


@pytest.mark.parametrize(
    "lines",
    [
        ["Zhang San", "Phone: 13800000000", "Email: zhangsan@example.com"],
        ["Li Si - Backend Engineer", "Skills: Python / FastAPI / MySQL", "3 years experience"],
        ["Wang Wu", "Education: BSc Computer Science", "Project: resume parser"],
    ],
)
def test_pdf_samples_extract_completely(sample_dir, lines):
    """验收点：合成 PDF 3 份提取完整（逐行都在，且不误判截断）。"""
    result = extract_text(_write_pdf(sample_dir / "sample.pdf", lines))
    for line in lines:
        assert line in result.text
    assert result.truncated is False
    assert result.raw_length >= sum(len(line) for line in lines)


def test_pdf_empty_pages_raise_empty_error(sample_dir):
    """纯图片/空白 PDF 不返回空串，而是明确失败（T-13 落 failed(stage=extract)）。"""
    path = sample_dir / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ExtractError, match="未能提取到文本"):
        extract_text(path)


def test_pdf_encrypted_raises(sample_dir):
    path = sample_dir / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ExtractError, match=ENCRYPTED_PDF_MESSAGE):
        extract_text(path)


def test_pdf_corrupt_raises(sample_dir):
    path = sample_dir / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a real pdf")
    with pytest.raises(ExtractError, match="PDF 解析失败"):
        extract_text(path)


# ---------------------------------------------------------------- DOCX


@pytest.mark.parametrize(
    "payload",
    [
        {
            "paragraphs": ["姓名：张三", "求职意向：后端开发工程师", "技能：Python、FastAPI、MySQL"],
        },
        {
            "paragraphs": ["姓名：李四", "教育背景：某大学 计算机科学与技术 本科"],
            "table_rows": [["时间", "公司", "职位"], ["2020-2023", "字节跳动", "后端工程师"]],
        },
        {
            "paragraphs": ["姓名：王五", "自我评价：三年后端经验，熟悉高并发系统设计"],
            "header": "王五的简历",
            "footer": "第 1 页",
        },
    ],
)
def test_docx_samples_extract_completely(sample_dir, payload):
    """验收点：合成 DOCX 3 份提取完整（中文、表格、页眉页脚都取到）。"""
    path = _write_docx(sample_dir / "sample.docx", **payload)
    result = extract_text(path)

    for text in payload.get("paragraphs", []):
        assert text in result.text
    for row in payload.get("table_rows", []):
        for cell in row:
            assert cell in result.text
    if payload.get("header"):
        assert payload["header"] in result.text
    if payload.get("footer"):
        assert payload["footer"] in result.text
    assert result.truncated is False


def test_docx_body_order_keeps_paragraph_and_table_alternation(sample_dir):
    """正文按文档顺序：段落 → 表格 → 段落，不是"段落全在前、表格全在后"。"""
    path = _write_docx(
        sample_dir / "order.docx",
        paragraphs=["第一段", "第二段"],
        table_rows=[["单元格A", "单元格B"]],
    )
    # 表格是最后 add 的，这里再补一段，验证表格之后的段落仍排在表格后面
    document = Document(str(path))
    document.add_paragraph("第三段")
    document.save(str(path))

    text = extract_text(path).text
    assert text.index("第二段") < text.index("单元格A") < text.index("第三段")


def test_docx_table_row_uses_tab_separator(sample_dir):
    path = _write_docx(sample_dir / "table.docx", table_rows=[["2020-2023", "字节跳动", "后端工程师"]])
    text = extract_text(path).text
    assert "2020-2023\t字节跳动\t后端工程师" in text


def test_docx_merged_cells_are_deduplicated(sample_dir):
    """合并单元格会让 row.cells 重复返回同一内容，输出里不能出现 `合并\t合并`。"""
    path = sample_dir / "merged.docx"
    document = Document()
    table = document.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "合并项"
    table.rows[0].cells[0].merge(table.rows[0].cells[1])
    document.save(str(path))

    text = extract_text(path).text
    assert "合并项\t合并项" not in text
    assert "合并项" in text


def test_docx_empty_raises(sample_dir):
    path = sample_dir / "empty.docx"
    Document().save(str(path))
    with pytest.raises(ExtractError, match="未能提取到文本"):
        extract_text(path)


def test_docx_corrupt_raises(sample_dir):
    path = sample_dir / "fake.docx"
    path.write_bytes(b"PK\x03\x04 definitely not a docx")
    with pytest.raises(ExtractError, match="DOCX 解析失败"):
        extract_text(path)


# ---------------------------------------------------------------- 截断 / 归一化 / 参数


def test_long_text_is_truncated_and_flagged(sample_dir):
    """超 15000 字符截断并记 truncated；raw_length 保留截断前的长度。"""
    path = _write_docx(sample_dir / "long.docx", paragraphs=["简" * (DEFAULT_TEXT_MAX_CHARS + 500)])
    result = extract_text(path)

    assert result.truncated is True
    assert len(result.text) == DEFAULT_TEXT_MAX_CHARS
    assert result.raw_length == DEFAULT_TEXT_MAX_CHARS + 500


def test_exactly_limit_is_not_truncated(sample_dir):
    path = _write_docx(sample_dir / "limit.docx", paragraphs=["简" * DEFAULT_TEXT_MAX_CHARS])
    result = extract_text(path)
    assert result.truncated is False
    assert len(result.text) == DEFAULT_TEXT_MAX_CHARS


def test_custom_max_chars(sample_dir):
    path = _write_docx(sample_dir / "small.docx", paragraphs=["abcdefghij"])
    result = extract_text(path, max_chars=4)
    assert result.text == "abcd"
    assert result.truncated is True
    assert result.raw_length == 10


def test_zero_max_chars_rejected(sample_dir):
    path = _write_docx(sample_dir / "zero.docx", paragraphs=["x"])
    with pytest.raises(ValueError, match="max_chars"):
        extract_text(path, max_chars=0)


def test_normalize_text_crlf_nbsp_and_blank_lines():
    raw = "\ufeff第一行\r\n第二行\r\n\r\n\r\n\r\n第三行\u00a0 \r\n   \r\n"
    assert normalize_text(raw) == "第一行\n第二行\n\n第三行"


def test_unsupported_ext_raises(sample_dir):
    path = sample_dir / "note.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ExtractError, match="不支持的文件类型"):
        extract_text(path)


def test_missing_file_raises(sample_dir):
    with pytest.raises(ExtractError, match="简历文件不存在"):
        extract_text(sample_dir / "nope.pdf")


def test_explicit_ext_overrides_suffix(sample_dir):
    """T-13 会传 `resume_files.file_ext`；即使磁盘后缀丢了也能按库里的 ext 提取。"""
    path = _write_pdf(sample_dir / "no_suffix", ["Zhang San", "Python"])
    result = extract_text(path, ext="pdf")
    assert "Zhang San" in result.text


def test_empty_message_constant_is_used():
    """错误文案集中在模块常量，T-13 的 error 字段与测试断言共用一份，避免两处漂移。"""
    assert EMPTY_TEXT_MESSAGE.startswith("未能提取到文本")
