"""Resumable per-person QA with heartbeat and lease-fenced publication."""
from copy import deepcopy
from datetime import datetime
import threading

from sqlalchemy import select, update

from app.config import settings
from app.core.errors import BizError
from app.database import SessionLocal
from app.models.talent import ProfileQuestion, ProfileQuestionJob
from app.modules.talents import question_jobs as jobs


class LeaseLost(Exception):
    pass


def _heartbeat_interval():
    return min(5.0, max(1, settings.task_heartbeat_timeout) / 3)


def handle_question_job(job_id, token, *, session_factory=None):
    factory = session_factory or SessionLocal
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(_heartbeat_interval()):
            try:
                with factory() as db:
                    result = db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id == job_id,
                        ProfileQuestionJob.status == 'running', ProfileQuestionJob.lease_token == token)
                        .values(heartbeat_at=datetime.now()))
                    db.commit()
                    if result.rowcount != 1:
                        return
            except Exception:
                # A missed heartbeat is recoverable; it must not publish without its lease.
                return

    thread = threading.Thread(target=heartbeat, name=f'qa-heartbeat-{job_id}', daemon=True)
    thread.start()
    try:
        with factory() as db:
            row = db.get(ProfileQuestionJob, job_id)
            if row is None or row.status != 'running' or row.lease_token != token:
                return
            uid, request = row.asked_by, deepcopy(row.request_json)
            progress = deepcopy(row.progress_json)

            def guard(*, final=False):
                # End the previous MySQL repeatable-read snapshot before reauthorization.
                db.rollback()
                db.expire_all()
                stmt = select(ProfileQuestionJob).where(ProfileQuestionJob.id == job_id)
                if final:
                    stmt = stmt.with_for_update()
                active = db.scalar(stmt)
                if active is None or active.status != 'running' or active.lease_token != token:
                    raise LeaseLost()
                scope = jobs.current_scope(db, uid)
                jobs._authorized(db, active, scope)
                return scope

            try:
                guard()
                mode = request.get('mode', 'individual')
                if mode == 'statistics':
                    from app.modules.talents.batch_analysis import build_analysis
                    scope = guard()
                    frozen = [jobs.analysis_snapshot(db, t['person_id'], t['version'], scope) for t in request['targets']]
                    analysis = build_analysis(frozen, request['criteria'])
                    guard(final=True)
                    progress['analysis'] = analysis
                    jobs.checkpoint(db, job_id, token, progress, status='completed', stage='completed',
                        lease_token=None, error=None, finished_at=datetime.now())
                    return
                completed = {item['person_id'] for item in progress.get('items', [])}
                from app.modules.talents.qa_service import ask
                for target in request['targets']:
                    if target['person_id'] in completed:
                        continue
                    scope = guard()
                    if not jobs.checkpoint(db, job_id, token, progress, stage='answering'):
                        return
                    prior = db.scalar(select(ProfileQuestion).where(
                        ProfileQuestion.person_id == target['person_id'], ProfileQuestion.asked_by == uid,
                        ProfileQuestion.profile_version == target['version'],
                        ProfileQuestion.status.in_(['answered', 'insufficient_evidence']),
                        ProfileQuestion.snapshot_json['job_id'].as_integer() == job_id)
                        .order_by(ProfileQuestion.id.desc()).limit(1))
                    if prior is not None:
                        saved = prior.snapshot_json
                        item = dict(saved['result'], question_id=prior.id, person_id=prior.person_id,
                            profile_version=prior.profile_version, source_coverage=saved.get('source_coverage'),
                            follow_up_to=request.get('follow_up_to'))
                    else:
                        item = ask(db, target['person_id'], request['question'], user_id=uid, scope=scope,
                            version=target['version'], follow_up_to=request.get('follow_up_to'), guard=guard, job_id=job_id)
                    guard(final=True)
                    progress['items'].append(item)
                    if not jobs.checkpoint(db, job_id, token, progress, stage='answering'):
                        return
                if mode == 'compare':
                    from app.modules.talents.batch_analysis import build_analysis
                    scope = guard()
                    frozen = [jobs.analysis_snapshot(db, t['person_id'], t['version'], scope) for t in request['targets']]
                    progress['analysis'] = build_analysis(frozen, request.get('criteria', []))
                guard(final=True)
                jobs.checkpoint(db, job_id, token, progress, status='completed', stage='completed',
                    lease_token=None, error=None, finished_at=datetime.now())
            except LeaseLost:
                db.rollback()
            except Exception as exc:
                db.rollback()
                row = db.get(ProfileQuestionJob, job_id)
                retry = (not isinstance(exc, BizError) or exc.code >= 500) and row.attempt_count < row.max_attempts
                jobs.checkpoint(db, job_id, token, progress,
                    status='pending' if retry else 'failed', stage='retrying' if retry else 'failed',
                    lease_token=None, error='问答暂不可用，正在重试' if retry else '问答未完成，请核对当前权限和档案或重新提问',
                    finished_at=None if retry else datetime.now())
    finally:
        stop.set()
        thread.join(timeout=1)
