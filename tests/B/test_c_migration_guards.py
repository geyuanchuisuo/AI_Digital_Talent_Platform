"""Integrated C guards: verify existing and absent schema branches without DDL."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
import pytest


def load(name):
    path=Path(__file__).parents[2]/'backend/migrations/versions'/name
    spec=importlib.util.spec_from_file_location('guard_'+path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('exists',[False,True])
def test_application_fk_guard(monkeypatch,exists):
    m=load('C_0004_application_fk.py');op=MagicMock();monkeypatch.setattr(m,'op',op)
    inspector=MagicMock()
    inspector.get_foreign_keys.side_effect=lambda table:[{'name':'fk_'+table+'_application'}] if exists else []
    monkeypatch.setattr(m.sa,'inspect',lambda bind:inspector)
    m.upgrade()
    assert op.create_foreign_key.call_count==(0 if exists else 2)
    assert op.alter_column.call_count==2


@pytest.mark.parametrize('filename,column',[
    ('C_0005_plan_item_due_date.py','due_date'),('C_0006_course_capacity.py','capacity')])
@pytest.mark.parametrize('exists',[False,True])
def test_column_guard(monkeypatch,filename,column,exists):
    m=load(filename);op=MagicMock();monkeypatch.setattr(m,'op',op)
    inspector=MagicMock();inspector.get_columns.return_value=[{'name':column}] if exists else []
    monkeypatch.setattr(m.sa,'inspect',lambda bind:inspector)
    m.upgrade()
    assert op.add_column.call_count==(0 if exists else 1)


@pytest.mark.parametrize('exists',[False,True])
def test_learning_key_guard_keeps_null_only_backfill(monkeypatch,exists):
    m=load('C_0007_learning_evidence_key.py');op=MagicMock();monkeypatch.setattr(m,'op',op)
    inspector=MagicMock()
    inspector.get_columns.return_value=[{'name':'evidence_key'}] if exists else []
    inspector.get_unique_constraints.return_value=[{'name':'uk_evidence_record_key'}] if exists else []
    monkeypatch.setattr(m.sa,'inspect',lambda bind:inspector)
    m.upgrade()
    assert op.add_column.call_count==(0 if exists else 1)
    assert op.create_unique_constraint.call_count==(0 if exists else 1)
    assert 'WHERE evidence_key IS NULL' in op.execute.call_args.args[0]
