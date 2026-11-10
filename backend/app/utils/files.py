"""文件校验与哈希工具（T-10）。

架构 §4：`utils/` = 文件哈希 / magic bytes / 脱敏 / ULID；`docs/架构设计.md` v1.4 §5.2
明确"文件哈希、magic bytes 校验、SHA-256 去重、写 resume_files + parse_tasks 全部属
`upload_service` 职责"，因此本文件只提供**纯函数**，不碰数据库、不碰 session。

三条必守口径（需求 F1-1 + 安全条目）：
1. **扩展名白名单**来自 `settings.upload_allowed_ext`，不在这里写死。
2. **伪造扩展名必须靠内容拦截**：只看后缀等于没校验。PDF 验 `%PDF-`；
   DOCX 是 ZIP，必须同时满足 ZIP 文件头**且**包含 `word/document.xml`
   （否则任意 zip 改后缀都能混进解析流水线，pdfminer/python-docx 会直接抛异常）。
3. **落盘路径必须由服务端生成**：文件名一律 UUID 重命名，且拼出的绝对路径必须仍在
   upload 根目录内（`safe_join`），用户提供的文件名只用于展示，永不参与路径拼接。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

PDF_MAGIC = b"%PDF-"
# ZIP 的三种合法头：普通文件 / 空档案 / 分卷续档
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

# DOCX 的判定锚点（OOXML 必备部件）；缺失即不是 Word 文档
DOCX_ANCHOR = "word/document.xml"

READ_CHUNK = 1024 * 1024


def normalize_ext(filename: str | None) -> str:
    """取小写扩展名（不含点）。

    只取最后一段路径成分，兼容浏览器可能传来的 `C:\\fake\\a.pdf` 与 `../../a.pdf`；
    无扩展名返回空串（由调用方判为非法）。
    """
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip().lower()


def display_name(filename: str | None) -> str:
    """原始文件名只保留最后一段，用于回显与落库（`resume_files.filename`）。

    `resume_files.filename` 是 VARCHAR(255)，超长直接截断——文件名不参与任何路径
    拼接，所以截断不会造成安全问题，只影响展示。

    ⚠️ **编码口径（H-13，不是缺陷，不要"修"）**：这里原样保留 multipart 头解出的
    字符串，**绝不做 latin-1↔GBK / GBK↔UTF-8 的猜测转码**。浏览器与 TestClient 都按
    UTF-8 传，`filename == "张三.pdf"` 是通的；出现 `ÕÅÈý...pdf` 只可能是 Git Bash 的
    curl 把中文名按 GBK 编成了字节。若在服务端"猜着转码"，合法的西欧文件名
    （`Müller` / `José`）会被转坏——命令行验证请改用浏览器或 pytest/TestClient。
    """
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:255] or "未命名文件"


def read_limited(stream, max_bytes: int) -> tuple[bytes, bool]:
    """从文件流读到 `max_bytes` 为止；返回 (内容, 是否超限)。

    超限时**不再继续读**（避免把 200MB 的文件全读进内存），由调用方判为 rejected。
    单文件上限 20MB，一次性载入内存可接受；后续 T-11 提取器同样按整文件处理。
    """
    buffer = bytearray()
    while True:
        chunk = stream.read(READ_CHUNK)
        if not chunk:
            return bytes(buffer), False
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            return bytes(buffer), True


def sha256_hex(data: bytes) -> str:
    """上传阶段去重用的 SHA-256（数据库 CHAR(64)）。"""
    return hashlib.sha256(data).hexdigest()


def _looks_like_pdf(data: bytes) -> bool:
    # `%PDF-` 必须出现在文件头（规范要求 1024 字节内，实际无一例外在 0 位置）
    return data.startswith(PDF_MAGIC)


def _looks_like_docx(data: bytes) -> bool:
    """ZIP 头 + 含 `word/document.xml`，两者缺一不可。"""
    if not data.startswith(ZIP_MAGICS):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return DOCX_ANCHOR in set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def validate_content(ext: str, data: bytes) -> str | None:
    """按扩展名校验文件真实内容；通过返回 `None`，不通过返回中文拒绝原因。"""
    if ext == "pdf":
        return None if _looks_like_pdf(data) else "文件内容不是有效的 PDF"
    if ext == "docx":
        return None if _looks_like_docx(data) else "文件内容不是有效的 DOCX"
    if ext in {"png", "jpg", "jpeg"}:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data)) as image:
                expected = "PNG" if ext == "png" else "JPEG"
                if image.format != expected:
                    return "图片内容与扩展名不一致"
                if image.width * image.height > 40_000_000:
                    return "图片像素超过安全上限（4000 万）"
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return "文件内容不是有效的图片"
        return None
    return "非法文件类型"


def safe_join(root: Path, *parts: str) -> Path:
    """在 `root` 内拼接路径；越界抛 `ValueError`（路径穿越防护的最后一道闸）。

    虽然本项目落盘用的文件名恒为 UUID（用户输入不参与拼接），这里仍做一次
    兜底断言：任何未来改动把用户输入带进路径时，会立即失败而不是写到 upload 目录外。
    """
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*parts).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError(f"路径越界：{target} 不在 {resolved_root} 内")
    return target
