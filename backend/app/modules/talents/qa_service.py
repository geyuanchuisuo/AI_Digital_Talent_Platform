"""Authorized, version-bound profile QA with durable request outcomes."""
from app.config import settings
from app.core.errors import BizError
from app.service.talent_service import get_person_snapshot
from app.modules.talents.qa_models import ProfileQuestion
from app.modules.talents import qa
from app.modules.talents.qa_index import retrieve


def ask(db, person_id, question, *, user_id, scope, version=None, follow_up_to=None):
    # Authorization and current deletion are checked before any model or source access.
    snapshot = get_person_snapshot(db, person_id, version=version, scope=scope)
    if snapshot['status'] != 'ready':
        raise BizError(409, '档案尚不可用，不能问答')
    if not question.strip() or len(question) > 1000:
        raise BizError(422, '问题需要1至1000个字符')
    previous_turn = None
    query = question
    prompt_version, system_prompt = qa.PROMPT_VERSION, qa.SYSTEM_PROMPT
    if follow_up_to is not None:
        from app.modules.talents import follow_up
        parent, previous_turn = follow_up.parent_turn(db, person_id, user_id, follow_up_to, version)
        snapshot = get_person_snapshot(db, person_id, version=parent.profile_version, scope=scope)
        if snapshot['status'] != 'ready':
            raise BizError(409, '上一轮档案版本当前不可访问，请开始新问题')
        source_ids = {s.get('resume_file_id') for s in snapshot.get('sources', [])}
        source_ids.update(s.get('file_id') for s in snapshot.get('source_pages', []))
        source_ids.update(s.get('file_id') for s in snapshot.get('evidence', []))
        if any(c.get('file_id') not in source_ids for c in
               (parent.snapshot_json.get('result', {}).get('citations') or [])):
            raise BizError(409, '上一轮引用来源当前不可访问')
        query = follow_up.retrieval_query(question, parent)
        prompt_version, system_prompt = follow_up.PROMPT_VERSION, qa.SYSTEM_PROMPT + follow_up.PROMPT_SUFFIX
    trace = dict(question=question, prompt_version=qa.PROMPT_VERSION,
                 system_prompt=qa.SYSTEM_PROMPT, model=settings.llm_model,
                 embedding_model=settings.embedding_model, retrieval_strategy='milvus_cosine',
                 retrieval_threshold=0.35)
    trace.update(prompt_version=prompt_version, system_prompt=system_prompt,
                 follow_up_to=follow_up_to, previous_turn=previous_turn, retrieval_query=query)
    record = ProfileQuestion(person_id=person_id, profile_version=snapshot['profile_version'],
        asked_by=user_id, status='running', snapshot_json=trace)
    db.add(record); db.commit()
    calls = []
    def generate(q, context):
        trace['context'] = context
        from app.core.llm_client import chat_json
        import json
        payload = {'question': q, 'evidence': context}
        if previous_turn is not None:
            payload['previous_turn'] = previous_turn
        output = chat_json(json.dumps(payload, ensure_ascii=False),
            schema=qa.Answer, system_prompt=system_prompt, on_result=calls.append)
        return output.model_dump()
    try:
        candidates = retrieve(snapshot, query)
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
                profile_version=record.profile_version, follow_up_to=follow_up_to)


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


def question_history(db, person_id, *, user_id, scope, offset=0, limit=20):
    """Read only the caller's questions; never expose provider/Prompt audit data."""
    from copy import deepcopy
    from sqlalchemy import select, func
    from app.models.resume import ParsedResume
    snapshot = get_person_snapshot(db, person_id, scope=scope)
    if snapshot['status'] != 'ready':
        raise BizError(409, '档案当前不可用，无法查看历史问答')
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise BizError(422, '历史分页参数无效')
    conditions = (ProfileQuestion.person_id == person_id, ProfileQuestion.asked_by == user_id)
    total = db.scalar(select(func.count()).select_from(ProfileQuestion).where(*conditions))
    records = db.scalars(select(ProfileQuestion).where(*conditions)
        .order_by(ProfileQuestion.id.desc()).offset(offset).limit(limit)).all()
    source_ids = set(db.scalars(select(ParsedResume.resume_file_id).where(
        ParsedResume.talent_profile_id == person_id)))
    items = []
    for row in records:
        saved = row.snapshot_json or {}
        result = saved.get('result') or {}
        citations = deepcopy(result.get('citations') or [])
        status, answer = row.status, result.get('answer', '')
        # An incorrect merge can later be undone; do not reveal the detached person's facts.
        if any(c.get('file_id') not in source_ids for c in citations):
            status, answer, citations = 'source_unavailable', '', []
        items.append({'question_id': row.id, 'question': saved.get('question', ''),
            'person_id': row.person_id, 'profile_version': row.profile_version,
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'status': status, 'answer': answer, 'citations': citations,
            'source_coverage': saved.get('source_coverage')})
    return {'items': items, 'total': total}
