"""B-owned Milvus chunk collection. Call only after current profile authorization."""
import hashlib
import json
import math
from app.config import settings
from app.modules.talents.qa_sources import chunks
from app.modules.talents.qa import QAError


def collection_name():
    return settings.milvus_collection+'_qa_chunks_v1'


def retrieve(snapshot, question):
    from app.core.embedding_client import embed_texts, embed_query
    from app.core.milvus_store import get_store
    rows=chunks(snapshot)
    if not rows:return []
    person_id=int(snapshot['person_id']);version=int(snapshot['profile_version'])
    digest=hashlib.sha256(json.dumps([settings.embedding_model,settings.embedding_dim,rows],
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    ids=[int.from_bytes(hashlib.sha256(f'{person_id}:{version}:{digest}:{i}'.encode()).digest()[:8], 'big')&((1<<63)-1)
         for i in range(len(rows))]
    lookup=dict(zip(ids,rows))
    client=get_store().client;name=collection_name()
    expression=f'person_id == {person_id} and profile_version == {version} and source_digest == "{digest}"'
    try:
        if not client.has_collection(collection_name=name):
            try:
                client.create_collection(collection_name=name,dimension=settings.embedding_dim,
                    metric_type='COSINE',auto_id=False,enable_dynamic_field=True,consistency_level='Strong')
            except Exception:
                if not client.has_collection(collection_name=name):raise
        count=client.query(collection_name=name,filter=expression,output_fields=['count(*)'],consistency_level='Strong')
        if not count or int(count[0]['count(*)'])!=len(rows):
            # Partial failures are not cached as complete; deterministic IDs make retries idempotent.
            for start in range(0,len(rows),16):
                block=rows[start:start+16]
                vectors=embed_texts([row['snippet'] for row in block])
                if len(vectors)!=len(block):raise QAError('索引向量数量异常')
                client.upsert(collection_name=name,data=[dict(id=ids[start+i],vector=vector,
                    person_id=person_id,profile_version=version,source_digest=digest)
                    for i,vector in enumerate(vectors)])
        hits=client.search(collection_name=name,data=[embed_query(question)],filter=expression,
            limit=8,search_params={'metric_type':'COSINE'},consistency_level='Strong')
        output=[]
        for hit in hits[0] if hits else []:
            score=float(hit['distance']);key=int(hit['id'])
            if key not in lookup or not math.isfinite(score):raise QAError('索引结果不属于当前档案版本')
            output.append(dict(lookup[key],_score=score))
        return output
    except QAError:raise
    except Exception as exc:raise QAError('档案片段索引暂不可用') from exc


def remove_person(person_id):
    from app.core.milvus_store import get_store
    client=get_store().client;name=collection_name()
    if client.has_collection(collection_name=name):
        client.delete(collection_name=name,filter=f'person_id == {int(person_id)}')
