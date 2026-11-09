"""Transactional profile history. Caller owns commit/rollback and authorization."""
from copy import deepcopy
from datetime import datetime
import json

from sqlalchemy import select

from app.models.talent import TalentProfile, TalentTag, TalentProfileVersion, TalentProfileEdit, TalentFieldEvidence
from app.models.resume import ResumePage, ParseAttempt, ParsedResume, ResumeFile

HISTORY_MODELS = (TalentProfileVersion, TalentProfileEdit, TalentFieldEvidence, ResumePage, ParseAttempt)


def _json(value):
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v), ensure_ascii=False))


def _content(snapshot):
    result = deepcopy(snapshot)
    for key in ('snapshot_at', 'profile_version', 'version_source', 'evidence', 'evidence_status', 'parse_status', 'parse_stage'):
        result.pop(key, None)
    for key in ('created_at', 'updated_at', 'vector_status'):
        (result.get('profile') or {}).pop(key, None)
    return result


def current_version(db, person_id):
    return db.scalar(select(TalentProfileVersion).where(
        TalentProfileVersion.talent_profile_id == person_id
    ).order_by(TalentProfileVersion.version.desc()).limit(1))


def save_version(db, profile, change_type, *, changed_by=None):
    from app.service import talent_service as svc
    if change_type not in {'baseline', 'reparse', 'manual_edit', 'merge', 'restore', 'delete'}:
        raise ValueError('Invalid profile change type')
    # Serialize all writers on the existing person row; unique version is the final guard.
    db.execute(select(TalentProfile.id).where(TalentProfile.id == profile.id).with_for_update())
    db.flush()
    profiles, tags, parsed, files, tasks = svc._load_snapshot_bundle(db, [profile.id])
    parsed_row = parsed.get(profile.id)
    file_id = parsed_row.resume_file_id if parsed_row else None
    snapshot = svc._build_snapshot(person_id=profile.id, profile=profile,
        tag_rows=tags.get(profile.id, []), parsed=parsed_row,
        resume_file=files.get(file_id), parse_task=tasks.get(file_id),
        requested_version=None, snapshot_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    snapshot = _json(snapshot)
    from app.service.profile_service import FIELD_COLUMNS
    snapshot['_audit_profile'] = _json({key: getattr(profile, column) for key, column in FIELD_COLUMNS.items()})
    snapshot['sources'] = [_json(svc._parsed_snapshot(parsed, db.get(ResumeFile, parsed.resume_file_id)))
        for parsed in db.scalars(select(ParsedResume).where(ParsedResume.talent_profile_id == profile.id).order_by(ParsedResume.id))]
    previous = current_version(db, profile.id)
    if previous and change_type != 'restore' and _content(previous.profile_snapshot_json) == _content(snapshot):
        if snapshot['status'] == 'ready':
            from app.modules.talents.evidence import collect_evidence
            collect_evidence(db, profile, previous.version)
        return previous.version
    version = previous.version + 1 if previous else 1
    snapshot.update(profile_version=version, version_source='persisted')
    row = TalentProfileVersion(talent_profile_id=profile.id, version=version,
        change_type=change_type, changed_by=changed_by, profile_snapshot_json=snapshot)
    db.add(row)
    if previous and snapshot['status'] == 'ready':
        old_raw = previous.profile_snapshot_json.get('_audit_profile', {})
        new_raw = snapshot.get('_audit_profile', {})
        for evidence in db.scalars(select(TalentFieldEvidence).where(
                TalentFieldEvidence.talent_profile_id == profile.id,
                TalentFieldEvidence.profile_version == previous.version)):
            key = evidence.field_name.split('.')[-1]
            same_value = (key in old_raw and old_raw.get(key) == new_raw.get(key))
            if not same_value:
                old_parsed = previous.profile_snapshot_json.get('parsed') or {}
                new_parsed = snapshot.get('parsed') or {}
                same_value = key in old_parsed and old_parsed.get(key) == new_parsed.get(key)
            linked = db.scalar(select(ParsedResume.id).where(ParsedResume.resume_file_id == evidence.resume_file_id,
                ParsedResume.talent_profile_id == profile.id))
            if same_value and linked:
                db.add(TalentFieldEvidence(talent_profile_id=profile.id, profile_version=version,
                    field_name=evidence.field_name, resume_file_id=evidence.resume_file_id,
                    source_page=evidence.source_page, source_text=evidence.source_text,
                    source=evidence.source, confidence=evidence.confidence, review_status=evidence.review_status))
    if change_type == 'manual_edit' and previous:
        if changed_by is None:
            raise ValueError('Manual edits require an actor')
        old = previous.profile_snapshot_json.get('_audit_profile', {})
        new = snapshot.get('_audit_profile', {})
        for field in sorted(set(old) | set(new)):
            if field in {'updated_at', 'created_at', 'vector_status', 'manual_edited_fields'}:
                continue
            if old.get(field) != new.get(field):
                db.add(TalentProfileEdit(talent_profile_id=profile.id, profile_version=version,
                    field_name=field, old_value=json.dumps(old.get(field), ensure_ascii=False),
                    new_value=json.dumps(new.get(field), ensure_ascii=False), edited_by=changed_by))
    db.flush()
    if snapshot['status'] == 'ready':
        from app.modules.talents.evidence import collect_evidence
        collect_evidence(db, profile, version)
    return version


def read_version(db, person_id, version=None):
    query = select(TalentProfileVersion).where(TalentProfileVersion.talent_profile_id == person_id)
    if version is not None:
        query = query.where(TalentProfileVersion.version == version)
    row = db.scalar(query.order_by(TalentProfileVersion.version.desc()).limit(1))
    if row is None:
        return None
    snapshot = deepcopy(row.profile_snapshot_json)
    snapshot.pop('_audit_profile', None)
    evidence = db.scalars(select(TalentFieldEvidence).where(
        TalentFieldEvidence.talent_profile_id == person_id,
        TalentFieldEvidence.profile_version == row.version).order_by(TalentFieldEvidence.id)).all()
    snapshot['evidence'] = []
    for e in evidence:
        source_file = db.get(ResumeFile, e.resume_file_id)
        document_text = source_file is not None and source_file.file_ext == 'docx'
        snapshot['evidence'].append(dict(field_path=e.field_name, file_id=e.resume_file_id,
            filename=source_file.filename if source_file else None,
            page_no=None if document_text else e.source_page,
            locator_type='document_text' if document_text else 'page',
            snippet=e.source_text, source=e.source,
            confidence=float(e.confidence) if e.confidence is not None else None,
            review_status=e.review_status))
    snapshot['evidence_status'] = 'available' if evidence else 'unavailable'
    return snapshot


def add_evidence(db, person_id, version, field_path, *, file_id, page_no, snippet,
                 source='text', confidence=None, review_status='pending_review'):
    from app.service.talent_service import EVIDENCE_FIELD_PATHS
    if field_path not in EVIDENCE_FIELD_PATHS or page_no < 1 or not snippet.strip():
        raise ValueError('Invalid field evidence')
    if source not in {'text', 'ocr'} or review_status not in {'confirmed', 'pending_review'}:
        raise ValueError('Invalid evidence source/status')
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError('Invalid evidence confidence')
    latest = current_version(db, person_id)
    page = db.scalar(select(ResumePage).where(ResumePage.resume_file_id == file_id, ResumePage.page_no == page_no))
    linked = db.scalar(select(ParsedResume.id).where(ParsedResume.resume_file_id == file_id,
        ParsedResume.talent_profile_id == person_id))
    if latest is None or latest.version != version or not linked or page is None or snippet not in page.text_content:
        raise ValueError('Evidence must reference the current profile and an associated source page')
    existing = db.scalar(select(TalentFieldEvidence).where(
        TalentFieldEvidence.talent_profile_id == person_id, TalentFieldEvidence.profile_version == version,
        TalentFieldEvidence.field_name == field_path, TalentFieldEvidence.resume_file_id == file_id,
        TalentFieldEvidence.source_page == page_no, TalentFieldEvidence.source_text == snippet))
    if existing:
        return existing
    row = TalentFieldEvidence(talent_profile_id=person_id, profile_version=version,
        field_name=field_path, resume_file_id=file_id, source_page=page_no, source_text=snippet,
        source=source, confidence=confidence, review_status=review_status)
    db.add(row); db.flush()
    return row
