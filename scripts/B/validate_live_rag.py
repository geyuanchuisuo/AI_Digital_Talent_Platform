"""Synthetic live B07/B08 validation in a fresh guarded DB/vector namespace.

Run with backend/.venv Python. No business DB, shared collection, .env edits,
provider stubs or real personal documents are used by this executable.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'


def validate_targets(database, collection, existing_collections=()):
    if not re.fullmatch(r'ai_talent_b_live_[a-zA-Z0-9_]{8,40}', database):
        raise ValueError('An explicitly isolated ai_talent_b_live_* database is required')
    if not re.fullmatch(r'b_live_[a-zA-Z0-9_]{8,40}', collection):
        raise ValueError('An explicitly isolated b_live_* collection prefix is required')
    if database != 'ai_talent_' + collection:
        raise ValueError('Database and vector namespace must identify the same owned run')
    owned = {collection, collection + '_qa_chunks_v1', collection + '_knowledge_chunks_v1'}
    if owned.intersection(existing_collections):
        raise ValueError('An existing vector namespace must not be reused or overwritten')


def isolated_environment(database, collection):
    validate_targets(database, collection)
    os.environ['MYSQL_DB'] = database
    os.environ['MILVUS_COLLECTION'] = collection
    run_dir = BACKEND / 'data' / 'live_rag' / collection
    os.environ['UPLOAD_DIR'] = str(run_dir / 'uploads')
    # A live fixture must never mint JWTs with a shared environment's signing key.
    os.environ['JWT_SECRET'] = os.urandom(32).hex()
    return run_dir


def execute(args):
    run_dir = isolated_environment(args.database, args.collection_prefix)
    sys.path.insert(0, str(BACKEND))
    from app.config import settings
    assert settings.mysql_db == args.database and settings.milvus_collection == args.collection_prefix
    from app.core.milvus_store import get_store
    validate_targets(args.database, args.collection_prefix, get_store().client.list_collections())
    from scripts.init_db import create_database
    if not create_database():
        raise ValueError('Live validation requires a fresh database; existing DB was not modified')
    from alembic import command
    from alembic.config import Config
    config = Config(str(BACKEND / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND / 'migrations'))
    command.upgrade(config, 'head')
    from sqlalchemy import select, update
    from fastapi.testclient import TestClient
    from app.database import SessionLocal
    from app.models import User, TalentProfile, ResumeFile, ParsedResume, ParseTask
    from app.models.resume import ResumePage
    from app.models.talent import ProfileQuestionJob
    from app.modules.system.models import SysRole, SysUserRole
    from app.modules.talents.history import save_version
    from app.modules.knowledge.models import KnowledgeQuestionJob
    from app.core.milvus_store import get_store
    from app.service.auth_service import hash_password
    from app.main import app
    from app.tasks.runner import runner
    report = {'database': args.database, 'collection_prefix': args.collection_prefix,
        'llm_model_configured': settings.llm_model, 'agnes_target_verified': False,
        'embedding_model': settings.embedding_model, 'embedding_dim': settings.embedding_dim,
        'synthetic_only': True, 'checks': [], 'stage': 'seed', 'ok': False}
    marker = uuid4().hex[:12]
    password = 'SyntheticOnly-' + marker
    names = {'admin': 'live_admin_' + marker, 'personal': 'live_person_' + marker}
    report['test_usernames'] = names
    # Kept only in ignored runtime files for the owned temporary browser session.
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'browser-fixture.json').write_text(json.dumps({'usernames': names, 'password': password}), encoding='utf-8')
    try:
        with SessionLocal() as db:
            people = [TalentProfile(name='合成同名人员', skills_json=['Python', 'Redis']),
                      TalentProfile(name='合成同名人员', skills_json=['Java', 'Kafka'])]
            db.add_all(people);db.flush()
            admin = User(username=names['admin'], password_hash=hash_password(password), role='hr', data_scope_mode='scoped')
            personal = User(username=names['personal'], password_hash=hash_password(password), role='personal', person_id=people[0].id)
            role = SysRole(code='hr', name='Synthetic live HR', data_scope='all')
            db.add_all([admin, personal, role]);db.flush()
            admin_id, role_id = admin.id, role.id
            db.add(SysUserRole(user_id=admin.id, role_id=role.id))
            people_ids, file_ids = [], []
            for i, person in enumerate(people):
                text = ('合成简历：星桥数据项目由本合成人员实施，使用Python和Redis。' if i == 0 else
                        '合成简历：远帆财务项目由另一名同名合成人员实施，使用Java和Kafka。')
                from docx import Document
                path = run_dir / f'profile-{i}.docx'
                source = Document();source.add_paragraph(text);source.save(path)
                import hashlib
                file = ResumeFile(filename=f'synthetic-profile-{i}.docx', stored_path=str(path),
                    file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), file_ext='docx', file_size=path.stat().st_size,
                    upload_batch_id=uuid4().hex[:26], uploaded_by=admin.id)
                db.add(file);db.flush()
                db.add_all([ParsedResume(resume_file_id=file.id, talent_profile_id=person.id,
                    raw_text=text, structured_json={'skills': person.skills_json}),
                    ParseTask(resume_file_id=file.id, status='success', stage='complete'),
                    ResumePage(resume_file_id=file.id, page_no=1, text_content=text, source_type='document', quality_status='ready')])
                db.flush();save_version(db, person, 'baseline', changed_by=admin.id)
                people_ids.append(person.id);file_ids.append(file.id)
            db.commit()
        report['person_ids'] = people_ids
        with TestClient(app) as client:
            def call(method, path, *, headers=None, expected=200, **kwargs):
                response = client.request(method, '/api' + path, headers=headers, **kwargs)
                assert response.status_code == expected, f'{method} {path}: {response.status_code}'
                if expected != 200:return None
                return response.json()['data']
            tokens = {}
            for role, name in names.items():
                data = call('POST', '/auth/login', json={'username': name, 'password': password})
                tokens[role] = {'Authorization': 'Bearer ' + data['token']}
            ah, ph = tokens['admin'], tokens['personal']
            def check(name, passed=True, **details):
                report['checks'].append({'name': name, 'passed': bool(passed), **details})
                assert passed, name
            def wait_job(path, headers, timeout=180):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    value = call('GET', path, headers=headers)
                    if value['status'] not in ('pending', 'running'):
                        assert value['status'] == 'completed', f'{path} ended {value["status"]}'
                        return value
                    time.sleep(.5)
                raise TimeoutError(path)
            def wait_revision(doc_id, revision_id, stage='completed', timeout=120):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    values = call('GET', '/knowledge/documents?limit=100', headers=ah)['items']
                    row = next(v for d in values if d['id'] == doc_id for v in d['versions'] if v['id'] == revision_id)
                    if row['status'] == 'completed' and row['stage'] == stage:return row
                    assert row['status'] != 'failed', f'knowledge revision failed at {row["stage"]}'
                    time.sleep(.5)
                raise TimeoutError('knowledge_ingest')
            report['stage'] = 'knowledge_ingest'
            docs = []
            private_marker = 'PRIVATE_' + marker
            for scope, text in [('public', '合成联调请假制度：请假须提前两个工作日提交申请，并经主管审批。'),
                                ('admin', '合成受限制度：内部专项代号为' + private_marker + '，仅管理员可见。')]:
                doc = call('POST', '/knowledge/documents', headers=ah, json={'title': '合成联调制度-' + scope, 'access_scope': scope})
                revision = call('POST', f'/knowledge/documents/{doc["id"]}/versions', headers=ah,
                    files={'file': ('synthetic-policy.txt', text.encode(), 'text/plain')})
                wait_revision(doc['id'], revision['id'])
                call('POST', f'/knowledge/documents/{doc["id"]}/publish', headers=ah, json={'revision_id': revision['id']})
                docs.append((doc['id'], revision['id']))
            check('knowledge_real_ingestion_and_publication', documents=2)
            public_doc, public_revision = docs[0]
            visible = call('GET', '/knowledge/documents', headers=ph)
            check('knowledge_public_admin_isolation', [d['id'] for d in visible['items']] == [public_doc])
            call('GET', f'/knowledge/versions/{docs[1][1]}/source', headers=ph, expected=403)
            report['stage'] = 'knowledge_question'
            value = call('POST', '/knowledge/questions', headers=ph,
                json={'question': '合成联调请假制度要求提前多久提交申请，需要谁审批？', 'idempotency_key': 'knowledge-' + marker})
            answered = wait_job(f'/knowledge/questions/{value["job_id"]}', ph)
            result = answered['result']
            check('knowledge_real_answer_with_citation', result['status'] == 'answered' and bool(result['citations']) and
                all(c['document_id'] == public_doc for c in result['citations']), answer=result['answer'])
            with SessionLocal() as db:
                audit = db.get(KnowledgeQuestionJob, value['job_id']).audit_json
                check('restricted_context_never_sent_to_model', all(c['document_id'] != docs[1][0] and
                    private_marker not in c['snippet'] for c in audit.get('context', [])), model_call_records=len(audit.get('model_calls', [])))
            restricted = call('POST', '/knowledge/questions', headers=ph,
                json={'question': '合成受限制度中的内部专项代号是什么？', 'idempotency_key': 'restricted-' + marker})
            refusal = wait_job(f'/knowledge/questions/{restricted["job_id"]}', ph)
            check('restricted_question_refuses_without_leaking_private_code',
                refusal['result']['status'] == 'insufficient_evidence' and private_marker not in refusal['result']['answer'])
            call('PATCH', f'/knowledge/documents/{public_doc}', headers=ah, json={'access_scope': 'admin'})
            hidden = call('GET', f'/knowledge/questions/{value["job_id"]}', headers=ph)
            check('knowledge_revocation_hides_historical_answer', hidden['status'] == 'source_unavailable' and hidden['result'] is None)
            call('PATCH', f'/knowledge/documents/{public_doc}', headers=ah, json={'access_scope': 'public'})
            report['stage'] = 'profile_question'
            own = call('POST', '/talents/question-jobs', headers=ph, json={'person_ids': [people_ids[0]],
                'question': '星桥数据项目使用了哪些技术？', 'idempotency_key': 'own-' + marker})
            own_answer = wait_job(f'/talents/question-jobs/{own["job_id"]}', ph)
            item = own_answer['items'][0]
            check('profile_real_answer_uses_own_frozen_source', item['status'] == 'answered' and item['profile_version'] == 1 and
                bool(item['citations']) and all(c['file_id'] == file_ids[0] for c in item['citations']), answer=item['answer'])
            call('POST', '/talents/question-jobs', headers=ph, expected=403, json={'person_ids': [people_ids[1]],
                'question': '另一人的经历？', 'idempotency_key': 'forbidden-' + marker})
            check('profile_other_person_denied_before_job_creation')
            batch = call('POST', '/talents/question-jobs', headers=ah, json={'person_ids': people_ids,
                'question': '此人的合成项目名称和技术栈是什么？', 'idempotency_key': 'batch-' + marker})
            batch_answer = wait_job(f'/talents/question-jobs/{batch["job_id"]}', ah)
            check('same_name_batch_citations_do_not_mix_people', batch_answer['answered'] == 2 and
                all(all(c['file_id'] == file_ids[people_ids.index(i['person_id'])] for c in i['citations']) for i in batch_answer['items']))
            report['stage'] = 'restart_recovery'
            runner.stop()
            recovery = call('POST', '/talents/question-jobs', headers=ph, json={'person_ids': [people_ids[0]],
                'question': '星桥数据项目是否使用Redis？', 'idempotency_key': 'recovery-' + marker})
            with SessionLocal() as db:
                db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id == recovery['job_id']).values(
                    status='running', stage='answering', lease_token='synthetic-interrupted-attempt', attempt_count=1));db.commit()
            runner.start()
            recovered = wait_job(f'/talents/question-jobs/{recovery["job_id"]}', ph)
            check('persisted_running_state_recovers_with_real_services', recovered['attempt_count'] == 2,
                boundary='simulated interrupted persisted state; not an OS kill')
            cancel = call('POST', '/knowledge/questions', headers=ph,
                json={'question': '合成请假需要审批吗？', 'idempotency_key': 'cancel-' + marker})
            cancelled = call('POST', f'/knowledge/questions/{cancel["job_id"]}/cancel', headers=ph)
            check('knowledge_cancel_terminal', cancelled['status'] == 'cancelled' and cancelled['result'] is None)
            report['stage'] = 'knowledge_delete'
            call('DELETE', f'/knowledge/documents/{public_doc}', headers=ah)
            check('deleted_document_not_recalled_before_physical_cleanup', public_doc not in
                [d['id'] for d in call('GET', '/knowledge/documents', headers=ph)['items']])
            wait_revision(public_doc, public_revision, stage='deleted')
            from app.modules.knowledge.index import collection as knowledge_collection
            counts = get_store().client.query(collection_name=knowledge_collection(), filter=f'document_id == {public_doc}',
                output_fields=['count(*)'], consistency_level='Strong')
            check('deleted_document_vectors_removed', counts[0]['count(*)'] == 0)
            report['stage'] = 'last_role_revocation'
            from sqlalchemy import delete
            with SessionLocal() as db:
                db.execute(delete(SysUserRole).where(SysUserRole.user_id == admin_id));db.commit()
            call('POST', '/knowledge/documents', headers=ah, expected=403,
                json={'title': 'synthetic revoked write', 'access_scope': 'public'})
            call('POST', '/talents/question-jobs', headers=ah, expected=403,
                json={'person_ids': [people_ids[0]], 'question': '撤权探针', 'idempotency_key': 'revoked-' + marker})
            check('last_scoped_role_revocation_does_not_restore_all')
            with SessionLocal() as db:
                db.add(SysUserRole(user_id=admin_id, role_id=role_id));db.commit()
            report['stage'], report['ok'] = 'completed', True
    except Exception as exc:
        report['failure'] = {'error_type': type(exc).__name__, 'stage': report['stage'],
            'assertion': str(exc)[:180] if isinstance(exc, (AssertionError, TimeoutError)) else ''}
    finally:
        if not args.keep_collections:
            client = get_store().client
            owned = [args.collection_prefix, args.collection_prefix + '_qa_chunks_v1', args.collection_prefix + '_knowledge_chunks_v1']
            for name in owned:
                if client.has_collection(collection_name=name):client.drop_collection(collection_name=name)
            report['collections_removed'] = owned
        else:report['collections_retained_for_owned_browser_session'] = True
        (run_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'stage': report['stage'], 'checks': report['checks'],
        'failure': report.get('failure'), 'report': str(run_dir / 'report.json')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--collection-prefix', required=True)
    parser.add_argument('--keep-collections', action='store_true')
    raise SystemExit(execute(parser.parse_args()))
