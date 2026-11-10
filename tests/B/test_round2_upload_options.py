from types import SimpleNamespace
from test_round1_access import client
from app.api.deps import get_current_user
from app.config import settings


def test_upload_capabilities_require_management_scope(client):
    assert client.get('/api/resumes/upload-options').status_code == 403


def test_capabilities_follow_enabled_ocr_and_the_deployment_whitelist(client, monkeypatch):
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7, role='hr', person_id=None)
    monkeypatch.setattr(settings, 'upload_allowed_ext', 'pdf,docx,png,jpg,jpeg')
    monkeypatch.setattr(settings, 'upload_max_mb', 12)
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    response = client.get('/api/resumes/upload-options')
    assert response.status_code == 200
    data = response.json()['data']
    assert 'png' in data['allowed_ext'] and data['max_mb'] == 12
    monkeypatch.setattr(settings, 'ocr_enabled', False)
    assert 'png' not in client.get('/api/resumes/upload-options').json()['data']['allowed_ext']
