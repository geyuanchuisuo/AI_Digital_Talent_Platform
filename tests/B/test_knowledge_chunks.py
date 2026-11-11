import pytest


def test_chunks_keep_real_offsets_and_stable_order():
    from app.modules.knowledge.chunks import make_chunks
    source = '制度原文 ' * 400
    pages = [{'page_no': 2, 'text': source, 'source_type': 'text', 'quality_status': 'ready'}]
    first = make_chunks(pages)
    assert first == make_chunks(pages)
    assert len(first) > 1
    assert [c['chunk_no'] for c in first] == list(range(len(first)))
    assert first[0]['offset_start'] == 0 and first[-1]['offset_end'] == len(source)
    assert all(c['page_no'] == 2 and source[c['offset_start']:c['offset_end']] == c['text'] for c in first)
    assert all(len(c['text']) <= 1000 for c in first)


def test_docx_and_txt_do_not_invent_physical_pages():
    from app.modules.knowledge.chunks import make_chunks
    for source_type in ('document', 'txt'):
        result = make_chunks([{'page_no': 1, 'text': '合成制度正文', 'source_type': source_type, 'quality_status': 'ready'}])
        assert result[0]['page_no'] is None
        assert result[0]['text'] == '合成制度正文'


def test_failed_or_empty_source_is_not_indexed_as_complete():
    from app.modules.knowledge.chunks import make_chunks
    with pytest.raises(ValueError):
        make_chunks([])
    with pytest.raises(ValueError):
        make_chunks([{'text': '前半内容', 'source_type': 'text', 'page_no': 1, 'quality_status': 'ready'},
                     {'text': '', 'source_type': 'text', 'page_no': 2, 'quality_status': 'failed'}])
