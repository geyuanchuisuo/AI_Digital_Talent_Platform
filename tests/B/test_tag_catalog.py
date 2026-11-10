from app.modules.talents import tag_catalog


def test_catalog_supports_cross_domain_selection_with_stable_categories():
    rows = tag_catalog.catalog()['items']
    by_name = {row['name']: row for row in rows}
    assert by_name['Python']['type'] == 'skill'
    assert by_name['制造业']['type'] == 'industry'
    assert by_name['高级工程师']['type'] == 'level'
    assert by_name['跨部门协作']['type'] == 'general'
    assert len(rows) >= 100 and len(by_name) == len(rows)


def test_suggestions_have_real_versioned_source_and_do_not_invent_skills():
    snapshot = {'person_id': 7, 'profile_version': 3, 'source_pages': [
        {'file_id': 9, 'filename': 'synthetic.pdf', 'page_no': 2,
         'text': '在制造业负责 Python 项目，使用 PostgreSQL。'}]}
    result = tag_catalog.suggest(snapshot)
    by_name = {row['name']: row for row in result['items']}
    assert {'Python', 'PostgreSQL', '制造业'}.issubset(by_name)
    assert 'Java' not in by_name
    assert by_name['Python']['file_id'] == 9 and by_name['Python']['page_no'] == 2
    assert by_name['Python']['matched_text'] == 'Python'
    assert result['profile_version'] == 3
    assert all(row['review_status'] == 'pending_review' for row in result['items'])


def test_aliases_and_boundaries_do_not_mistake_google_for_go_or_java_for_javascript():
    result = tag_catalog.suggest({'person_id': 7, 'profile_version': 1, 'source_pages': [
        {'file_id': 9, 'page_no': None, 'text': 'Google JavaScript golang，熟悉Python'}]})
    by_name = {row['name']: row for row in result['items']}
    assert 'Java' not in by_name
    assert by_name['Go']['matched_text'] == 'golang'
    assert by_name['Python']['matched_text'] == 'Python'
    assert tag_catalog.suggest({'person_id': 7, 'profile_version': 1, 'source_pages': []})['items'] == []


def test_authenticated_limited_hr_can_read_public_vocabulary_without_person_access(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace as N
    from app.api.deps import get_current_user
    from app.api.talents import router
    from app.core.errors import register_exception_handlers
    from app.core.auth import scope as auth
    app = FastAPI(); app.include_router(router); register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: N(id=7, role='hr', person_id=None)
    monkeypatch.setattr(auth, '_management_range', lambda *a: (False, (), (), ()))
    with TestClient(app) as client:
        response = client.get('/api/talents/tag-catalog')
    assert response.status_code == 200
    assert response.json()['data']['version'] == 'talent-tags-v1'


def test_independent_manual_skill_tag_survives_an_unrelated_skills_edit():
    from app.models.talent import TalentTag
    from app.service.talent_service import build_tag_rows
    rows = build_tag_rows([TalentTag(tag='rust', tag_type='skill', source='manual'),
        TalentTag(tag='python', tag_type='skill', source='manual')],
        requested=None, skills_final=['go'], skills_before=['python'])
    assert ('rust', 'skill', 'manual') in rows
    assert ('python', 'skill', 'manual') not in rows
    assert ('go', 'skill', 'manual') in rows
