"""Authorized, version-bound profile QA with durable request outcomes."""
from app.config import settings
from app.core.errors import BizError
from app.service.talent_service import get_person_snapshot
from app.modules.talents.qa_models import ProfileQuestion
from app.modules.talents import qa
from app.modules.talents.qa_index import retrieve


def ask(db, person_id, question, *, user_id, scope, version=None):
    # Authorization and current deletion are checked before any model or source access.
    snapshot = get_person_snapshot(db, person_id, version=version, scope=scope)
    if snapshot['status'] != 'ready':
        raise BizError(409, '档案尚不可用，不能问答')
    if not question.strip() or len(question) > 1000:
        raise BizError(422, '问题需要1至1000个字符')
    trace = dict(question=question, prompt_version=qa.PROMPT_VERSION,
                 system_prompt=qa.SYSTEM_PROMPT, model=settings.llm_model,
                 embedding_model=settings.embedding_model, retrieval_strategy='milvus_cosine',
                 retrieval_threshold=0.35)
    record = ProfileQuestion(person_id=person_id, profile_version=snapshot['profile_version'],
        asked_by=user_id, status='running', snapshot_json=trace)
    db.add(record); db.commit()
    calls = []
    def generate(q, context):
        trace['context'] = context
        from app.core.llm_client import chat_json
        import json
        output = chat_json(json.dumps({'question':q, 'evidence':context}, ensure_ascii=False),
            schema=qa.Answer, system_prompt=qa.SYSTEM_PROMPT, on_result=calls.append,
            purpose="talent_qa")
        return output.model_dump()
    try:
        candidates = retrieve(snapshot, question)
        unique = {}
        for row in candidates:
            key = (row['file_id'],row.get('page_no'),row['snippet'])
            if key not in unique or row['_score'] > unique[key]['_score']:
                unique[key] = row
        candidates = list(unique.values())
        scores = [row.pop('_score') for row in candidates]
        trace['source_coverage'] = 'frozen_pages' if snapshot.get('source_pages') else 'legacy_evidence'
        result = qa.answer_from_evidence(question, candidates,
            ranker=lambda q,texts:scores, generator=generate)
    except Exception as exc:
        record.status = 'failed'
        record.snapshot_json = {**trace, 'model_calls':calls, 'error':'问答处理失败', 'error_type':type(exc).__name__}
        db.commit()
        raise BizError(503, '档案问答暂不可用，请稍后重试')
    record.status = result['status']
    record.snapshot_json = {**trace, 'result':result, 'model_calls':calls}
    db.commit()
    return dict(result, source_coverage=trace['source_coverage'], question_id=record.id, person_id=person_id,
                profile_version=record.profile_version)


def ask_batch(db, person_ids, question, *, user_id, scope):
    ids=list(dict.fromkeys(person_ids))
    if not ids or len(ids)>20 or any(type(i) is not int or i<1 for i in ids):
        raise BizError(422,'请选择1至20个有效人员ID')
    if not question.strip() or len(question)>1000:
        raise BizError(422,'问题需要1至1000个字符')
    # The whole scope is authorized before the first embedding or audit write.
    for person_id in ids:
        get_person_snapshot(db,person_id,scope=scope)
    items=[]
    for person_id in ids:
        try:
            items.append(ask(db,person_id,question,user_id=user_id,scope=scope))
        except BizError as exc:
            if exc.code==403:raise
            db.rollback()
            items.append({'person_id':person_id,'status':'failed','error':str(exc)})
    return {'items':items,'requested':len(ids),'answered':sum(i['status']=='answered' for i in items),
            'partial':any(i['status']!='answered' for i in items)}
