import pytest
from app.modules.talents import qa


def evidence(text='熟悉 Python 和 SQL'):
    return dict(file_id=2, page_no=1, snippet=text, source='text', field_path='skills')


def test_no_evidence_does_not_call_model():
    def forbidden(*args):
        raise AssertionError('must not call model')
    result = qa.answer_from_evidence('有什么技能？', [], ranker=forbidden, generator=forbidden)
    assert result['status'] == 'insufficient_evidence'
    assert result['citations'] == []


def test_citations_are_server_owned_and_duplicates_removed():
    rows = [evidence(), evidence()]
    result = qa.answer_from_evidence('有什么技能？', rows,
        ranker=lambda q, texts: [0.9] * len(texts),
        generator=lambda q, context: {'answer':'熟悉 Python。', 'citation_ids':[1]})
    assert result['status'] == 'answered'
    assert result['citations'] == [dict(evidence(), citation_id=1)]


@pytest.mark.parametrize('ids', [[], [2], [0], [-1]])
def test_missing_or_invented_citation_is_rejected(ids):
    with pytest.raises(qa.QAError):
        qa.answer_from_evidence('技能？', [evidence()], ranker=lambda q,t:[1.0],
            generator=lambda q,c: {'answer':'Python', 'citation_ids':ids})


def test_low_similarity_refuses_without_model_call():
    result = qa.answer_from_evidence('驾驶证？', [evidence()], ranker=lambda q,t:[0.0],
        generator=lambda *a: pytest.fail('unrelated evidence'))
    assert result['status'] == 'insufficient_evidence'


def test_model_errors_are_not_success():
    def broken(*args):
        raise RuntimeError('provider failed')
    with pytest.raises(qa.QAError):
        qa.answer_from_evidence('技能？', [evidence()], ranker=lambda q,t:[1.0], generator=broken)
