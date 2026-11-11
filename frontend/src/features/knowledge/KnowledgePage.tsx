'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Input,List,Popconfirm,Select,Space,Tag,Typography} from 'antd';
import {get,post,patch,del,downloadFile,getErrorMessage} from '@/lib/http';
import KnowledgeQuestion from './KnowledgeQuestion';

interface Revision {id:number;version:number;filename:string;status:string;stage:string;error?:string;}
interface Document {id:number;title:string;access_scope:string;enabled:boolean;expires_at:string|null;
  deleted_at:string|null;published_revision_id:number|null;versions:Revision[];}
interface Documents {items:Document[];total:number;can_manage:boolean;}
const statusLabel:Record<string,string>={pending:'等待处理',running:'处理中',completed:'已完成',failed:'处理失败',cancelled:'已取消'};
export default function KnowledgePage({readOnly=false}:{readOnly?:boolean}) {
  const [data,setData]=useState<Documents>({items:[],total:0,can_manage:false});
  const [offset,setOffset]=useState(0),[title,setTitle]=useState(''),[scope,setScope]=useState('admin');
  const [expires,setExpires]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [refreshKey,setRefreshKey]=useState(0),[expiryDrafts,setExpiryDrafts]=useState<Record<number,string>>({});
  const generation=useRef(0);const mounted=useRef(false);
  const load=useCallback(async()=>{
    const seq=++generation.current;
    try {const value=await get<Documents>(`/knowledge/documents?offset=${offset}&limit=20`);
      if(mounted.current&&seq===generation.current){setData(value);setError('');}}
    catch(e){if(mounted.current&&seq===generation.current){setError(getErrorMessage(e));
      if([401,403].includes((e as {code?:number}).code||0))setData({items:[],total:0,can_manage:false});}}
  },[offset]);
  useEffect(()=>{mounted.current=true;void load();return()=>{mounted.current=false;generation.current++;};},[load]);
  const processing=data.items.some(doc=>doc.versions.some(v=>['pending','running'].includes(v.status)));
  useEffect(()=>{if(!processing)return;const timer=setTimeout(()=>void load(),2000);return()=>clearTimeout(timer);},[data,processing,load]);
  const manage=data.can_manage&&!readOnly;
  const mutate=async(action:()=>Promise<unknown>)=>{setBusy(true);setError('');
    try {await action();if(mounted.current){setRefreshKey(v=>v+1);await load();}}
    catch(e){if(mounted.current)setError(getErrorMessage(e));}
    finally{if(mounted.current)setBusy(false);}};
  const create=()=>mutate(async()=>{await post('/knowledge/documents',{
    title,access_scope:scope,expires_at:expires||null});setTitle('');});
  const upload=(id:number,file:File)=>mutate(async()=>{const form=new FormData();form.append('file',file);
    await post(`/knowledge/documents/${id}/versions`,form);});
  const download=async(v:Revision)=>{try {await downloadFile(`/knowledge/versions/${v.id}/source`,v.filename);}
    catch(e){setError(getErrorMessage(e));}};
  return <Space direction="vertical" size="large" style={{width:'100%'}}>
    <Typography.Title level={2}>{readOnly?'制度知识与问答':'知识库'}</Typography.Title>
    {error&&<Alert type="warning" message={error}/>}
    {manage&&<Card title="新建制度文档"><Space direction="vertical" style={{width:'100%'}}>
      <Alert type="info" message="公开文档可供所有已登录账号阅读。请确认原件、范围及到期时间后发布。"/>
      <Input aria-label="文档标题" value={title} maxLength={200} onChange={e=>setTitle(e.target.value)}/>
      <Select aria-label="文档范围" value={scope} onChange={setScope} style={{minWidth:240}}
        options={[{value:'admin',label:'管理员（全量管理范围）'},{value:'public',label:'公开（已登录用户）'}]}/>
      <Input aria-label="文档到期时间" type="datetime-local" value={expires} onChange={e=>setExpires(e.target.value)}/>
      <Button disabled={busy||!title.trim()} onClick={()=>void create()}>新建文档</Button>
    </Space></Card>}
    <Card title={`制度文档（${data.total}）`} extra={<Button onClick={()=>void load()}>刷新文档</Button>}>
      <List dataSource={data.items} locale={{emptyText:'暂无已发布且可访问的制度'}} renderItem={doc=><List.Item>
        <Space direction="vertical" style={{width:'100%'}}>
          <Typography.Text strong style={{overflowWrap:'anywhere'}}>{doc.title}</Typography.Text>
          <Space wrap><Tag>{doc.access_scope==='public'?'公开':'管理员'}</Tag>
            {doc.deleted_at?<Tag color="red">已删除</Tag>:!doc.enabled?<Tag>已停用</Tag>:null}
            {doc.expires_at&&<Typography.Text>到期：{doc.expires_at.replace('T',' ')}</Typography.Text>}</Space>
          {manage&&!doc.deleted_at&&<Space wrap>
            <label>上传版本（PDF/DOCX/TXT）<input type="file" aria-label={`上传版本：${doc.title}`} accept=".pdf,.docx,.txt" disabled={busy}
              onChange={e=>{const file=e.target.files?.[0];if(file)void upload(doc.id,file);e.target.value='';}}/></label>
            <Select aria-label={`范围：${doc.title}`} value={doc.access_scope} disabled={busy}
              options={[{value:'admin',label:'管理员'},{value:'public',label:'公开'}]}
              onChange={value=>void mutate(()=>patch(`/knowledge/documents/${doc.id}`,{access_scope:value}))}/>
            <Button disabled={busy} onClick={()=>void mutate(()=>patch(`/knowledge/documents/${doc.id}`,{enabled:!doc.enabled}))}>{doc.enabled?'停用':'启用'}</Button>
            <Input aria-label={`到期：${doc.title}`} type="datetime-local" value={expiryDrafts[doc.id]??doc.expires_at?.slice(0,16)??''}
              onChange={e=>setExpiryDrafts(v=>({...v,[doc.id]:e.target.value}))}/>
            <Button disabled={busy} onClick={()=>void mutate(()=>patch(`/knowledge/documents/${doc.id}`,{
              expires_at:(expiryDrafts[doc.id]??doc.expires_at)||null}))}>更新到期时间</Button>
            <Popconfirm title="删除后停止检索，原件和向量将进入清理流程，确定删除？" onConfirm={()=>mutate(()=>del(`/knowledge/documents/${doc.id}`))}>
              <Button danger disabled={busy}>删除文档</Button></Popconfirm>
          </Space>}
          {doc.versions.map(v=><Space key={v.id} wrap><Typography.Text style={{overflowWrap:'anywhere'}}>版本 {v.version} · {v.filename} · {v.stage==='deleted'?'清理完成':statusLabel[v.status]||v.status}</Typography.Text>
            {doc.published_revision_id===v.id&&<Tag color="green">当前发布</Tag>}
            {v.error&&<Typography.Text type="danger">{v.error}</Typography.Text>}
            {!doc.deleted_at&&<Button onClick={()=>void download(v)}>下载原件</Button>}
            {manage&&!doc.deleted_at&&v.status==='completed'&&v.stage==='completed'&&doc.published_revision_id!==v.id&&
              <Button disabled={busy} onClick={()=>void mutate(()=>post(`/knowledge/documents/${doc.id}/publish`,{revision_id:v.id}))}>发布版本 {v.version}</Button>}
            {manage&&!['pending','running'].includes(v.status)&&<Button disabled={busy} onClick={()=>void mutate(()=>post(`/knowledge/versions/${v.id}/retry`))}>
              {doc.deleted_at?'重试清理':'重试或重建索引'}</Button>}
          </Space>)}
        </Space></List.Item>}/>
      <Space><Button disabled={offset===0} onClick={()=>setOffset(v=>Math.max(0,v-20))}>上一页</Button>
        <Button disabled={offset+20>=data.total} onClick={()=>setOffset(v=>v+20)}>下一页</Button></Space>
    </Card>
    <KnowledgeQuestion refreshKey={refreshKey}/>
  </Space>;
}
