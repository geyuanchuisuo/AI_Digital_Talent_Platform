'use client';
import {useEffect, useRef, useState} from 'react';
import {Alert, Button, Card, Input, List, Space, Typography} from 'antd';
import {post, getErrorMessage} from '@/lib/http';
import QuestionHistory from './QuestionHistory';
import {useQuestionJob} from './useQuestionJob';

interface Answer {
  status:string; answer?:string; profile_version:number;
  question_id?:number;
  source_coverage?:string;
  citations:{citation_id:number; filename?:string; file_id:number; page_no:number|null; snippet:string}[];
}
export default function ProfileQuestion({personId,allowIndex=false}:{personId:number;allowIndex?:boolean}) {
  const [question,setQuestion]=useState('');
  const [answer,setAnswer]=useState<Answer|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const [parent,setParent]=useState<{id:number;version:number;question:string}|null>(null);
  const [answeredQuestion,setAnsweredQuestion]=useState('');
  const generation=useRef(0);
  const task=useQuestionJob([personId]);
  const working=busy||task.busy;
  useEffect(()=>{if(!task.job)setAnswer(null);else if(task.job.status==='completed'&&task.job.items[0]){
    setAnswer(task.job.items[0]);setAnsweredQuestion(task.job.question);
  }},[task.job]);
  useEffect(()=>{generation.current++;setAnswer(null);setQuestion('');setError('');setNotice('');setBusy(false);setParent(null);setAnsweredQuestion('');
    return ()=>{generation.current++;};},[personId]);
  const submit=async()=>{
    const current=++generation.current;
    setBusy(true);setError('');setNotice('');setAnswer(null);
    const submittedQuestion=question;
    try {await task.submit({
      question,...(parent?{follow_up_to:parent.id,version:parent.version}:{})});
      if(current===generation.current)setAnsweredQuestion(submittedQuestion);
    } catch(e){if(current===generation.current)setError(getErrorMessage(e));}
    finally {if(current===generation.current)setBusy(false);}
  };
  const newQuestion=()=>{
    task.clear();
    generation.current++;setParent(null);setAnswer(null);setQuestion('');setAnsweredQuestion('');setError('');setNotice('');setBusy(false);
  };
  const followUp=(id:number,version:number,previousQuestion:string)=>{
    task.clear();
    generation.current++;setParent({id,version,question:previousQuestion});setAnswer(null);setQuestion('');setError('');setNotice('');setBusy(false);
  };
  const refreshIndex=async()=>{
    task.clear();
    const current=++generation.current;setBusy(true);setAnswer(null);setError('');setNotice('');setParent(null);
    try {const value=await post<{profile_version:number;chunks:number}>(`/talents/${personId}/questions/index`);
      if(current===generation.current)setNotice(value.chunks?`索引就绪：档案版本 ${value.profile_version}，${value.chunks} 个片段。`:'暂无可索引原文。');}
    catch(e){if(current===generation.current)setError(getErrorMessage(e));}
    finally{if(current===generation.current)setBusy(false);}
  };
  return <Card title="档案问答"><Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text type="secondary">仅根据本人的档案原文回答，引用内容仍需核实。</Typography.Text>
    {parent&&<Alert type="info" message={`追问档案版本 ${parent.version} · 上一轮问题：${parent.question}`} description="仅携带直接上一轮对话以理解指代；事实和引用会重新从该版本原文核对。"/>}
    <Input.TextArea aria-label="档案问题" maxLength={1000} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="例如：档案中有哪些 Python 项目经历？" />
    <Button type="primary" loading={working} disabled={!question.trim()||working} onClick={()=>void submit()}>提问</Button>
    {(parent||answer)&&<Button disabled={working} onClick={newQuestion}>新问题</Button>}
    {allowIndex&&<Button disabled={working} onClick={()=>void refreshIndex()}>刷新全文索引</Button>}
    {task.job&&<Typography.Text>任务 #{task.job.job_id} · {task.job.status==='completed'?'已完成':task.job.status==='cancelled'?'已取消':task.job.status==='failed'?'未完成':'处理中'} · 已处理 {task.job.processed}/{task.job.requested}</Typography.Text>}
    {task.job&&['pending','running'].includes(task.job.status)&&<Space><Button onClick={()=>void task.cancel()}>取消问答</Button>
      {!task.busy&&<Button onClick={task.resume}>重新连接</Button>}</Space>}
    {(error||task.error)&&<Alert type="warning" message={error||task.error} showIcon />}
    {notice&&<Alert type="info" message={notice}/>}
    {answer&&<><Typography.Text>档案版本 {answer.profile_version}</Typography.Text>
      {answer.source_coverage==='legacy_evidence'&&<Alert type="info" message="该历史版本仅保留字段证据，全文尚未建立版本快照。"/>}
      <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{answer.answer}</Typography.Paragraph>
      {answer.question_id&&['answered','insufficient_evidence'].includes(answer.status)&&<Button disabled={busy}
        onClick={()=>followUp(answer.question_id!,answer.profile_version,answeredQuestion)}>继续追问</Button>}
      <List dataSource={answer.citations} renderItem={c=><List.Item><Space direction="vertical">
        <Typography.Text>[{c.citation_id}] {c.filename||`原件 #${c.file_id}`} · {c.page_no==null?'文档文本（无固定页码）':`第 ${c.page_no} 页`}</Typography.Text>
        <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{c.snippet}</Typography.Paragraph>
      </Space></List.Item>} /></>}
    <QuestionHistory personId={personId} onFollowUp={item=>followUp(item.question_id,item.profile_version,item.question)}/>
  </Space></Card>;
}
