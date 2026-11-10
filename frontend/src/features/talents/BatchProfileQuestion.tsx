'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Input,List,Space,Typography} from 'antd';
import {post,getErrorMessage} from '@/lib/http';
type Item={person_id:number;status:string;answer?:string;error?:string;profile_version?:number;
  citations?:{citation_id:number;filename?:string;page_no:number|null;snippet:string}[]};
export default function BatchProfileQuestion({people}:{people:{id:number;name:string}[]}) {
  const [question,setQuestion]=useState('');const [busy,setBusy]=useState(false);
  const [data,setData]=useState<{items:Item[];requested:number;answered:number}|null>(null);
  const [error,setError]=useState('');const seq=useRef(0);const key=people.map(p=>p.id).join(',');
  useEffect(()=>{seq.current++;setData(null);setError('');setBusy(false);return()=>{seq.current++;};},[key]);
  const submit=async()=>{const current=++seq.current;setBusy(true);setError('');setData(null);
    try {const value=await post<{items:Item[];requested:number;answered:number}>('/talents/questions/batch',
      {person_ids:people.map(p=>p.id),question});if(seq.current===current)setData(value);}
    catch(e){if(seq.current===current)setError(getErrorMessage(e));}
    finally{if(seq.current===current)setBusy(false);}};
  if(!people.length)return null;
  return <Card title={`批量档案问答（已选 ${people.length} 人）`}><Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text>逐人回答并列出引用；资料不足不会计为成功回答。请勿将检索结果视为全体人员统计。</Typography.Text>
    <Input.TextArea aria-label="批量档案问题" value={question} maxLength={1000} onChange={e=>setQuestion(e.target.value)}/>
    <Button loading={busy} disabled={busy||!question.trim()} onClick={()=>void submit()}>逐人提问</Button>
    {error&&<Alert type="warning" message={error}/>}
    {data&&<><Typography.Text>请求 {data.requested} 人，已回答 {data.answered} 人</Typography.Text>
      <List dataSource={data.items} renderItem={item=><List.Item><Space direction="vertical">
        <Typography.Text strong>{people.find(p=>p.id===item.person_id)?.name} · {item.status==='answered'?'已回答':item.status==='failed'?'处理失败':'资料不足'}{item.profile_version?` · 档案版本 ${item.profile_version}`:''}</Typography.Text>
        <Typography.Paragraph>{item.answer||item.error}</Typography.Paragraph>
        {item.citations?.map(c=><Typography.Paragraph key={c.citation_id}>[{c.citation_id}] {c.filename} · {c.page_no==null?'文档文本':`第 ${c.page_no} 页`}：{c.snippet}</Typography.Paragraph>)}
      </Space></List.Item>}/></>}
  </Space></Card>;
}
