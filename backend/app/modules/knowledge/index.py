"""Milvus knowledge collection; DB-authorized revision/attempt filtering first."""
from hashlib import sha256
import json
import math
from sqlalchemy import select
from app.config import settings
from app.core.errors import BizError
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument


def _client():
    from app.core.milvus_store import get_store
    return get_store().client


def collection():
    return settings.milvus_collection + '_knowledge_chunks_v1'


def ensure(client):
    name = collection()
    if not client.has_collection(collection_name=name):
        try:
            client.create_collection(collection_name=name, dimension=settings.embedding_dim,
                metric_type='COSINE', auto_id=False, enable_dynamic_field=True, consistency_level='Strong')
        except Exception:
            if not client.has_collection(collection_name=name):
                raise


def upsert(revision, chunks, token, *, guard):
    from app.core.embedding_client import embed_texts
    client = _client()
    ensure(client)
    for start in range(0, len(chunks), 16):
        guard()
        block = chunks[start:start + 16]
        vectors = embed_texts([row.text for row in block])
        if len(vectors) != len(block):
            raise BizError(503, '知识向量数量异常')
        guard()
        client.upsert(collection_name=collection(), data=[{
            'id': int.from_bytes(sha256(f'{row.id}:{token}'.encode()).digest()[:8], 'big') & ((1 << 63) - 1),
            'vector': vector, 'document_id': revision.document_id, 'revision_id': revision.id,
            'chunk_id': row.id, 'index_token': token,
        } for row, vector in zip(block, vectors)])


def remove_revision(revision_id):
    client = _client()
    if client.has_collection(collection_name=collection()):
        client.delete(collection_name=collection(), filter=f'revision_id == {int(revision_id)}')


def search(db, question, revisions, *, authorize=None):
    if not revisions:
        return []
    from app.core.embedding_client import embed_query
    client = _client()
    # An unavailable collection is not an empty business result.
    if not client.has_collection(collection_name=collection()):
        raise BizError(503, '知识索引尚不可用')
    vector = embed_query(question)
    # Embedding may be slow; build the filter from fresh authorization afterwards.
    if authorize is not None:
        revisions = authorize()
    if not revisions:
        return []
    permitted = {r.id: (r.index_token, r.document_id, r.version, r.filename) for r in revisions}
    expression = ' or '.join(f'(revision_id == {rid} and index_token == {json.dumps(values[0])})'
        for rid, values in permitted.items())
    hits = client.search(collection_name=collection(), data=[vector], filter=expression,
        limit=8, output_fields=['chunk_id', 'revision_id', 'index_token'],
        search_params={'metric_type': 'COSINE'}, consistency_level='Strong')
    output = []
    seen = set()
    for hit in hits[0] if hits else []:
        entity = hit.get('entity', {})
        revision_id, chunk_id = entity.get('revision_id'), entity.get('chunk_id')
        if revision_id not in permitted or entity.get('index_token') != permitted[revision_id][0]:
            continue
        score = float(hit.get('distance', 0))
        if not math.isfinite(score) or score < 0.35 or chunk_id in seen:
            continue
        chunk = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.revision_id == revision_id))
        if chunk is None:
            continue
        _, doc_id, version, filename = permitted[revision_id]
        document = db.get(KnowledgeDocument, doc_id)
        output.append({'chunk_id': chunk.id, 'document_id': doc_id, 'revision_id': revision_id,
            'version': version, 'document_title': document.title, 'filename': filename,
            'page_no': chunk.page_no, 'source_type': chunk.source_type, 'offset_start': chunk.offset_start,
            'offset_end': chunk.offset_end, 'snippet': chunk.text, 'index_token': entity['index_token']})
        seen.add(chunk_id)
    return output
