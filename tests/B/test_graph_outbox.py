"""Transactional generation/outbox tests using independent in-memory SQLite."""
import pytest
import os
from pathlib import Path
import subprocess
import sys
from sqlalchemy import create_engine, MetaData, Integer, DefaultClause, text, select
from sqlalchemy.orm import Session


def test_graph_models_can_be_first_import_without_circular_registration():
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[2]/'backend'),
         'MYSQL_DB':'ai_talent_b09_import_only'}
    result=subprocess.run([sys.executable,'-B','-c',
        'from app.modules.talents.graph_models import GRAPH_MODELS; import app.models; assert len(GRAPH_MODELS)==2'],
        env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


@pytest.fixture
def graph_db():
    from app.modules.talents.graph_models import GRAPH_MODELS
    engine = create_engine('sqlite://')
    meta = MetaData()
    for model in GRAPH_MODELS:
        table = model.__table__.to_metadata(meta)
        for col in table.columns:
            if col.primary_key: col.type = Integer()
            if col.name == 'updated_at': col.server_default = DefaultClause(text('CURRENT_TIMESTAMP'))
    meta.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def test_intent_is_transactional_monotonic_and_keeps_tombstone(graph_db):
    from app.modules.talents.graph_outbox import enqueue
    from app.modules.talents.graph_models import GraphSource, GraphSyncJob
    db = graph_db
    first = enqueue(db, 'profile', 12, 1, operation='upsert', requested_by=7)
    assert first.generation == 1
    db.rollback()
    assert db.scalar(select(GraphSyncJob)) is None
    first = enqueue(db, 'profile', 12, 1, operation='upsert', requested_by=7)
    db.commit()
    deleted = enqueue(db, 'profile', 12, 2, operation='invalidate', requested_by=7)
    db.commit()
    assert deleted.generation == 2
    assert first.status == 'cancelled'
    state = db.scalar(select(GraphSource))
    assert state.generation == 2 and state.operation == 'invalidate'
    assert not hasattr(state, 'projection_json')
    restored = enqueue(db, 'profile', 12, 3, operation='upsert', requested_by=7)
    assert restored.generation == 3


def test_duplicate_intent_does_not_reset_retry_budget(graph_db):
    from app.modules.talents.graph_outbox import enqueue
    one = enqueue(graph_db, 'job', 3, 1, operation='upsert', requested_by=7)
    one.status = 'failed'; one.attempt_count = 3
    graph_db.commit()
    two = enqueue(graph_db, 'job', 3, 1, operation='upsert', requested_by=7)
    assert two.id == one.id and two.attempt_count == 3
