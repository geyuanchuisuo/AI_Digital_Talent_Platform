from types import SimpleNamespace
import pytest
from app.modules.talents import qa_index


class Client:
    def __init__(self):self.exists=False;self.rows=[];self.filters=[];self.bad=False
    def has_collection(self,**kw):return self.exists
    def create_collection(self,**kw):self.exists=True
    def query(self,**kw):return [{'count(*)':len(self.rows)}]
    def upsert(self,**kw):self.rows=kw['data']
    def search(self,**kw):
        self.filters.append(kw['filter'])
        return [[{'id':999 if self.bad else row['id'],'distance':0.9} for row in self.rows[:8]]]


def test_index_reuses_persisted_chunks_and_filters_person_version_digest(monkeypatch):
    from app.core import embedding_client,milvus_store
    client=Client();calls=[]
    monkeypatch.setattr(milvus_store,'get_store',lambda:SimpleNamespace(client=client))
    monkeypatch.setattr(embedding_client,'embed_texts',lambda texts:calls.append(texts) or [[1.0,0.0]]*len(texts))
    monkeypatch.setattr(embedding_client,'embed_query',lambda question:[1.0,0.0])
    snapshot={'person_id':12,'profile_version':3,'source_pages':[{'file_id':7,'page_no':2,'text':'原文。'*300}]}
    first=qa_index.retrieve(snapshot,'原文？');second=qa_index.retrieve(snapshot,'原文？')
    assert first==second and len(calls)==1
    assert all('person_id == 12 and profile_version == 3 and source_digest ==' in f for f in client.filters)
    client.bad=True
    with pytest.raises(qa_index.QAError,match='不属于'):qa_index.retrieve(snapshot,'原文？')


def test_batch_reports_failure_and_insufficient_separately(monkeypatch):
    from app.modules.talents import qa_service
    from app.core.errors import BizError
    monkeypatch.setattr(qa_service,'get_person_snapshot',lambda *a,**k:{'status':'ready'})
    def ask(db,pid,*args,**kw):
        if pid==2:raise BizError(503,'不可用')
        return {'person_id':pid,'status':'answered' if pid==1 else 'insufficient_evidence'}
    monkeypatch.setattr(qa_service,'ask',ask)
    result=qa_service.ask_batch(SimpleNamespace(rollback=lambda:None),[1,2,3,1],'技能？',user_id=1,scope=None)
    assert result['requested']==3 and result['answered']==1 and result['partial']
    assert [i['status'] for i in result['items']]==['answered','failed','insufficient_evidence']
