'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Input,List,Space,Typography} from 'antd';
import {get,post,downloadFile,getErrorMessage} from '@/lib/http';

interface Citation {citation_id:number;document_id:number;revision_id:number;version:number;
  document_title:string;filename:string;page_no:number|null;snippet:string;offset_start:number;offset_end:number;}
export interface KnowledgeJob {job_id:number;question:string;status:string;error?:string;
  result:{status:string;answer:string;citations:Citation[]}|null;}
export default function KnowledgeQuestion({refreshKey=0}:{refreshKey?:number}) {
  const [question,setQuestion]=useState('');const [job,setJob]=useState<KnowledgeJob|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const generation=useRef(0),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const request=useRef<{question:string;key:string}|null>(null);
  const stop=()=>{generation.current++;if(timer.current)clearTimeout(timer.current);timer.current=null;};
  const accept=(value:KnowledgeJob,seq:number)=>{
    if(seq!==generation.current)return;
    setJob(value);setError('');const active=['pending','running'].includes(value.status);setBusy(active);
    if(!active)request.current=null;
    if(value.status==='failed')setError(value.error||'制度问答未完成');
    if(active)timer.current=setTimeout(()=>void poll(value.job_id,seq),1500);
  };
  const poll=async(id:number,seq:number)=>{
    try {accept(await get<KnowledgeJob>(`/knowledge/questions/${id}`),seq);}
    catch(e){if(seq===generation.current){setBusy(false);setError(getErrorMessage(e));
      if([401,403,409].includes((e as {code?:number}).code||0))setJob(null);}}
  };
  useEffect(()=>{
    stop();const seq=generation.current;setJob(null);setError('');setBusy(false);
    void (async()=>{try {const latest=await get<KnowledgeJob|null>('/knowledge/questions/recent');if(latest)accept(latest,seq);}
      catch(e){if(seq===generation.current)setError(getErrorMessage(e));}})();
    return stop;
    // Refresh revalidates source authorization; sequence guards protect the view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[refreshKey]);
  const submit=async()=>{
    stop();const seq=generation.current;setBusy(true);setError('');setJob(null);
    if(request.current?.question!==question.trim())request.current={question:question.trim(),key:crypto.randomUUID()};
    try {accept(await post<KnowledgeJob>('/knowledge/questions',{
      question:request.current.question,idempotency_key:request.current.key}),seq);}
    catch(e){if(seq===generation.current){setBusy(false);setError(getErrorMessage(e));}}
  };
  const cancel=async()=>{if(!job)return;stop();const seq=generation.current;setBusy(true);
    try {accept(await post<KnowledgeJob>(`/knowledge/questions/${job.job_id}/cancel`),seq);}
    catch(e){if(seq===generation.current){setBusy(false);setError(getErrorMessage(e));}}};
  const refresh=()=>{if(job){stop();setBusy(true);void poll(job.job_id,generation.current);}};
  const source=async(c:Citation)=>{try {await downloadFile(`/knowledge/versions/${c.revision_id}/source`,c.filename);}
    catch(e){setError(getErrorMessage(e));refresh();}};
  return <Card title="制度问答"><Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text type="secondary">仅检索当前已发布、未到期且有权访问的制度；人才事实请使用档案问答。</Typography.Text>
    <Input.TextArea aria-label="制度问题" value={question} onChange={e=>setQuestion(e.target.value)} maxLength={1000}/>
    <Space><Button type="primary" disabled={busy||!question.trim()} loading={busy} onClick={()=>void submit()}>查询制度</Button>
      {job&&['pending','running'].includes(job.status)&&<Button onClick={()=>void cancel()}>取消问答</Button>}
      {job&&<Button disabled={busy} onClick={refresh}>刷新问答</Button>}</Space>
    {busy&&<Typography.Text>制度问答处理中，可刷新页面后继续查看。</Typography.Text>}
    {error&&<Alert type="warning" message={error}/>}
    {job?.status==='cancelled'&&<Alert type="info" message="问答已取消"/>}
    {job?.status==='source_unavailable'&&<Alert type="warning" message="引用文档的授权、版本或有效状态已变化，旧回答已隐藏，请重新提问。"/>}
    {job?.result&&<><Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{job.result.answer}</Typography.Paragraph>
      <List dataSource={job.result.citations} renderItem={c=><List.Item><Space direction="vertical">
        <Typography.Text>[{c.citation_id}] {c.document_title} · 版本 {c.version} · {c.page_no===null?'文档文本':`第 ${c.page_no} 页`}</Typography.Text>
        <Typography.Paragraph style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{c.snippet}</Typography.Paragraph>
        <Button onClick={()=>void source(c)}>下载引用原件</Button>
      </Space></List.Item>}/></>}
  </Space></Card>;
}
