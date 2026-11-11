"""Graph projections are versioned literal evidence, not inferred capability."""
import pytest

from app.modules.talents.graph_projection import profile_projection, policy_projection, job_projection, tombstone
from app.core.errors import BizError


def profile(pid=1,version=2):
    return {'person_id':pid,'profile_version':version,'version_source':'persisted','status':'ready',
        'profile':{'name':'同名样例','skills':['Zig'],'phone':'SHOULD-NOT-BE-A-NODE-PROPERTY'},
        'parsed':{'certificates':['PMP','不存在证书']},'source_pages':[
            {'file_id':7,'filename':'synthetic.pdf','page_no':2,'quality_status':'ready',
             'text':'不会Java，计划学习Go。使用Python及Zig开发，持有PMP。'}]}


def test_profile_has_stable_versioned_root_and_cited_mentions_not_ability():
    data=profile_projection(profile())
    assert data['neo4j_state']=='not_connected' and data['root']['kind']=='Person'
    assert {n['kind'] for n in data['nodes']}=={'Person','Skill','Certificate'}
    assert 'SHOULD-NOT-BE-A-NODE-PROPERTY' not in str(data['nodes'])
    assert all(e['type'] in ('MENTIONS_SKILL','MENTIONS_CERTIFICATE') for e in data['edges'])
    assert all(e['review_status']=='pending_review' for e in data['edges'])
    assert all(e['evidence'][0]['source_version']==2 for e in data['edges'])
    assert any(e['evidence'][0]['context_status']=='negated' for e in data['edges'])
    assert any(n['label']=='zig' for n in data['nodes'])
    assert not any(n['label']=='不存在证书' for n in data['nodes'])
    assert profile_projection(profile())['fingerprint']==data['fingerprint']


def test_same_names_and_versions_are_not_merged_into_one_person():
    a=profile_projection(profile());b=profile_projection(profile(2));c=profile_projection(profile(1,3))
    assert len({a['root']['key'],b['root']['key'],c['root']['key']})==3
    assert len({a['fingerprint'],b['fingerprint'],c['fingerprint']})==3


def test_alias_and_repeated_mentions_produce_one_skill_edge_with_multiple_citations():
    saved=profile();saved['source_pages'][0]['text']='使用Go与golang，Google不是编程语言。'
    saved['profile']['skills']=[];saved['parsed']['certificates']=[]
    value=profile_projection(saved)
    assert len(value['edges'])==1 and len(value['edges'][0]['evidence'])==2
    assert len({n['key'] for n in value['nodes']})==2


def test_policy_citations_keep_revision_chunk_and_original_offsets():
    chunks=[{'id':17,'page_no':None,'source_type':'txt','offset_start':120,'text':'使用Python。'}]
    value=policy_projection(document_id=9,revision_id=10,version=3,title='合成制度',chunks=chunks)
    citation=value['edges'][0]['evidence'][0]
    assert citation['document_id']==9 and citation['revision_id']==10 and citation['chunk_id']==17
    assert citation['char_start']==122 and citation['page_no'] is None
    assert value['root']['kind']=='Policy'


def test_job_is_only_a_pure_contract_and_does_not_invent_requirements():
    value=job_projection({'job_id':5,'version':2,'title':'合成岗位','jd_text':'计划使用Go，不要求Java。',
        'must_skills':['未出现技能']})
    assert value['root']['kind']=='Job'
    assert all(e['type']=='MENTIONS_SKILL' for e in value['edges'])
    assert not any(n['label']=='未出现技能' for n in value['nodes'])


def test_no_source_and_bounds_do_not_look_like_full_graphs():
    saved=profile();saved['source_pages']=[]
    value=profile_projection(saved)
    assert value['edges']==[] and value['coverage']['complete'] is False
    saved['source_pages']=[{'file_id':7,'page_no':1,'text':'x'*2_000_001}]
    with pytest.raises(BizError) as error:profile_projection(saved)
    assert error.value.code==422
    saved=profile();saved['version_source']='baseline'
    with pytest.raises(BizError):profile_projection(saved)


def test_invalidation_contains_only_identifiers_and_rejects_free_queries():
    value=tombstone('profile',1,3)
    assert value['operation']=='invalidate' and 'nodes' not in value
    for kind,pid,version in [('MATCH (n) DELETE n',1,3),('profile',True,3),('profile',1,-1)]:
        with pytest.raises(BizError):tombstone(kind,pid,version)


@pytest.mark.parametrize('bad',[None,{'unexpected':'object'}])
def test_malformed_text_is_rejected_with_existing_business_error(bad):
    saved=profile();saved['source_pages'][0]['text']=bad
    with pytest.raises(BizError) as error:profile_projection(saved)
    assert error.value.code==422


def test_docx_parts_with_no_physical_page_are_not_false_deduplicates():
    saved=profile();saved['parsed']['certificates']=[];saved['profile']['skills']=[]
    saved['source_pages']=[{'file_id':7,'page_no':None,'text':'Python','quality_status':'ready'},
                           {'file_id':7,'page_no':None,'text':'Python','quality_status':'ready'}]
    edges=profile_projection(saved)['edges']
    assert len(edges)==1 and [e['source_part'] for e in edges[0]['evidence']]==[0,1]


def test_overlapping_policy_chunks_deduplicate_physical_mentions():
    value=policy_projection(document_id=1,revision_id=2,version=1,title='合成',chunks=[
        {'id':3,'page_no':1,'offset_start':0,'text':'使用Python。'},
        {'id':4,'page_no':1,'offset_start':2,'text':'Python。'}])
    assert len(value['edges'])==1 and len(value['edges'][0]['evidence'])==1


@pytest.mark.parametrize('skills',[['js','py','cpp'],['JavaScript','Python','C++']])
def test_public_skill_aliases_in_original_source_are_not_discarded(skills):
    value=job_projection({'job_id':1,'version':1,'title':'合成岗位',
        'jd_text':'使用 js、py、cpp 开发。','must_skills':skills})
    assert {n['label'] for n in value['nodes'] if n['kind']=='Skill'}=={'javascript','python','c++'}
    assert len(value['edges'])==3
