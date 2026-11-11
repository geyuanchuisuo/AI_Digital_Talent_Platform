"""Every graph read starts and ends with SQL-authorized evidence reconstruction."""
from app.config import settings
from app.core.errors import BizError
from app.modules.talents.graph_service import state_for,job_view
from app.modules.talents.graph_runtime import get_client


def read(db,kind,source_id,*,user_id,client=None):
    def authorized():
        if kind=='profile':
            from app.modules.talents.graph_preview import preview
            return preview(db,source_id,user_id=user_id)
        if kind=='knowledge':
            from app.modules.knowledge.graph_preview import preview
            return preview(db,source_id,user_id=user_id)
        raise BizError(422,'不支持的图谱读取类型')
    result=authorized()
    state,job=state_for(db,kind,source_id)
    result['sync']=job_view(job) if job else None
    result['neo4j_state']='disabled' if not settings.neo4j_enabled else 'pending'
    if settings.neo4j_enabled:
        if state is None or state.operation!='upsert':
            result['neo4j_state']='awaiting_confirmation' if kind=='profile' else 'not_materialized'
        elif job is not None and job.status=='failed':
            result['neo4j_state']='failed'
        elif job is not None and job.status=='completed' and job.fingerprint!=result['fingerprint']:
            result['neo4j_state']='outdated'
    if (not settings.neo4j_enabled or state is None or job is None or state.operation!='upsert'
        or job.status!='completed' or job.fingerprint!=result['fingerprint']
        or state.source_version!=result['root']['source_version']):
        fresh=authorized()
        if fresh['fingerprint']!=result['fingerprint']:
            raise BizError(409,'来源版本已变化，请重试')
        return result
    expected=(state.generation,result['fingerprint'])
    try:
        remote=(client or get_client()).read_projection(kind,source_id,*expected)
    except Exception:
        result['neo4j_state']='unavailable'
    else:
        # Never return remote fields: use freshly authorized SQL evidence only.
        wanted_nodes=[{k:n[k] for k in ('key','kind','label')} for n in result['nodes']]
        wanted_edges=[{k:e[k] for k in ('key','from','to','type','evidence','review_status','ability_verified')} for e in result['edges']]
        same=(sorted(remote['nodes'],key=lambda n:n['key'])==sorted(wanted_nodes,key=lambda n:n['key']) and
              sorted(remote['edges'],key=lambda e:e['key'])==sorted(wanted_edges,key=lambda e:e['key']))
        result['neo4j_state']='synced' if same else 'inconsistent'
    fresh=authorized()
    current,current_job=state_for(db,kind,source_id)
    if (fresh['fingerprint']!=expected[1] or current is None or current.generation!=expected[0]
        or current_job is None or current_job.status!='completed'):
        raise BizError(409,'来源或同步状态已变化，请重试')
    return result
