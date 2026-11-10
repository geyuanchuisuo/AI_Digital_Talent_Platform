'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,List,Space,Typography} from 'antd';
import {get,getErrorMessage} from '@/lib/http';

interface HistoryItem {
  question_id:number; question:string; profile_version:number; created_at?:string;
  status:string; answer:string;
  citations:{citation_id:number;file_id:number;filename?:string;page_no:number|null;snippet:string}[];
}
export default function QuestionHistory({personId,onFollowUp}:{personId:number;onFollowUp?:(item:HistoryItem)=>void}) {
  const [items,setItems]=useState<HistoryItem[]>([]);
  const [total,setTotal]=useState(0);
  const [loaded,setLoaded]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const generation=useRef(0);
  useEffect(()=>{
    generation.current++;setItems([]);setTotal(0);setLoaded(false);setBusy(false);setError('');
    return ()=>{generation.current++;};
  },[personId]);
  const load=async(offset:number)=>{
    const current=++generation.current;setBusy(true);setError('');
    try {
      const value=await get<{items:HistoryItem[];total:number}>(`/talents/${personId}/questions?offset=${offset}&limit=10`);
      if(current!==generation.current)return;
      setItems(previous=>offset?[...previous,...value.items]:value.items);setTotal(value.total);setLoaded(true);
    } catch(e){if(current===generation.current){setItems([]);setLoaded(false);setError(getErrorMessage(e));}}
    finally{if(current===generation.current)setBusy(false);}
  };
  return <Space direction="vertical" style={{width:'100%'}}>
    <Button loading={busy} onClick={()=>void load(0)}>查看问答历史</Button>
    {error&&<Alert type="warning" message={error} showIcon/>}
    {loaded&&<><Typography.Text type="secondary">仅显示你提交且当前仍有权查看的历史问答，共 {total} 条。</Typography.Text>
      <List dataSource={items} locale={{emptyText:'暂无问答历史'}} renderItem={item=><List.Item>
        <Card size="small" title={item.question} style={{width:'100%'}}>
          <Typography.Text type="secondary">档案版本 {item.profile_version}{item.created_at?` · ${item.created_at}`:''}</Typography.Text>
          {item.status==='failed'&&<Alert type="warning" message="本次问答失败，未生成有效回答。"/>}
          {item.status==='running'&&<Alert type="info" message="本次请求尚无最终结果，请稍后刷新。"/>}
          {item.status==='source_unavailable'&&<Alert type="warning" message="原文来源当前不可访问，历史回答已隐藏。"/>}
          <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{item.answer}</Typography.Paragraph>
          {onFollowUp&&['answered','insufficient_evidence'].includes(item.status)&&
            <Button onClick={()=>onFollowUp(item)}>追问此记录</Button>}
          {item.citations.map(c=><div key={c.citation_id}>
            <Typography.Text>[{c.citation_id}] {c.filename||`原件 #${c.file_id}`} · {c.page_no==null?'文档文本（无固定页码）':`第 ${c.page_no} 页`}</Typography.Text>
            <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{c.snippet}</Typography.Paragraph>
          </div>)}
        </Card>
      </List.Item>}/>
      {items.length<total&&<Button disabled={busy} onClick={()=>void load(items.length)}>加载更多历史</Button>}
    </>}
  </Space>;
}
