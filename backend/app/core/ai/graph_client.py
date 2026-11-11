"""Fixed-query, namespace-scoped graph transport. SQL callers own authorization.

Each source owns its nodes, including terms, so no adjacency reveals other roots.
The unique source fence serializes generations; tombstones retain no source text.
"""
import json
import re
from urllib.parse import urlsplit

SCHEMA = 'CREATE CONSTRAINT b_graph_source IF NOT EXISTS FOR (s:BGraphSource) REQUIRE (s.namespace, s.source_key) IS UNIQUE'
FENCE = '''MERGE (s:BGraphSource {namespace:$namespace, source_key:$source_key})
ON CREATE SET s.generation=0
SET s.lock=coalesce(s.lock,0)+1
WITH s WHERE s.generation < $generation OR
 (s.generation = $generation AND s.fingerprint = $fingerprint)
SET s.generation=$generation, s.fingerprint=$fingerprint, s.deleted=$deleted
RETURN s.generation AS generation'''
REMOVE = '''MATCH (n:BGraphNode {namespace:$namespace, source_key:$source_key}) DETACH DELETE n'''
NODES = '''UNWIND $nodes AS item
CREATE (n:BGraphNode {namespace:$namespace, source_key:$source_key, key:item.key,
 kind:item.kind, label:item.label, generation:$generation})'''
EDGES = '''UNWIND $edges AS item
MATCH (a:BGraphNode {namespace:$namespace, source_key:$source_key, key:item.from}),
 (b:BGraphNode {namespace:$namespace, source_key:$source_key, key:item.to})
CREATE (a)-[:BGRAPH_LINK {key:item.key, type:item.type, evidence:item.evidence,
 review_status:'pending_review', ability_verified:false}]->(b)'''
READ = '''MATCH (s:BGraphSource {namespace:$namespace,source_key:$source_key,
 generation:$generation,fingerprint:$fingerprint,deleted:false})
MATCH (n:BGraphNode {namespace:$namespace,source_key:$source_key,generation:$generation})
RETURN n.key AS key, n.kind AS kind, n.label AS label ORDER BY key LIMIT 302'''
READ_EDGES = '''MATCH (s:BGraphSource {namespace:$namespace,source_key:$source_key,
 generation:$generation,fingerprint:$fingerprint,deleted:false})
MATCH (a:BGraphNode {namespace:$namespace,source_key:$source_key,generation:$generation})
 -[e:BGRAPH_LINK]->(b:BGraphNode {namespace:$namespace,source_key:$source_key,generation:$generation})
RETURN e.key AS key,a.key AS `from`,b.key AS `to`,e.type AS type,e.evidence AS evidence,
 e.review_status AS review_status,e.ability_verified AS ability_verified ORDER BY key LIMIT 301'''


class GraphUnavailable(RuntimeError):
    pass


def validate_projection(kind, source_id, projection):
    if kind not in ('profile', 'knowledge', 'job') or type(source_id) is not int or source_id < 1:
        raise ValueError('Invalid graph source')
    if projection is None:
        return
    root = projection['root']
    expected = {'profile':'Person','knowledge':'Policy','job':'Job'}[kind]
    if (root['source_kind'], root['source_id'], root['kind']) != (kind, source_id, expected):
        raise ValueError('Graph source mismatch')
    nodes, edges = projection['nodes'], projection['edges']
    if len(nodes) > 301 or len(edges) > 300 or root not in nodes:
        raise ValueError('Invalid graph bounds')
    keys = {n['key'] for n in nodes}
    if len(keys) != len(nodes):
        raise ValueError('Duplicate graph node')
    kinds = {n['key']:n['kind'] for n in nodes}
    for node in nodes:
        if (node['kind'] not in (expected,'Skill','Certificate') or
            (node['kind'] == expected and node['key'] != root['key']) or
            not isinstance(node['label'], str) or len(node['label']) > 300):
            raise ValueError('Invalid graph node')
    for edge in edges:
        target = {'MENTIONS_SKILL':'Skill','MENTIONS_CERTIFICATE':'Certificate'}.get(edge['type'])
        if (target is None or edge['from'] != root['key'] or kinds.get(edge['to']) != target
            or edge.get('ability_verified') is not False or edge.get('review_status') != 'pending_review'
            or not isinstance(edge.get('evidence'),list) or not 1 <= len(edge['evidence']) <= 8):
            raise ValueError('Invalid graph relationship')
    if len(json.dumps(projection,ensure_ascii=False)) > 2_000_000:
        raise ValueError('Graph projection exceeds bound')


class GraphClient:
    def __init__(self, *, enabled, uri, username, password, database, namespace, timeout=10, driver=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',namespace):
            raise ValueError('Invalid graph namespace')
        if not enabled or not all((uri,username,password,database)):
            raise GraphUnavailable('Graph is disabled or unconfigured')
        parsed=urlsplit(uri)
        if parsed.username is not None or parsed.password is not None or parsed.scheme not in (
            'bolt','bolt+s','bolt+ssc','neo4j','neo4j+s','neo4j+ssc') or not parsed.hostname:
            raise ValueError('Invalid graph URI; pass credentials separately')
        self.namespace, self.database, self.timeout = namespace, database, max(1,min(timeout,60))
        if driver is None:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(username,password), connection_timeout=self.timeout,
                connection_acquisition_timeout=self.timeout, max_transaction_retry_time=self.timeout)
        self.driver = driver

    def prepare(self):
        from neo4j import Query
        with self.driver.session(database=self.database) as session:
            session.run(Query(SCHEMA,timeout=self.timeout)).consume()

    def replace(self, kind, source_id, generation, projection):
        validate_projection(kind,source_id,projection)
        if type(generation) is not int or generation < 1:
            raise ValueError('Invalid graph generation')
        params = dict(namespace=self.namespace,source_key=f'{kind}:{source_id}',generation=generation,
            fingerprint=projection['fingerprint'] if projection else 'deleted',deleted=projection is None)
        from neo4j import unit_of_work
        @unit_of_work(timeout=self.timeout)
        def write(tx):
            if tx.run(FENCE,**params).single() is None:
                return False
            tx.run(REMOVE,**params).consume()
            if projection is not None:
                tx.run(NODES,**params,nodes=[{k:n[k] for k in ('key','kind','label')} for n in projection['nodes']]).consume()
                tx.run(EDGES,**params,edges=[{**{k:e[k] for k in ('key','from','to','type')},
                    'evidence':json.dumps(e['evidence'],ensure_ascii=False)} for e in projection['edges']]).consume()
            return True
        with self.driver.session(database=self.database) as session:
            return session.execute_write(write)

    def read_projection(self, kind, source_id, generation, fingerprint):
        validate_projection(kind,source_id,None)
        if type(generation) is not int or generation < 1:
            raise ValueError('Invalid graph generation')
        from neo4j import unit_of_work
        @unit_of_work(timeout=self.timeout)
        def read(tx):
            params=dict(namespace=self.namespace,source_key=f'{kind}:{source_id}',generation=generation,fingerprint=fingerprint)
            nodes=[dict(row) for row in tx.run(READ,**params)]
            edges=[dict(row) for row in tx.run(READ_EDGES,**params)]
            for edge in edges:
                edge['evidence']=json.loads(edge['evidence'])
            return dict(nodes=nodes,edges=edges)
        with self.driver.session(database=self.database) as session:
            return session.execute_read(read)

    def close(self):
        self.driver.close()
