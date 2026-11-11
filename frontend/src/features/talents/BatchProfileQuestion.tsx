'use client';
import {useEffect,useState} from 'react';
import {Alert,Button,Card,Input,List,Radio,Space,Typography} from 'antd';
import {useQuestionJob,type JobMode,type Criterion} from './useQuestionJob';
import BatchAnalysisResult,{conditionText} from './BatchAnalysisResult';
import BatchCriteriaEditor from './BatchCriteriaEditor';
export default function BatchProfileQuestion({people}:{people:{id:number;name:string}[]}) {
  const [question,setQuestion]=useState('');
  const [mode,setMode]=useState<JobMode>('individual');
  const personKey=people.map(p=>p.id).join(',');
  useEffect(()=>{
    try {const saved=sessionStorage.getItem(`b08-mode:${personKey}`);
      setMode(saved==='compare'||saved==='statistics'?saved:'individual');
    } catch {setMode('individual');}
  },[personKey]);
  const changeMode=(value:JobMode)=>{
    setMode(value);
    try {sessionStorage.setItem(`b08-mode:${personKey}`,value);} catch {/* Preference storage is optional. */}
  };
  const [criteria,setCriteria]=useState<Criterion[]>([{field:'skills',operator:'contains',value:''}]);
  const task=useQuestionJob(people.map(p=>p.id),mode);const data=task.job;
  const invalidCriteria=criteria.some(c=>String(c.value).trim()==='');
  if(!people.length)return null;
  return <Card title={`批量档案问答（已选 ${people.length} 人）`}><Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text>逐人回答并列出引用；资料不足不会计为成功回答。请勿将检索结果视为全体人员统计。</Typography.Text>
    <Radio.Group aria-label="批量处理方式" value={mode} onChange={e=>changeMode(e.target.value)} options={[
      {label:'逐人问答',value:'individual'},{label:'字段与证据比较',value:'compare'},{label:'档案字段统计',value:'statistics'}]}/>
    {mode!=='statistics'&&<Input.TextArea aria-label="批量档案问题" value={question} maxLength={1000} onChange={e=>setQuestion(e.target.value)}/>}
    {mode!=='individual'&&<BatchCriteriaEditor value={criteria} onChange={setCriteria} disabled={task.busy}/>}
    {mode==='compare'&&<Typography.Text type="secondary">比较可删除全部字段条件，直接查看字段表与逐人证据。</Typography.Text>}
    <Button loading={task.busy} disabled={task.busy||(mode!=='statistics'&&!question.trim())||(mode==='statistics'&&(!criteria.length||invalidCriteria))||(mode==='compare'&&(people.length<2||invalidCriteria))}
      onClick={()=>void task.submit({question:mode==='statistics'?'所选档案字段统计':question,
        ...(mode==='individual'?{}:{criteria})})}>
      {mode==='statistics'?'统计所选档案':mode==='compare'?'比较所选档案':'逐人提问'}
    </Button>
    {data&&<Typography.Text>任务 #{data.job_id} · 已处理 {data.processed}/{data.requested}{data.status==='cancelled'?' · 已取消':''}</Typography.Text>}
    {!!data?.criteria?.length&&<Typography.Text>任务条件（同时满足）：{data.criteria.map(conditionText).join('；')}</Typography.Text>}
    {data&&['pending','running'].includes(data.status)&&<Space><Button onClick={()=>void task.cancel()}>取消问答</Button>
      {!task.busy&&<Button onClick={task.resume}>重新连接</Button>}</Space>}
    {task.error&&<Alert type="warning" message={task.error}/>}
    {data?.analysis&&<BatchAnalysisResult analysis={data.analysis} people={people} criteria={data.criteria??data.analysis.criteria??[]}/>}
    {data&&mode!=='statistics'&&<><Typography.Text>请求 {data.requested} 人，已回答 {data.answered} 人</Typography.Text>
      <List dataSource={data.items} renderItem={item=><List.Item><Space direction="vertical">
        <Typography.Text strong>{people.find(p=>p.id===item.person_id)?.name} · {item.status==='answered'?'已回答':item.status==='failed'?'处理失败':'资料不足'} · ID {item.person_id}{item.profile_version?` · 档案版本 ${item.profile_version}`:''}</Typography.Text>
        <Typography.Paragraph>{item.answer||item.error}</Typography.Paragraph>
        {item.citations?.map(c=><Typography.Paragraph key={c.citation_id}>[{c.citation_id}] {c.filename} · {c.page_no==null?'文档文本':`第 ${c.page_no} 页`}：{c.snippet}</Typography.Paragraph>)}
      </Space></List.Item>}/></>}
  </Space></Card>;
}
