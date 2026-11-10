"""F02 per-page extraction: synthetic inputs, injected OCR for unit cases."""
from io import BytesIO

from PIL import Image, ImageDraw
import pytest
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from docx import Document

from app.config import settings
from app.service import extract_service as svc
from app.utils.files import validate_content


def _slow_ocr_worker(connection, image, models):
    import time
    time.sleep(10)


def image_bytes(fmt="PNG"):
    stream = BytesIO()
    image=Image.new("RGB", (320, 200), "white")
    ImageDraw.Draw(image).text((10,40), "SYNTHETIC RESUME CONTENT", fill="black")
    image.save(stream, format=fmt)
    return stream.getvalue()


def pdf(path, kinds):
    output = canvas.Canvas(str(path))
    for kind in kinds:
        if kind == "text":
            output.drawString(50, 700, "SYNTHETIC Alice Python Engineer")
        elif kind == "image":
            output.drawImage(ImageReader(BytesIO(image_bytes())), 20, 200, width=320, height=200)
        output.showPage()
    output.save()
    return path


@pytest.fixture(autouse=True)
def ocr_settings(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "ocr_max_pages", 50)
    monkeypatch.setattr(svc, "_ocr_image", lambda image, timeout: ("合成测试 张晓林 Python 工程师", 0.72), raising=False)


@pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("JPEG", "jpg")])
def test_images_have_real_content_checks_and_reviewable_pages(tmp_path, fmt, ext):
    data = image_bytes(fmt)
    assert validate_content(ext, data) is None
    assert validate_content(ext, b"not an image") is not None
    path = tmp_path / ("sample." + ext)
    path.write_bytes(data)
    result = svc.extract_text(path)
    assert "张晓林" in result.text
    page = result.pages[0]
    assert (page.page_no, page.source_type, page.quality_status) == (1, "ocr", "low_confidence")
    assert page.confidence == 0.72


def test_mixed_pdf_keeps_text_layer_and_only_ocrs_scan(tmp_path):
    result = svc.extract_text(pdf(tmp_path / "mixed.pdf", ["text", "image"]))
    assert [p.source_type for p in result.pages] == ["text", "ocr"]
    assert "Alice" in result.pages[0].text
    assert "张晓林" in result.pages[1].text
    assert not result.truncated


def test_disabled_ocr_reports_scan_instead_of_losing_a_page(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", False)
    with pytest.raises(svc.ExtractError, match="未启用"):
        svc.extract_text(pdf(tmp_path / "mixed.pdf", ["text", "image"]))


def test_page_limit_is_explicitly_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    result = svc.extract_text(pdf(tmp_path / "long.pdf", ["text", "text"]))
    assert result.truncated
    assert len(result.pages) == 1


def test_scan_failure_retains_page_failure_and_never_returns_partial_success(tmp_path, monkeypatch):
    def fail(*args):
        raise svc.ExtractError("OCR 超时")
    monkeypatch.setattr(svc, "_ocr_image", fail, raising=False)
    with pytest.raises(svc.ExtractError, match="第 2 页.*超时") as raised:
        svc.extract_text(pdf(tmp_path / "failed.pdf", ["text", "image"]))
    assert raised.value.pages[0].quality_status == "ready"
    assert raised.value.pages[1].quality_status == "failed"


def test_docx_text_has_no_claim_to_physical_pagination(tmp_path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("合成测试 张晓林 Python")
    document.save(path)
    result = svc.extract_text(path)
    assert result.pages[0].source_type == "document"
    assert "张晓林" in result.pages[0].text


def test_unknown_ocr_confidence_is_not_reported_as_good_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_ocr_image", lambda *args: ("需要核实的识别文本", None))
    path=tmp_path / "unknown.png"
    path.write_bytes(image_bytes())
    assert svc.extract_text(path).pages[0].quality_status == "low_confidence"


def test_missing_models_fail_explicitly_and_timeout_stops_the_child(tmp_path, monkeypatch):
    import multiprocessing
    import time
    from app.modules.files import ocr
    with pytest.raises(ocr.OCRFailure, match="模型缺失"):
        ocr.recognize(b"image", timeout=1, directory=tmp_path)
    for kind in ("det", "cls", "rec"):
        (tmp_path / f"{kind}.onnx").write_bytes(b"unit test placeholder")
    monkeypatch.setattr(ocr, "_worker", _slow_ocr_worker)
    start=time.monotonic()
    with pytest.raises(ocr.OCRFailure, match="超时"):
        ocr.recognize(b"image", timeout=0.15, directory=tmp_path)
    assert time.monotonic()-start < 5
    assert not multiprocessing.active_children()


def test_empty_ocr_on_a_nonblank_scan_cannot_succeed_with_only_other_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_ocr_image", lambda *args: ("", None))
    with pytest.raises(svc.ExtractError, match="第 2 页"):
        svc.extract_text(pdf(tmp_path / 'unreadable.pdf', ['text','image']))


def test_text_watermark_does_not_hide_dominant_scanned_body(tmp_path):
    path=tmp_path/'watermark.pdf'
    output=canvas.Canvas(str(path))
    output.drawString(30,800,'Scanned resume generated by scanner software')
    output.drawImage(ImageReader(BytesIO(image_bytes())),30,70,width=500,height=650)
    output.save()
    result=svc.extract_text(path)
    assert result.pages[0].source_type=='ocr'
    assert '张晓林' in result.text


def test_real_blank_separator_page_is_not_mistaken_for_missing_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_ocr_image", lambda *args: ("", None))
    result=svc.extract_text(pdf(tmp_path/'separator.pdf',['text','blank']))
    assert result.pages[1].quality_status=='empty'
    assert not result.truncated
