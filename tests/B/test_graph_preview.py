"""Current SQL authorization remains authoritative for all graph evidence previews."""
from datetime import datetime, timedelta

import pytest

from test_round1_versions import db
from test_round2_processing import bundle
from test_knowledge_service import knowledge
from app.core.auth.scope import AccountScope
from app.core.errors import BizError
from app.modules.talents.history import save_version
from app.modules.files.processing import store_pages
from app.service.extract_service import ExtractedPage

SCOPE=AccountScope('hr',None,True,'admin')


def prepared(db):
    person,file,_=bundle(db)
    store_pages(db,file.id,[ExtractedPage(1,'使用Python开发。','text',None,'ready')])
    save_version(db,person,'baseline');db.commit()
    return person,file


def test_profile_preview_is_read_only_pinned_and_does_not_call_graph(db,monkeypatch):
    from app.modules.talents import graph_preview
    person,_=prepared(db)
    monkeypatch.setattr(graph_preview,'current_scope',lambda *a:SCOPE)
    result=graph_preview.preview(db,person.id,user_id=7)
    assert result['neo4j_state']=='not_connected'
    assert result['root']['source_version']==1 and result['edges']
    assert person.tags_json is None  # preview does not assign inferred labels


def test_reauthorization_after_projection_cannot_return_revoked_sources(db,monkeypatch):
    from app.modules.talents import graph_preview
    person,_=prepared(db)
    real=graph_preview.profile_projection
    monkeypatch.setattr(graph_preview,'current_scope',lambda *a:SCOPE)
    def revoke(saved):
        result=real(saved)
        monkeypatch.setattr(graph_preview,'current_scope',lambda *a:AccountScope('personal',None,False,'public'))
        return result
    monkeypatch.setattr(graph_preview,'profile_projection',revoke)
    with pytest.raises(BizError) as error:graph_preview.preview(db,person.id,user_id=7)
    assert error.value.code==403


def published(knowledge):
    from app.modules.knowledge.models import KnowledgeChunk
    db,service=knowledge
    doc=service.create_document(db,'合成制度','public',user_id=7)
    revision=service.upload(db,doc.id,'synthetic.txt','使用Python。'.encode(),user_id=7)
    revision.status=revision.stage='completed';revision.index_token='synthetic'
    db.add(KnowledgeChunk(revision_id=revision.id,chunk_no=0,page_no=None,source_type='txt',
        offset_start=0,offset_end=9,text='使用Python。'))
    db.commit();service.publish(db,doc.id,revision.id,user_id=7)
    return db,service,doc,revision


def test_policy_preview_only_uses_current_published_complete_authorized_revision(knowledge):
    from app.modules.knowledge.graph_preview import preview
    db,service,doc,revision=published(knowledge)
    result=preview(db,doc.id,user_id=8)
    assert result['root']['revision_id']==revision.id
    assert result['edges'][0]['evidence'][0]['document_id']==doc.id
    service.update_document(db,doc.id,user_id=7,access_scope='admin')
    with pytest.raises(BizError) as error:preview(db,doc.id,user_id=8)
    assert error.value.code==403


@pytest.mark.parametrize('change',['disabled','deleted','expired','draft'])
def test_unavailable_policies_never_export_sources(knowledge,change):
    from app.modules.knowledge.graph_preview import preview
    db,service,doc,revision=published(knowledge)
    if change=='disabled':doc.enabled=False
    if change=='deleted':doc.deleted_at=datetime.now()
    if change=='expired':doc.expires_at=datetime.now()-timedelta(seconds=1)
    if change=='draft':doc.published_revision_id=None
    db.commit()
    with pytest.raises(BizError):preview(db,doc.id,user_id=8)


def test_publication_change_during_projection_is_rejected(knowledge,monkeypatch):
    from app.modules.knowledge import graph_preview
    db,service,doc,revision=published(knowledge)
    real=graph_preview.policy_projection
    def unpublish(**kwargs):
        result=real(**kwargs);doc.published_revision_id=None;db.commit();return result
    monkeypatch.setattr(graph_preview,'policy_projection',unpublish)
    with pytest.raises(BizError):graph_preview.preview(db,doc.id,user_id=8)
