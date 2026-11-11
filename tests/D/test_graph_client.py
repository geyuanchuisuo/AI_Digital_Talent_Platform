"""Driver boundary tests; no connection to a Neo4j service."""
from copy import deepcopy
import pytest


def projection():
    from app.modules.talents.graph_projection import job_projection
    return job_projection({'job_id': 1, 'version': 1, 'title': 'Test', 'jd_text': 'Python'})


def test_validation_rejects_unknown_relation_and_cross_root():
    from app.core.ai.graph_client import validate_projection
    value = projection()
    validate_projection('job', 1, value)
    for mutate in (lambda p: p['edges'][0].update(type='HAS_SKILL'),
                   lambda p: p['root'].update(source_id=2),
                   lambda p: p['edges'][0].update(to='unrelated')):
        changed = deepcopy(value); mutate(changed)
        with pytest.raises(ValueError):
            validate_projection('job', 1, changed)


def test_disabled_client_never_creates_driver():
    from app.core.ai.graph_client import GraphClient, GraphUnavailable
    with pytest.raises(GraphUnavailable):
        GraphClient(enabled=False, uri='', username='', password='', database='neo4j', namespace='test')


def test_namespace_and_generation_are_mandatory():
    from app.core.ai.graph_client import GraphClient
    for namespace in ('', 'unsafe space', 'x' * 81):
        with pytest.raises(ValueError):
            GraphClient(enabled=True, uri='bolt://localhost:9', username='unit', password='unit',
                        database='neo4j', namespace=namespace)


def test_connection_uri_cannot_embed_credentials():
    from app.core.ai.graph_client import GraphClient
    with pytest.raises(ValueError,match='URI'):
        GraphClient(enabled=True,uri='bolt://user:do-not-log@localhost:7687',username='unit',
            password='unit',database='neo4j',namespace='test')


def test_static_queries_keep_lock_fence_and_scoped_delete():
    from app.core.ai import graph_client as g
    assert 'CREATE CONSTRAINT' in g.SCHEMA
    assert 'IS UNIQUE' in g.SCHEMA
    assert 'SET s.lock' in g.FENCE
    assert '$generation' in g.FENCE and '$namespace' in g.FENCE
    assert '$source_key' in g.REMOVE and '$namespace' in g.REMOVE
    assert '$generation' in g.READ and '$fingerprint' in g.READ
    assert '$namespace' in g.READ


class Result:
    def __init__(self,value=None):self.value=value
    def single(self):return self.value
    def consume(self):return None


class Driver:
    def __init__(self,accepted):self.accepted=accepted;self.calls=[]
    def session(self,**kwargs):
        self.database=kwargs['database'];return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute_write(self,callback):return callback(self)
    def run(self,query,**params):
        self.calls.append((query,params))
        return Result({'generation':params['generation']} if self.accepted else None)


def test_stale_remote_fence_prevents_all_destructive_writes():
    from app.core.ai.graph_client import GraphClient,FENCE
    driver=Driver(False)
    client=GraphClient(enabled=True,uri='bolt://unit.invalid',username='unit',password='unit',
        namespace='isolated_test',database='neo4j',driver=driver)
    assert client.replace('job',1,1,projection()) is False
    assert len(driver.calls)==1 and driver.calls[0][0]==FENCE


def test_successful_write_uses_parameters_and_namespace_for_every_query():
    from app.core.ai.graph_client import GraphClient
    driver=Driver(True)
    value=projection()
    value['root']['label']="quote ' ); MATCH (n) DETACH DELETE n //"
    client=GraphClient(enabled=True,uri='bolt://unit.invalid',username='unit',password='unit',
        namespace='isolated_test',database='neo4j',driver=driver)
    assert client.replace('job',1,2,value)
    assert len(driver.calls)==4
    for query,params in driver.calls:
        assert params['namespace']=='isolated_test' and params['source_key']=='job:1'
        assert value['root']['label'] not in query
