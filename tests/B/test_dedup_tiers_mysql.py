"""Opt-in genuine MySQL locks/auth; models/vector are not called."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.skipif(
    os.getenv('B04_MYSQL') != '1' or not os.getenv('MYSQL_DB', '').startswith('ai_talent_b04_'),
    reason='opt-in fresh isolated B04 MySQL database required')


@pytest.fixture(autouse=True)
def deactivate_owned_fixtures():
    yield
    from datetime import datetime
    from sqlalchemy import update
    from app.database import SessionLocal
    from app.models import User, TalentProfile
    with SessionLocal() as db:
        db.execute(update(User).where(User.username.like('b04_%')).values(is_active=0))
        db.execute(update(TalentProfile).where(TalentProfile.name == '合成并发样例').values(deleted_at=datetime.now()))
        db.commit()


def test_real_mysql_parallel_auto_merge_and_current_role_revocation(monkeypatch):
    from app.database import SessionLocal
    from app.models import User, TalentProfile, ResumeFile, ParsedResume, ParseTask, TalentDuplicate
    from app.models.talent import TalentProfileEdit
    from app.modules.system.models import SysRole, SysUserRole
    from app.service import dedup_service as svc, profile_service
    tag = uuid4().hex[:12]
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: None)
    with SessionLocal() as db:
        user = User(username='b04_' + tag, password_hash='synthetic-disabled-login', role='hr',
                    data_scope_mode='scoped', is_active=1)
        role = db.scalar(select(SysRole).where(SysRole.code == 'hr'))
        assert role is not None and role.data_scope == 'all'
        person = TalentProfile(name='合成并发样例', phone='13800000000', email=tag+'@example.test')
        db.add_all([user, person]); db.flush()
        binding = SysUserRole(user_id=user.id, role_id=role.id)
        db.add(binding); db.commit()
        uid, pid, binding_id = user.id, person.id, binding.id

        def incoming():
            file = ResumeFile(filename='synthetic-b04.pdf', stored_path='synthetic/unused',
                file_hash=uuid4().hex*2, file_ext='pdf', file_size=20,
                upload_batch_id=uuid4().hex[:26], uploaded_by=uid)
            db.add(file); db.flush()
            parsed = ParsedResume(resume_file_id=file.id, raw_text=f'合成并发样例 13800000000 {tag}@example.test',
                structured_json={'basic': {'name': '合成并发样例', 'phone': '13800000000',
                    'email': tag+'@example.test'}, 'skills': ['Go']})
            db.add_all([parsed, ParseTask(resume_file_id=file.id, status='running', stage='profile')])
            db.commit()
            match = svc.detect(db, parsed)
            assert match.confidence == 'high'
            return svc.flag(db, parsed, match).id

        did = incoming()
    gate = Barrier(2)

    def compete(_):
        with SessionLocal() as db:
            gate.wait(timeout=10)
            result = svc.try_auto_merge(db, did)
            db.commit()  # Release locks on the deliberate no-op losing request.
            return result is not None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, range(2)))
        assert sorted(results) == [False, True]
        with SessionLocal() as db:
            assert db.get(TalentDuplicate, did).status == 'merged'
            assert db.scalar(select(func.count()).select_from(TalentProfileEdit).where(
                TalentProfileEdit.talent_profile_id == pid,
                TalentProfileEdit.field_name == '_merge_journal_v1')) == 1
            svc.undo_merge(db, did, user_id=uid, reason='合成撤销演练')
            assert svc.try_auto_merge(db, did) is None
            db.commit()
            # A separate high-confidence source must still fail when the last role is revoked.
        with SessionLocal() as db:
            file = ResumeFile(filename='synthetic-revoked.pdf', stored_path='synthetic/unused',
                file_hash=uuid4().hex*2, file_ext='pdf', file_size=20,
                upload_batch_id=uuid4().hex[:26], uploaded_by=uid)
            db.add(file); db.flush()
            parsed = ParsedResume(resume_file_id=file.id, raw_text=f'合成并发样例 13800000000 {tag}@example.test',
                structured_json={'basic': {'name': '合成并发样例', 'phone': '13800000000', 'email': tag+'@example.test'}})
            db.add_all([parsed, ParseTask(resume_file_id=file.id, status='running', stage='profile')]); db.commit()
            row = svc.flag(db, parsed, svc.detect(db, parsed))
            db.delete(db.get(SysUserRole, binding_id)); db.commit()
            assert svc.try_auto_merge(db, row.id) is None
            assert row.status == 'pending' and parsed.talent_profile_id is None
    finally:
        with SessionLocal() as db:
            db.get(User, uid).is_active = 0
            db.commit()
