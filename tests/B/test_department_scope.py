from types import SimpleNamespace as N
from unittest.mock import MagicMock
import pytest
from app.core.auth import scope as auth
from test_round1_versions import db


def test_real_role_department_and_expired_grants_are_applied(db,monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime,timedelta
    from sqlalchemy import MetaData,Integer,text,DefaultClause
    from app.modules.system.models import SysRole,SysUserRole,SysDepartment
    from app.modules.recruitment.scope_models import UserScopeGrant
    from app.models.talent import TalentProfile
    from app.database import SessionLocal
    meta=MetaData()
    for cls in [SysRole,SysUserRole,SysDepartment,UserScopeGrant]:
        table=cls.__table__.to_metadata(meta)
        for col in table.columns:
            if col.primary_key:col.type=Integer()
            if col.name=='updated_at':col.server_default=DefaultClause(text('CURRENT_TIMESTAMP'))
    meta.create_all(db.get_bind())
    db.add_all([SysDepartment(id=10,name='本部'),SysDepartment(id=20,name='外部'),
        SysRole(id=1,code='limited',name='部门管理',data_scope='dept'),
        SysUserRole(user_id=5,role_id=1),
        TalentProfile(id=101,name='本人',department_id=10),
        TalentProfile(id=102,name='同部门',department_id=10),
        TalentProfile(id=103,name='其他部门',department_id=20),
        UserScopeGrant(granter_user_id=1,grantee_user_id=5,scope_type='department',scope_ref_id=20,
            reason='已过期',valid_from=datetime.now()-timedelta(days=2),valid_until=datetime.now()-timedelta(days=1))])
    db.commit()
    @contextmanager
    def factory():yield db
    monkeypatch.setattr('app.database.SessionLocal',factory)
    scope=auth.get_account_scope(N(id=5,role='hr',person_id=101))
    assert scope.allowed_person_ids==(101,102)
    assert scope.department_ids==(10,)
    assert not auth.authorize_profile_access(scope,103).allowed


def test_explicit_management_scope_is_not_legacy_all(monkeypatch):
    monkeypatch.setattr(auth,'_management_range',lambda uid,pid:(False,(10,),(42,),()))
    result=auth.get_account_scope(N(id=5,role='hr',person_id=9))
    assert not result.can_view_all_persons
    assert auth.authorize_profile_access(result,42).allowed
    assert not auth.authorize_profile_access(result,43).allowed


def test_authorization_lookup_failure_is_closed(monkeypatch):
    monkeypatch.setattr(auth,'_management_range',lambda *a:(_ for _ in ()).throw(RuntimeError('db down')))
    result=auth.get_account_scope(N(id=5,role='hr',person_id=None))
    assert not result.can_view_all_persons
    assert not auth.authorize_profile_access(result,42).allowed


def test_disabled_account_cannot_use_legacy_hr_access():
    scope=auth.get_account_scope(N(id=5,role='hr',person_id=None,is_active=False))
    assert not auth.authorize_profile_access(scope,42).allowed


def test_snapshot_consumes_explicit_person_set():
    from app.service.talent_service import _normalize_scope,_ensure_scope,SnapshotScopeError
    shared=auth.AccountScope('hr',None,False,'admin',allowed_person_ids=(42,))
    normalized=_normalize_scope(shared)
    _ensure_scope(normalized,[42])
    with pytest.raises(SnapshotScopeError):_ensure_scope(normalized,[43])


def test_list_filters_rows_and_total_before_pagination(db):
    from app.models.talent import TalentProfile
    from app.service.talent_service import list_talents
    db.add_all([TalentProfile(id=801,name='范围内'),TalentProfile(id=802,name='范围外')]);db.commit()
    scope=auth.AccountScope('hr',None,False,'admin',allowed_person_ids=(801,))
    rows,total=list_talents(db,scope=scope)
    assert total==1 and [r['id'] for r in rows]==[801]


def test_search_filters_in_milvus_and_rechecks_hits(db,monkeypatch):
    from app.models.talent import TalentProfile
    from app.service.talent_service import search_talents
    from app.core import embedding_client,milvus_store
    db.add_all([TalentProfile(id=801,name='范围内'),TalentProfile(id=802,name='范围外')]);db.commit()
    captured={}
    def search(**kwargs):
        captured.update(kwargs)
        return [[{'id':801,'distance':0.9},{'id':802,'distance':0.8}]]
    monkeypatch.setattr(embedding_client,'embed_query',lambda q:[1.0,0.0])
    monkeypatch.setattr(milvus_store,'get_store',lambda:N(client=N(search=search),collection='test',ensure_collection=lambda:None))
    scope=auth.AccountScope('hr',None,False,'admin',allowed_person_ids=(801,))
    rows=search_talents(db,query='技能',top_k=10,scope=scope)
    assert captured['filter']=='id in [801]'
    assert [r['id'] for r in rows]==[801]
