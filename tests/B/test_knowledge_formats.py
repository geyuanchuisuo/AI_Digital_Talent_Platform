from hashlib import sha256
from types import SimpleNamespace
import pytest
from app.modules.knowledge.ingest import extract
from app.modules.knowledge.chunks import make_chunks
from app.core.errors import BizError


def revision(path, ext):
    return SimpleNamespace(stored_path=str(path), file_ext=ext, file_hash=sha256(path.read_bytes()).hexdigest())


def test_actual_docx_and_pdf_preserve_source_locations(tmp_path):
    from docx import Document
    from reportlab.pdfgen import canvas
    docx = tmp_path / 'synthetic.docx'
    document = Document();document.add_paragraph('Synthetic institutional policy: approval is required.');document.save(docx)
    pieces = make_chunks(extract(revision(docx, 'docx')))
    assert 'approval is required' in pieces[0]['text'] and pieces[0]['page_no'] is None
    pdf = tmp_path / 'synthetic.pdf'
    writer = canvas.Canvas(str(pdf))
    writer.drawString(50, 700, 'Synthetic institutional policy page one.');writer.showPage()
    writer.drawString(50, 700, 'Approval is required on page two.');writer.save()
    pieces = make_chunks(extract(revision(pdf, 'pdf')))
    assert [p['page_no'] for p in pieces] == [1, 2]
    assert 'Approval is required' in pieces[1]['text']


def test_document_exceeding_page_limit_is_not_partially_published(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas
    from app.config import settings
    monkeypatch.setattr(settings, 'ocr_max_pages', 1)
    pdf = tmp_path / 'synthetic_limit.pdf'
    writer = canvas.Canvas(str(pdf))
    for i in range(2):
        writer.drawString(50, 700, f'Synthetic policy page {i + 1}.');writer.showPage()
    writer.save()
    with pytest.raises(BizError) as exc:
        extract(revision(pdf, 'pdf'))
    assert exc.value.code == 422


def test_worker_and_models_can_be_imported_from_a_cold_process():
    import subprocess
    import sys
    from pathlib import Path
    code = ('from app.modules.knowledge.ingest import extract; '
            'from app.modules.knowledge.qa_worker import handle_question; '
            'from app.models import TABLE_NAMES; '
            'assert "knowledge_documents" in TABLE_NAMES and "knowledge_question_jobs" in TABLE_NAMES')
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
