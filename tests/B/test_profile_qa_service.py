from types import SimpleNamespace
import pytest
from sqlalchemy import MetaData, Integer, select
from test_round1_versions import db
from test_round2_processing import bundle
from app.modules.talents.qa_models import ProfileQuestion
from app.modules.talents import qa_service, qa
from app.core.auth.scope import AccountScope
from app.models.resume import ResumePage
from app.modules.talents.history import save_version


def prepare(db):
    metadata=MetaData()
    table=ProfileQuestion.__table__.to_metadata(metadata)
    table.c.id.type=Integer()
    metadata.create_all(db.get_bind())
    profile,source,task=bundle(db)
    db.add(ResumePage(resume_file_id=source.id,page_no=1,text_content='张晓林 Python',
                      source_type='text',quality_status='ready'))
    save_version(db,profile,'baseline');db.commit()
    return profile


def test_authorization_precedes_embedding_and_audit(db,monkeypatch):
    p=prepare(db)
    monkeypatch.setattr(qa,'_rank',lambda *a:pytest.fail('must authorize first'))
    with pytest.raises(Exception):
        qa_service.ask(db,p.id,'技能？',user_id=7,scope=AccountScope('personal',p.id+1,False,'public'))
    assert db.scalars(select(ProfileQuestion)).all()==[]


def test_answer_audit_is_bound_to_actual_version_and_person(db,monkeypatch):
    p=prepare(db)
    monkeypatch.setattr(qa_service,'retrieve',lambda s,q:[dict(row,_score=1.0) for row in s['evidence'][:1]])
    from app.core import llm_client
    monkeypatch.setattr(llm_client,'chat_json',lambda *a,**k:qa.Answer(answer='熟悉 Python',citation_ids=[1]))
    result=qa_service.ask(db,p.id,'技能？',user_id=7,scope=AccountScope('personal',p.id,False,'public'))
    row=db.get(ProfileQuestion,result['question_id'])
    assert row.person_id==p.id and row.profile_version==1 and row.asked_by==7
    assert row.snapshot_json['result']['citations'][0]['snippet']
    assert row.snapshot_json['system_prompt']==qa.SYSTEM_PROMPT
    assert result['status']=='answered'


def test_provider_failure_is_durable_and_not_answered(db,monkeypatch):
    p=prepare(db)
    monkeypatch.setattr(qa_service,'retrieve',lambda *a:(_ for _ in ()).throw(qa.QAError('failed')))
    from app.core.errors import BizError
    with pytest.raises(BizError) as error:
        qa_service.ask(db,p.id,'技能？',user_id=7,scope=AccountScope('personal',p.id,False,'public'))
    assert error.value.code==503
    assert db.scalar(select(ProfileQuestion)).status=='failed'
