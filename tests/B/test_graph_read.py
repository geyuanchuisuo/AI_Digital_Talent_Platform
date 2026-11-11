"""Races must never return evidence from a replaced publication/version."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.core.errors import BizError


def test_completed_sql_status_does_not_hide_missing_graph_relations(monkeypatch):
    from app.modules.talents import graph_read,graph_preview
    from app.modules.talents.graph_projection import job_projection
    projection=job_projection(dict(job_id=1,version=1,title='Synthetic',jd_text='Python'))
    monkeypatch.setattr(graph_read.settings,'neo4j_enabled',True)
    state=SimpleNamespace(generation=1,operation='upsert',source_version=1)
    job=SimpleNamespace(status='completed',fingerprint=projection['fingerprint'])
    monkeypatch.setattr(graph_read,'state_for',lambda *a:(state,job))
    monkeypatch.setattr(graph_read,'job_view',lambda job:{'status':job.status})
    monkeypatch.setattr(graph_preview,'preview',lambda *a,**k:deepcopy(projection))
    class Transport:
        def read_projection(self,*args):
            return dict(nodes=[{k:n[k] for k in ('key','kind','label')} for n in projection['nodes']],edges=[])
    result=graph_read.read(None,'profile',1,user_id=7,client=Transport())
    assert result['neo4j_state']=='inconsistent'


def test_remote_cannot_inject_unapproved_source_fields(monkeypatch):
    from app.modules.talents import graph_read,graph_preview
    from app.modules.talents.graph_projection import job_projection
    projection=job_projection(dict(job_id=1,version=1,title='Synthetic',jd_text='Python'))
    monkeypatch.setattr(graph_read.settings,'neo4j_enabled',True)
    state=SimpleNamespace(generation=1,operation='upsert',source_version=1)
    job=SimpleNamespace(status='completed',fingerprint=projection['fingerprint'])
    monkeypatch.setattr(graph_read,'state_for',lambda *a:(state,job))
    monkeypatch.setattr(graph_read,'job_view',lambda job:{'status':job.status})
    monkeypatch.setattr(graph_preview,'preview',lambda *a,**k:deepcopy(projection))
    class Transport:
        def read_projection(self,*args):
            return dict(nodes=[{'key':'evil','kind':'Person','label':'other person secret'}],edges=[])
    result=graph_read.read(None,'profile',1,user_id=7,client=Transport())
    assert result['neo4j_state']=='inconsistent' and 'other person secret' not in str(result)


@pytest.mark.parametrize('enabled',[False,True])
def test_pending_read_rejects_source_changed_between_checks(monkeypatch,enabled):
    from app.modules.talents import graph_read,graph_preview
    monkeypatch.setattr(graph_read.settings,'neo4j_enabled',enabled)
    monkeypatch.setattr(graph_read,'state_for',lambda *a:(None,None))
    values=iter([dict(fingerprint='old',root={'source_version':1}),dict(fingerprint='new',root={'source_version':2})])
    monkeypatch.setattr(graph_preview,'preview',lambda *a,**k:next(values))
    with pytest.raises(BizError) as error:
        graph_read.read(None,'profile',1,user_id=7)
    assert error.value.code==409


@pytest.mark.parametrize('status,fingerprint,expected',[('failed',None,'failed'),('completed','old','outdated')])
def test_failure_and_outdated_projection_are_explicit(monkeypatch,status,fingerprint,expected):
    from app.modules.talents import graph_read,graph_preview
    projection=dict(fingerprint='current',root={'source_version':1})
    monkeypatch.setattr(graph_read.settings,'neo4j_enabled',True)
    state=SimpleNamespace(generation=1,operation='upsert',source_version=1)
    job=SimpleNamespace(status=status,fingerprint=fingerprint)
    monkeypatch.setattr(graph_read,'state_for',lambda *a:(state,job))
    monkeypatch.setattr(graph_read,'job_view',lambda job:{'status':job.status})
    monkeypatch.setattr(graph_preview,'preview',lambda *a,**k:deepcopy(projection))
    assert graph_read.read(None,'profile',1,user_id=7)['neo4j_state']==expected
