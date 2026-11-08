from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import talents, resumes
from app.api.deps import get_current_user
from app.core.auth.scope import AccountScope
from app.core.errors import register_exception_handlers
from app.database import get_db
from app.service import talent_service


@pytest.fixture
def client():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(talents.router)
    app.include_router(resumes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7, role='personal', person_id=10)
    app.dependency_overrides[get_db] = lambda: object()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize('method,path,body', [
    ('get', '/api/talents/11', None),
    ('get', '/api/talents/11/resume-file?resume_file_id=1', None),
    ('get', '/api/talents/11/snapshot?version=1', None),
    ('get', '/api/talents/11/evidence?version=1', None),
    ('patch', '/api/talents/10', {'city': 'Shanghai'}),
    ('delete', '/api/talents/10', None),
    ('post', '/api/talents/10/restore', None),
    ('post', '/api/talents/10/revector', None),
    ('get', '/api/talents', None),
    ('get', '/api/talents/search?query=python', None),
    ('get', '/api/talents/duplicates', None),
    ('get', '/api/resumes/queue', None),
    ('get', '/api/resumes/batches', None),
    ('post', '/api/resumes/queue/1/retry', None),
])
def test_personal_denied_before_data_access(client, method, path, body):
    # A sentinel DB proves authorization occurs before any read, write or model call.
    response = client.request(method, path, json=body)
    assert response.status_code == 403, response.text


def test_snapshot_accepts_public_scope_and_denies_other_person():
    scope = AccountScope('personal', 10, False, 'public')
    with pytest.raises(talent_service.SnapshotScopeError):
        talent_service.get_person_snapshot(None, 11, scope=scope)


def test_restricted_management_scope_cannot_read_arbitrary_person():
    scope = AccountScope('hr', None, False, 'admin', department_ids=(1,))
    with pytest.raises(talent_service.SnapshotScopeError):
        talent_service.get_person_snapshot(None, 11, scope=scope)
