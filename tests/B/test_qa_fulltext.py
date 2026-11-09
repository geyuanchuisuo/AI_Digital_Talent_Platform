import pytest
from test_round1_versions import db
from test_round2_processing import bundle
from app.models.resume import ResumePage
from app.modules.talents.history import save_version, read_version


def test_source_pages_are_frozen_per_version_and_full_text_is_retrievable(db):
    p,f,_=bundle(db)
    text='项目详细经历。'*250+'末尾证据：参与星河数据迁移。'
    page=ResumePage(resume_file_id=f.id,page_no=2,text_content=text,source_type='text',quality_status='ready')
    db.add(page);db.flush()
    v1=save_version(db,p,'baseline');db.commit()
    page.text_content='不同版本：教育经历';db.flush()
    v2=save_version(db,p,'reparse');db.commit()
    from app.modules.talents.qa_sources import chunks
    rows=chunks(read_version(db,p.id,v1))
    assert any('星河数据迁移' in row['snippet'] for row in rows)
    assert all(row['page_no']==2 for row in rows)
    assert not any('不同版本' in row['snippet'] for row in rows)
    assert any('不同版本' in row['snippet'] for row in chunks(read_version(db,p.id,v2)))


def test_legacy_versions_do_not_read_current_pages():
    from app.modules.talents.qa_sources import chunks
    e={'file_id':3,'page_no':1,'snippet':'旧版引用'}
    assert chunks({'evidence':[e]})==[e]
    assert chunks({'source_pages':[],'evidence':[e]})==[e]


def test_batch_authorizes_every_person_before_model_or_writes(monkeypatch):
    from app.modules.talents import qa_service
    from app.core.errors import BizError
    def snapshot(db,person_id,**kwargs):
        if person_id==2: raise BizError(403,'拒绝')
        return {'status':'ready'}
    monkeypatch.setattr(qa_service,'get_person_snapshot',snapshot)
    monkeypatch.setattr(qa_service,'ask',lambda *a,**k:pytest.fail('must preauthorize all'))
    with pytest.raises(BizError):
        qa_service.ask_batch(None,[1,2],'技能？',user_id=1,scope=None)
