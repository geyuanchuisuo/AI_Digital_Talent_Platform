"""B-owned merge journal in the existing private profile-edit audit table."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json

from sqlalchemy import select

from app.core.errors import BizError
from app.dao import talent_profiles as profile_dao
from app.models.talent import TalentProfile, TalentTag, TalentProfileEdit
from app.models.resume import ParsedResume, ParseTask
from app.modules.talents.history import current_version, save_version

JOURNAL_FIELD = '_merge_journal_v1'


def state(db, profile):
    from app.service.profile_service import FIELD_COLUMNS
    columns = set(FIELD_COLUMNS.values()) | {'manual_edited_fields', 'source', 'department_id'}
    return {
        'fields': json.loads(json.dumps({k: getattr(profile, k) for k in sorted(columns)}, default=str)),
        'tags': [[t.tag, t.tag_type, t.source] for t in db.scalars(select(TalentTag).where(
            TalentTag.talent_profile_id == profile.id).order_by(TalentTag.tag))],
        'parsed_ids': list(db.scalars(select(ParsedResume.id).where(
            ParsedResume.talent_profile_id == profile.id).order_by(ParsedResume.id))),
    }


def begin(db, profile, parsed):
    task = db.scalar(select(ParseTask).where(ParseTask.resume_file_id == parsed.resume_file_id)
        .with_for_update().execution_options(populate_existing=True))
    task_before = None if task is None else {
        'id': task.id, 'error': task.error,
        'profiled_at': task.profiled_at.isoformat() if task.profiled_at else None}
    return {'before': state(db, profile), 'previous_person_id': parsed.talent_profile_id,
            'task_before': task_before}


def finish(db, profile, parsed, duplicate, before, version, user_id):
    payload = dict(before, duplicate_id=duplicate.id, parsed_id=parsed.id,
                   after=state(db, profile), merge_note=duplicate.resolution_note)
    db.add(TalentProfileEdit(talent_profile_id=profile.id, profile_version=version,
        field_name=JOURNAL_FIELD, old_value=None,
        new_value=json.dumps(payload, ensure_ascii=False), edited_by=user_id))
    db.flush()


def undo(db, duplicate_id, *, user_id, reason):
    from app.models.talent import TalentDuplicate
    from app.service import profile_service
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise BizError(422, '撤销原因需要1至500个字符')
    duplicate = db.get(TalentDuplicate, duplicate_id)
    if duplicate is None:
        raise BizError(404, '查重记录不存在')
    # Same profile-first locking order as merge; re-read mutable rows under the lock.
    profile = db.scalar(select(TalentProfile).where(TalentProfile.id == duplicate.profile_a_id)
        .with_for_update().execution_options(populate_existing=True))
    duplicate = db.scalar(select(TalentDuplicate).where(TalentDuplicate.id == duplicate_id)
        .with_for_update().execution_options(populate_existing=True))
    if profile is None or profile.deleted_at is not None or duplicate.status != 'merged':
        raise BizError(409, '该合并当前不可撤销')
    journal = db.scalar(select(TalentProfileEdit).where(
        TalentProfileEdit.talent_profile_id == profile.id,
        TalentProfileEdit.field_name == JOURNAL_FIELD).order_by(TalentProfileEdit.id.desc()).limit(1))
    if journal is None:
        raise BizError(409, '历史合并没有完整撤销日志，需人工核查')
    payload = json.loads(journal.new_value)
    latest = current_version(db, profile.id)
    if payload['duplicate_id'] != duplicate.id or latest is None or latest.version != journal.profile_version:
        raise BizError(409, '合并后已有其他修改，需人工核查，未覆盖当前档案')
    if state(db, profile) != payload['after']:
        raise BizError(409, '当前资料已变化，未覆盖当前档案')
    parsed = db.scalar(select(ParsedResume).where(ParsedResume.id == payload['parsed_id'])
        .with_for_update().execution_options(populate_existing=True))
    if parsed is None or parsed.talent_profile_id != profile.id:
        raise BizError(409, '解析关联已变化，未撤销')
    if payload['previous_person_id'] not in (None, profile.id):
        raise BizError(409, '涉及其他主档的历史关联，需要人工核查')
    task = db.scalar(select(ParseTask).where(ParseTask.resume_file_id == parsed.resume_file_id)
        .with_for_update().execution_options(populate_existing=True))
    if task is not None and task.status != 'success':
        raise BizError(409, '解析任务状态已变化，未撤销')
    task_before = payload.get('task_before')
    if (task is None) != (task_before is None) or (task and task.id != task_before['id']):
        raise BizError(409, '解析任务关联已变化，未撤销')
    for column, value in payload['before']['fields'].items():
        if column == 'work_years' and value is not None:
            value = Decimal(value)
        setattr(profile, column, deepcopy(value))
    profile_dao.replace_tag_rows(db, profile.id, payload['before']['tags'])
    profile.updated_by = user_id
    profile.vector_status = 'pending'
    parsed.talent_profile_id = payload['previous_person_id']
    duplicate.status = 'pending'
    duplicate.resolved_by = None
    duplicate.resolved_at = None
    duplicate.resolution_note = None
    if task is not None:
        task.status = 'duplicate_pending'
        task.stage = 'profile'
        task.finished_at = None
        task.heartbeat_at = None
        task.error = task_before['error']
        task.profiled_at = datetime.fromisoformat(task_before['profiled_at']) if task_before['profiled_at'] else None
    version = save_version(db, profile, 'restore', changed_by=user_id,
                           change_reason='撤销合并：' + reason.strip())
    db.add(TalentProfileEdit(talent_profile_id=profile.id, profile_version=version,
        field_name='_merge_undo_v1', old_value=str(journal.id),
        new_value=json.dumps({'duplicate_id': duplicate_id, 'reason': reason.strip()}, ensure_ascii=False),
        edited_by=user_id))
    db.commit()
    try:
        vector = profile_service.sync_profile_vector(profile,
            structured=profile_service.aggregate_structured(db, profile.id),
            tags=[t.tag for t in profile_dao.get_tag_rows(db, profile.id)])
        profile.vector_status = 'synced' if vector is None else vector.vector_status
    except Exception:
        profile.vector_status = 'failed'
    db.commit()
    return {'duplicate_id': duplicate_id, 'status': 'pending', 'master_profile_id': profile.id,
            'profile_version': version, 'vector_status': profile.vector_status}
