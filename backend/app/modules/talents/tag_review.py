"""Review literal tag evidence inside the existing profile-edit transaction."""
import json

from sqlalchemy import select

from app.core.errors import BizError
from app.core.normalize import normalize_tag
from app.models.talent import TalentProfileEdit, TalentProfileVersion
from app.dao import talent_profiles as profile_dao
from app.modules.talents import tag_catalog

FIELD = '_tag_review_v1'


def _source_ids(snapshot):
    ids = {row.get('resume_file_id') for row in snapshot.get('sources', [])}
    ids.update(row.get('file_id') for row in snapshot.get('source_pages', []))
    ids.update(row.get('file_id') for row in snapshot.get('evidence', []))
    if snapshot.get('parsed'):
        ids.add(snapshot['parsed'].get('resume_file_id'))
    return ids - {None}


def authorized_snapshot(db, pid, scope, version=None):
    from app.service.talent_service import get_person_snapshot, SnapshotScopeError
    if scope is None:
        raise BizError(403, '无权访问该资源')
    try:
        saved = get_person_snapshot(db, pid, version=version, scope=scope)
    except SnapshotScopeError as exc:
        raise BizError(403, '无权访问该资源') from exc
    if saved['status'] != 'ready':
        raise BizError(409, '档案版本或原文当前不可用')
    return saved


def validate(db, person, proofs, final_tags, scope):
    if scope is None or scope.role not in ('hr', 'admin'):
        raise BizError(403, '无权访问该资源')
    saved = authorized_snapshot(db, person.id, scope)
    candidates = {row['candidate_id']: row for row in tag_catalog.suggest(saved)['items']}
    final = {(tag, kind) for tag, kind, _ in final_tags}
    output, seen = [], set()
    for proof in proofs:
        if proof.catalog_version != tag_catalog.VERSION or proof.profile_version != saved['profile_version']:
            raise BizError(409, '标签依据版本已变化，请重新加载原文候选')
        row = candidates.get(proof.candidate_id)
        if row is None or row['code'] != proof.code:
            raise BizError(409, '原文候选已变化或不属于当前人员')
        tag = normalize_tag(row['name'], row['type'])
        if (tag, row['type']) not in final or tag in seen:
            raise BizError(422, '原文确认必须对应本次保存的唯一标签和类型')
        seen.add(tag)
        risky = row['context_status'] != 'mentioned' or row['evidence_truncated'] or saved.get('truncated') or any(
            e['quality_status'] not in ('ready',) for e in row['evidence'])
        if risky and not proof.reason.strip():
            raise BizError(422, '特殊语境或低质量原文须填写采用原因，不得据此认定已具备能力')
        output.append({'tag': tag, 'tag_type': row['type'], 'code': row['code'],
            'catalog_version': tag_catalog.VERSION, 'review_policy_version': tag_catalog.REVIEW_POLICY,
            'candidate_id': row['candidate_id'], 'source_profile_version': saved['profile_version'],
            'context_status': row['context_status'], 'evidence': row['evidence'], 'reason': proof.reason.strip(),
            'evidence_truncated': row['evidence_truncated'], 'ability_verified': False})
    return output


def persist(db, person, proofs, version, user_id):
    tags = {row.tag: row for row in profile_dao.get_tag_rows(db, person.id)}
    prior = {json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True)
        for value in db.scalars(select(TalentProfileEdit.new_value).where(
            TalentProfileEdit.talent_profile_id==person.id, TalentProfileEdit.profile_version==version,
            TalentProfileEdit.field_name==FIELD, TalentProfileEdit.edited_by==user_id))}
    for item in proofs:
        payload = dict(item, result_profile_version=version, tag_row_id=tags[item['tag']].id)
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if fingerprint in prior:
            continue
        prior.add(fingerprint)
        db.add(TalentProfileEdit(talent_profile_id=person.id, profile_version=version,
            field_name=FIELD, old_value=None, new_value=json.dumps(payload, ensure_ascii=False), edited_by=user_id))
    db.flush()


def quality(db, pid, *, scope):
    saved = authorized_snapshot(db, pid, scope)
    proofs = {}
    for entry in db.scalars(select(TalentProfileEdit).where(TalentProfileEdit.talent_profile_id==pid,
            TalentProfileEdit.field_name==FIELD).order_by(TalentProfileEdit.id)):
        payload = json.loads(entry.new_value)
        proofs[payload['tag']] = (payload, entry.edited_by)
    history = list(db.scalars(select(TalentProfileVersion).where(TalentProfileVersion.talent_profile_id==pid,
        TalentProfileVersion.version<=saved['profile_version']).order_by(TalentProfileVersion.version)))
    sources = {}
    items = []
    for tag in profile_dao.get_tag_rows(db, pid):
        item = {'tag': tag.tag, 'tag_type': tag.tag_type, 'source': tag.source,
            'evidence_status': 'manual_unverified' if tag.source=='manual' else 'ai_unverified',
            'ability_verified': False, 'evidence': []}
        if tag.tag in proofs:
            proof, reviewer = proofs[tag.tag]
            continuous = all(any(t.get('tag')==tag.tag and t.get('tag_type')==tag.tag_type
                for t in (v.profile_snapshot_json.get('profile') or {}).get('tags', []))
                for v in history if v.version>=proof['result_profile_version'])
            required_sources = {row['file_id'] for row in proof['evidence']}
            source_continuous = all(required_sources.issubset(_source_ids(v.profile_snapshot_json))
                for v in history if v.version>=proof['source_profile_version'])
            if proof['source_profile_version'] not in sources:
                try:
                    sources[proof['source_profile_version']] = authorized_snapshot(db, pid, scope, proof['source_profile_version'])
                except BizError as exc:
                    if exc.code==403: raise
                    sources[proof['source_profile_version']] = None
            original = sources[proof['source_profile_version']]
            valid = (original is not None and continuous and source_continuous and tag.id==proof['tag_row_id']
                     and tag.tag_type==proof['tag_type'])
            item['evidence_status'] = 'reviewed_source' if valid else 'needs_reconfirmation'
            if valid:
                item.update(evidence=proof['evidence'], context_status=proof['context_status'],
                    source_profile_version=proof['source_profile_version'], result_profile_version=proof['result_profile_version'])
                if scope.role in ('hr','admin'):
                    item.update(reason=proof['reason'], reviewed_by=reviewer)
        items.append(item)
    states = ('reviewed_source','needs_reconfirmation','manual_unverified','ai_unverified')
    return {'person_id': pid, 'profile_version': saved['profile_version'], 'items': items,
        'summary': {state: sum(i['evidence_status']==state for i in items) for state in states},
        'source_partial': bool(saved.get('truncated') or saved.get('coverage')=='partial'),
        'portrait_basis': 'model_profile_estimate_not_assessment', 'ability_verified': False,
        'missing_fields': saved.get('missing_fields', []), 'review_fields': saved.get('review_fields', [])}
