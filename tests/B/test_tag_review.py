"""Source-backed tag review must stay attached to current identity and history."""
import json
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from test_round1_versions import db
from test_round2_processing import bundle
from app.core.auth.scope import AccountScope
from app.core.errors import BizError
from app.models.talent import TalentProfileEdit
from app.schemas.talent import TalentPatchRequest
from app.service import talent_service as svc
from app.modules.talents.history import save_version, current_version
from app.modules.files.processing import store_pages
from app.service.extract_service import ExtractedPage
from app.modules.talents import tag_catalog

SCOPE = AccountScope('hr', None, True, 'admin')


@pytest.fixture
def reviewed(db, monkeypatch):
    person, file, _ = bundle(db)
    store_pages(db, file.id, [ExtractedPage(1, '使用Go开发服务，参与制造业项目。', 'text', None, 'ready')])
    save_version(db, person, 'baseline');db.commit()
    monkeypatch.setattr(svc, 'sync_profile_vector', lambda *a, **k: None)
    monkeypatch.setattr(svc, '_reload_profile', lambda pid: db.get(type(person), pid))
    saved = svc.get_person_snapshot(db, person.id, scope=SCOPE)
    item = next(r for r in tag_catalog.suggest(saved)['items'] if r['name']=='Go')
    proof = dict(code=item['code'],candidate_id=item['candidate_id'],profile_version=saved['profile_version'],
                 catalog_version=tag_catalog.VERSION,reason='')
    return person, file, proof


def patch(db, person, proof, **kwargs):
    return svc.patch_profile(db, person.id, TalentPatchRequest(tags=[{'tag':'Go','tag_type':'skill'}],
        tag_proofs=[proof]), user_id=7, scope=SCOPE, **kwargs)


def test_proof_and_tag_are_committed_together_and_quality_does_not_claim_ability(db, reviewed):
    from app.modules.talents.tag_review import quality
    person, file, proof = reviewed
    patch(db, person, proof)
    record = db.scalar(select(TalentProfileEdit).where(TalentProfileEdit.field_name=='_tag_review_v1'))
    saved = json.loads(record.new_value)
    assert saved['source_profile_version']==1 and saved['result_profile_version']==2
    assert saved['evidence'][0]['file_id']==file.id and saved['tag']=='go'
    result = quality(db, person.id, scope=SCOPE)
    item = next(r for r in result['items'] if r['tag']=='go')
    assert item['evidence_status']=='reviewed_source'
    assert item['ability_verified'] is False
    assert item['evidence'][0]['snippet']


@pytest.mark.parametrize('kind', ['forged', 'old_version', 'wrong_catalog', 'wrong_type', 'cross_person'])
def test_forged_and_stale_proofs_cannot_write_tags(db, reviewed, kind):
    person, _, proof = reviewed
    if kind in ('forged','cross_person'): proof={**proof,'candidate_id':'b'*64}
    if kind=='old_version':
        person.city='新城';save_version(db, person, 'manual_edit', changed_by=7);db.commit()
    if kind=='wrong_catalog':proof={**proof,'catalog_version':'unapproved-v2'}
    if kind=='wrong_type':proof={**proof,'code':'industry:Go'}
    with pytest.raises(BizError) as error:
        patch(db, person, proof)
    assert error.value.code in (409,422)
    db.rollback()
    assert 'go' not in (person.tags_json or [])


def test_proof_cannot_be_applied_without_authorized_scope(db, reviewed):
    person, _, proof=reviewed
    with pytest.raises(BizError) as error:
        svc.patch_profile(db,person.id,TalentPatchRequest(tags=['Go'],tag_proofs=[proof]),
            user_id=7,scope=AccountScope('personal',person.id+1,False,'public'))
    assert error.value.code==403


def test_nonpositive_context_requires_human_reason_and_records_it(db, reviewed):
    person, file, _ = reviewed
    store_pages(db,file.id,[ExtractedPage(1,'计划学习Go，尚未使用。','text',None,'ready')])
    save_version(db,person,'reparse');db.commit()
    saved=svc.get_person_snapshot(db,person.id,scope=SCOPE)
    item=next(r for r in tag_catalog.suggest(saved)['items'] if r['name']=='Go')
    proof=dict(code=item['code'],candidate_id=item['candidate_id'],profile_version=saved['profile_version'],
               catalog_version=tag_catalog.VERSION,reason='')
    with pytest.raises(BizError) as error:patch(db,person,proof)
    assert error.value.code==422
    patch(db,person,{**proof,'reason':'仅登记学习意向，不认定已具备技能'})
    from app.modules.talents.tag_review import quality
    private=quality(db,person.id,scope=AccountScope('personal',person.id,False,'public'))
    item=next(r for r in private['items'] if r['tag']=='go')
    assert 'reason' not in item and 'reviewed_by' not in item


def test_removal_and_manual_readdition_cannot_resurrect_source_confirmation(db, reviewed):
    from app.modules.talents.tag_review import quality
    person, _, proof=reviewed
    patch(db,person,proof)
    svc.patch_profile(db,person.id,TalentPatchRequest(tags=[]),user_id=7)
    svc.patch_profile(db,person.id,TalentPatchRequest(tags=[{'tag':'Go','tag_type':'skill'}]),user_id=7)
    item=next(r for r in quality(db,person.id,scope=SCOPE)['items'] if r['tag']=='go')
    assert item['evidence_status']=='needs_reconfirmation' and item['evidence']==[]


def test_noop_confirmation_is_saved_without_vector_or_duplicate_audit(db, reviewed, monkeypatch):
    person, _, _=reviewed
    svc.patch_profile(db,person.id,TalentPatchRequest(tags=[{'tag':'Go','tag_type':'skill'}]),user_id=7)
    saved=svc.get_person_snapshot(db,person.id,scope=SCOPE)
    item=next(r for r in tag_catalog.suggest(saved)['items'] if r['name']=='Go')
    proof=dict(code=item['code'],candidate_id=item['candidate_id'],profile_version=saved['profile_version'],
               catalog_version=tag_catalog.VERSION,reason='')
    monkeypatch.setattr(svc,'sync_profile_vector',lambda *a,**k:pytest.fail('no-op must not re-embed'))
    before=current_version(db,person.id).version
    from app.dao.talent_profiles import get_tag_rows
    payload=TalentPatchRequest(tags=[{'tag':r.tag,'tag_type':r.tag_type} for r in get_tag_rows(db,person.id)],tag_proofs=[proof])
    svc.patch_profile(db,person.id,payload,user_id=7,scope=SCOPE)
    svc.patch_profile(db,person.id,payload,user_id=7,scope=SCOPE)
    assert current_version(db,person.id).version==before
    assert len(db.scalars(select(TalentProfileEdit).where(TalentProfileEdit.field_name=='_tag_review_v1')).all())==1


def test_audit_failure_rolls_back_tag_changes(db, reviewed, monkeypatch):
    from app.modules.talents import tag_review
    person, _, proof=reviewed
    def fail(*a):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(tag_review,'persist',fail)
    with pytest.raises(RuntimeError):patch(db,person,proof)
    db.rollback()
    assert 'go' not in (person.tags_json or [])
    assert current_version(db,person.id).version==1


def test_detached_source_does_not_expose_old_confirmation(db, reviewed):
    from app.modules.talents.tag_review import quality
    from app.models.resume import ParsedResume
    person, file, proof=reviewed
    patch(db,person,proof)
    db.scalar(select(ParsedResume).where(ParsedResume.resume_file_id==file.id)).talent_profile_id=None
    save_version(db,person,'reparse');db.commit()
    item=next(r for r in quality(db,person.id,scope=SCOPE)['items'] if r['tag']=='go')
    assert item['evidence_status']=='needs_reconfirmation' and not item['evidence']


def test_reattached_source_cannot_resurrect_old_review(db, reviewed):
    from app.modules.talents.tag_review import quality
    from app.models.resume import ParsedResume
    person, file, proof=reviewed
    patch(db,person,proof)
    parsed=db.scalar(select(ParsedResume).where(ParsedResume.resume_file_id==file.id))
    parsed.talent_profile_id=None;save_version(db,person,'reparse');db.commit()
    parsed.talent_profile_id=person.id;save_version(db,person,'reparse');db.commit()
    item=next(r for r in quality(db,person.id,scope=SCOPE)['items'] if r['tag']=='go')
    assert item['evidence_status']=='needs_reconfirmation' and item['evidence']==[]


def test_reviewing_existing_ai_tag_promotes_manual_protection(db, reviewed):
    from app.models.talent import TalentTag
    person, _, _=reviewed
    db.add(TalentTag(talent_profile_id=person.id,tag='go',tag_type='skill',source='llm'))
    person.tags_json=['go'];save_version(db,person,'reparse');db.commit()
    saved=svc.get_person_snapshot(db,person.id,scope=SCOPE)
    item=next(r for r in tag_catalog.suggest(saved)['items'] if r['name']=='Go')
    proof=dict(code=item['code'],candidate_id=item['candidate_id'],profile_version=saved['profile_version'],
               catalog_version=tag_catalog.VERSION,reason='')
    patch(db,person,proof)
    tag=db.scalar(select(TalentTag).where(TalentTag.talent_profile_id==person.id,TalentTag.tag=='go'))
    assert tag.source=='manual' and 'tags' in person.manual_edited_fields
