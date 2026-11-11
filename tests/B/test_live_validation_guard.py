import importlib.util
from pathlib import Path
import pytest


def test_live_validation_rejects_business_and_shared_targets():
    path=Path(__file__).resolve().parents[2]/'scripts/B/validate_live_rag.py'
    spec=importlib.util.spec_from_file_location('b_live_guard',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for database, collection in [('ai_talent','talent_vectors'),('ai_talent_b_live_20261111_a123','talent_vectors'),
                                 ('ai_talent_b_live_x;drop','b_live_20261111_a123')]:
        with pytest.raises(ValueError):module.validate_targets(database,collection)
    module.validate_targets('ai_talent_b_live_20261111_a123','b_live_20261111_a123')


def test_live_validation_rejects_mismatched_or_occupied_run_namespaces():
    path=Path(__file__).resolve().parents[2]/'scripts/B/validate_live_rag.py'
    spec=importlib.util.spec_from_file_location('b_live_guard',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.validate_targets('ai_talent_b_live_20261111_a123','b_live_20261111_b456')
    with pytest.raises(ValueError):
        module.validate_targets('ai_talent_b_live_20261111_a123','b_live_20261111_a123',
            ['b_live_20261111_a123_knowledge_chunks_v1'])


def test_live_fixture_uses_a_fresh_process_only_signing_key(monkeypatch):
    import os
    path=Path(__file__).resolve().parents[2]/'scripts/B/validate_live_rag.py'
    spec=importlib.util.spec_from_file_location('b_live_guard',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for key in ('MYSQL_DB','MILVUS_COLLECTION','UPLOAD_DIR','JWT_SECRET'):
        monkeypatch.setenv(key,'synthetic_original')
    module.isolated_environment('ai_talent_b_live_20261111_a123','b_live_20261111_a123')
    first=os.environ['JWT_SECRET']
    assert first != 'synthetic_original' and len(first)==64
    module.isolated_environment('ai_talent_b_live_20261111_b456','b_live_20261111_b456')
    assert os.environ['JWT_SECRET'] != first
