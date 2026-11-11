"""Owner-bound durable QA requests and fenced checkpoints."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.models.user import User
from app.models.talent import ProfileQuestionJob
from app.service.talent_service import get_person_snapshot, SnapshotScopeError


def snapshot(db, pid, version, scope):
    try:
        value = get_person_snapshot(db, pid, version=version, scope=scope)
    except SnapshotScopeError as exc:
        raise BizError(403, '无权访问该资源') from exc
    if value['status'] != 'ready':
        raise BizError(409, '档案版本或原文当前不可用')
    return value


def current_scope(db, uid):
    db.expire_all()
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise BizError(403, '无权访问该资源')
    return get_account_scope(user)


def _owned(db, job_id, uid):
    row = db.get(ProfileQuestionJob, job_id)
    if row is None or row.asked_by != uid:
        raise BizError(403, '无权访问该资源')
    return row


def _authorized(db, row, scope):
    if row.request_json.get('mode', 'individual') != 'individual' and scope.role not in ('hr', 'admin'):
        raise BizError(403, '无权访问该资源')
    snapshots = {}
    for target in row.request_json['targets']:
        snapshots[target['person_id']] = snapshot(db, target['person_id'], target['version'], scope)
    for item in row.progress_json.get('items', []):
        saved = snapshots[item['person_id']]
        source_ids = {s.get('resume_file_id') for s in saved.get('sources', [])}
        source_ids.update(s.get('file_id') for s in saved.get('source_pages', []))
        source_ids.update(s.get('file_id') for s in saved.get('evidence', []))
        if any(c.get('file_id') not in source_ids for c in item.get('citations', [])):
            raise BizError(409, '引用来源当前不可访问')


def _view(row):
    request = row.request_json
    items = deepcopy(row.progress_json.get('items', []))
    analysis = deepcopy(row.progress_json.get('analysis'))
    return {'job_id': row.id, 'status': row.status, 'stage': row.stage,
            'attempt_count': row.attempt_count, 'max_attempts': row.max_attempts,
            'question': request['question'], 'targets': deepcopy(request['targets']),
            'mode': request.get('mode', 'individual'), 'criteria': deepcopy(request.get('criteria', [])),
            'analysis': analysis,
            'follow_up_to': request.get('follow_up_to'), 'items': items,
            'requested': len(request['targets']), 'processed': len(analysis['rows']) if analysis else len(items),
            'answered': sum(i.get('status') == 'answered' for i in items),
            'error': row.error}


def create(db, person_ids, question, *, user_id, scope, key, version=None, follow_up_to=None,
           mode='individual', criteria=None):
    ids = list(dict.fromkeys(person_ids))
    if not ids or len(ids) > 20 or any(type(i) is not int or i < 1 for i in ids):
        raise BizError(422, '请选择1至20个有效人员ID')
    from app.modules.talents.batch_analysis import validate_criteria
    criteria = validate_criteria(criteria if criteria is not None else [])
    if mode not in ('individual', 'compare', 'statistics'):
        raise BizError(422, '不支持该问答模式')
    if (mode == 'individual' and criteria) or (mode == 'statistics' and not criteria) or (mode == 'compare' and len(ids) < 2):
        raise BizError(422, '比较至少选择2人，统计须指定条件；逐人问答不接受统计条件')
    if mode != 'individual':
        if scope.role not in ('hr', 'admin'):
            raise BizError(403, '无权访问该资源')
        if follow_up_to is not None:
            raise BizError(422, '比较和统计不接受单人追问参数')
    if mode == 'statistics':
        question = '所选档案字段统计（明确条件，AND）'
    question = question.strip()
    if not question or len(question) > 1000 or not isinstance(key, str) or not 1 <= len(key) <= 64:
        raise BizError(422, '问题或幂等键无效')
    if len(ids) != 1 and (version is not None or follow_up_to is not None):
        raise BizError(422, '批量问答不接受单人追问或版本参数')
    fingerprint_data = [ids, question, version, follow_up_to]
    if mode != 'individual':
        fingerprint_data += [mode, criteria]
    fingerprint = sha256(json.dumps(fingerprint_data, ensure_ascii=False).encode()).hexdigest()
    existing = db.scalar(select(ProfileQuestionJob).where(
        ProfileQuestionJob.asked_by == user_id, ProfileQuestionJob.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise BizError(409, '该幂等键已用于其他问题')
        return read(db, existing.id, user_id=user_id)
    if follow_up_to is not None:
        from app.modules.talents.follow_up import parent_turn
        parent, _ = parent_turn(db, ids[0], user_id, follow_up_to, version)
        version = parent.profile_version
    selected = [snapshot(db, pid, version, scope) for pid in ids]
    if mode != 'individual':
        from app.models.talent import TalentProfile
        from app.modules.talents.history import save_version
        for saved in selected:
            if saved.get('version_source') == 'baseline':
                person = db.get(TalentProfile, saved['person_id'])
                save_version(db, person, 'baseline', changed_by=user_id)
        selected = [snapshot(db, pid, version, scope) for pid in ids]
    targets = [{'person_id': pid, 'version': saved['profile_version']} for pid, saved in zip(ids, selected)]
    row = ProfileQuestionJob(asked_by=user_id, idempotency_key=key, request_hash=fingerprint,
        request_json={'question': question, 'targets': targets, 'follow_up_to': follow_up_to,
                      'mode': mode, 'criteria': criteria},
        progress_json={'items': []})
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ProfileQuestionJob).where(
            ProfileQuestionJob.asked_by == user_id, ProfileQuestionJob.idempotency_key == key))
        if existing is None:
            raise
        if existing.request_hash != fingerprint:
            raise BizError(409, '该幂等键已用于其他问题')
        return read(db, existing.id, user_id=user_id)
    return _view(row)


def read(db, job_id, *, user_id):
    row = _owned(db, job_id, user_id)
    scope = current_scope(db, user_id)
    _authorized(db, row, scope)
    return _view(row)


def recent(db, *, user_id, person_ids, mode='individual'):
    ids = list(dict.fromkeys(person_ids))
    rows = db.scalars(select(ProfileQuestionJob).where(ProfileQuestionJob.asked_by == user_id)
        .order_by(ProfileQuestionJob.id.desc()).limit(50)).all()
    for row in rows:
        if row.request_json.get('mode', 'individual') == mode and [t['person_id'] for t in row.request_json['targets']] == ids:
            return read(db, row.id, user_id=user_id)
    return None


def analysis_snapshot(db, pid, version, scope):
    saved = snapshot(db, pid, version, scope)
    # A frozen record is distinct from today's still-running/failed extraction.
    current = get_person_snapshot(db, pid, scope=scope)
    saved['processing_incomplete'] = current.get('parse_status') in (
        'pending', 'running', 'failed', 'cancelled', 'duplicate_pending')
    return saved


def cancel(db, job_id, *, user_id):
    row = _owned(db, job_id, user_id)
    db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id == row.id,
        ProfileQuestionJob.status.in_(['pending', 'running'])).values(
            status='cancelled', stage='cancelled', lease_token=None, finished_at=datetime.now()))
    db.commit()
    db.refresh(row)
    return {'job_id': row.id, 'status': row.status}


def checkpoint(db, job_id, token, progress, **values):
    result = db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id == job_id,
        ProfileQuestionJob.status == 'running', ProfileQuestionJob.lease_token == token)
        .values(progress_json=deepcopy(progress), heartbeat_at=datetime.now(), **values))
    db.commit()
    return result.rowcount == 1
