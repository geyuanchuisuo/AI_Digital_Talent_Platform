"""Read-only graph evidence preview with fresh SQL authorization on both sides."""
from app.core.errors import BizError
from app.modules.talents.question_jobs import current_scope
from app.modules.talents.graph_projection import profile_projection
from app.service.talent_service import get_person_snapshot, SnapshotScopeError


def _source(db, pid, version, uid):
    db.rollback()  # End a previous repeatable-read snapshot before current authorization.
    scope = current_scope(db, uid)
    try:
        saved = get_person_snapshot(db, pid, version=version, scope=scope)
    except SnapshotScopeError as exc:
        raise BizError(403, '无权访问该资源') from exc
    if saved['status'] != 'ready':
        raise BizError(409, '档案版本或来源当前不可用')
    return saved


def preview(db, person_id, *, user_id, version=None):
    saved = _source(db, person_id, version, user_id)
    result = profile_projection(saved)
    _source(db, person_id, saved['profile_version'], user_id)
    return result
