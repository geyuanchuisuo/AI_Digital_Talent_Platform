"""T-10 L1：文件校验与哈希纯函数单测（不连 MySQL / 不外呼）。

对应 todolist T-10 验收点的可单测部分：
- 伪造扩展名（exe 改 .pdf）必须被 magic bytes 拦下
- DOCX 必须同时满足 ZIP 头 + 含 `word/document.xml`（普通 zip 改后缀不放行）
- 文件名剥离路径、扩展名归一、路径穿越防护
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.utils.files import (
    display_name,
    normalize_ext,
    read_limited,
    safe_join,
    sha256_hex,
    validate_content,
)

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"  # PE 头，伪装成 .pdf 的典型样本


def make_docx_bytes(paragraph: str = "张三") -> bytes:
    """用 python-docx 造一份真实 docx（含 word/document.xml）。"""
    import docx

    document = docx.Document()
    document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_plain_zip() -> bytes:
    """普通 zip（没有 word/document.xml），用于验证"只验 ZIP 头"是不够的。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a word document")
    return buffer.getvalue()


# ---------------------------------------------------------------- 扩展名 / 文件名


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("张三.pdf", "pdf"),
        ("张三.PDF", "pdf"),
        ("C:\\Users\\hr\\桌面\\简历.Docx", "docx"),
        ("../../etc/passwd.pdf", "pdf"),
        ("noext", ""),
        ("", ""),
        (None, ""),
        ("a.b.c.pdf", "pdf"),
    ],
)
def test_normalize_ext(raw, expected):
    assert normalize_ext(raw) == expected


def test_display_name_strips_path_and_truncates():
    assert display_name("C:\\fake\\dir\\张三.pdf") == "张三.pdf"
    assert display_name("../../x.docx") == "x.docx"
    assert display_name(None) == "未命名文件"
    assert len(display_name("a" * 400 + ".pdf")) == 255


# ---------------------------------------------------------------- magic bytes


def test_pdf_content_accepted():
    assert validate_content("pdf", PDF_BYTES) is None


def test_fake_pdf_rejected():
    """exe 改 .pdf —— 验收点要求必须拦下。"""
    assert validate_content("pdf", EXE_BYTES) == "文件内容不是有效的 PDF"
    # 只把扩展名换成 pdf 的真实 docx 同样要拦
    assert validate_content("pdf", make_docx_bytes()) is not None


def test_docx_content_accepted():
    assert validate_content("docx", make_docx_bytes("李四")) is None


def test_plain_zip_renamed_as_docx_rejected():
    assert validate_content("docx", make_plain_zip()) == "文件内容不是有效的 DOCX"


def test_fake_docx_rejected():
    assert validate_content("docx", EXE_BYTES) == "文件内容不是有效的 DOCX"
    assert validate_content("docx", PDF_BYTES) == "文件内容不是有效的 DOCX"


def test_unknown_ext_rejected():
    assert validate_content("exe", EXE_BYTES) == "非法文件类型"


# ---------------------------------------------------------------- 读取 / 哈希


def test_read_limited_reports_overflow_without_reading_all():
    """超过上限即停：3MB 的流读到第 2 个 1MB 块就返回，不会把整个流读进内存。"""
    from app.utils.files import READ_CHUNK

    stream = io.BytesIO(b"x" * (READ_CHUNK * 3))
    data, overflow = read_limited(stream, READ_CHUNK)
    assert overflow is True
    assert len(data) == READ_CHUNK * 2
    assert stream.tell() == READ_CHUNK * 2 < READ_CHUNK * 3


def test_read_limited_small_stream_reads_all():
    stream = io.BytesIO(b"x" * 5000)
    data, overflow = read_limited(stream, 4096)
    assert overflow is True  # 单块读完也要判超限（不因"一次就读到 EOF"而漏判）
    assert len(data) == 5000


def test_read_limited_normal():
    stream = io.BytesIO(b"abc")
    assert read_limited(stream, 4096) == (b"abc", False)


def test_sha256_hex_matches_hashlib():
    import hashlib

    assert sha256_hex(b"ai-talent") == hashlib.sha256(b"ai-talent").hexdigest()
    assert len(sha256_hex(PDF_BYTES)) == 64


# ---------------------------------------------------------------- 路径穿越防护


def test_safe_join_normal():
    from pathlib import Path

    root = Path.cwd()
    joined = safe_join(root, "2026-11-05", "abc.pdf")
    assert joined.parent.name == "2026-11-05"
    assert joined.is_relative_to(root.resolve())


@pytest.mark.parametrize("bad", ["..", "../..", "../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini"])
def test_safe_join_blocks_escape(bad):
    from pathlib import Path

    with pytest.raises(ValueError):
        safe_join(Path.cwd(), bad)
