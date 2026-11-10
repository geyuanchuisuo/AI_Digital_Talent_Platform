'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,List,Select,Space,Typography} from 'antd';
import {get,getErrorMessage} from '@/lib/http';

interface Definition {code:string;name:string;type:string}
interface Candidate extends Definition {file_id:number;filename?:string;page_no:number|null;snippet:string}
export default function TagSuggestions({personId,onChoose}:{personId:number;onChoose:(name:string,type:string)=>void}) {
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [definitions,setDefinitions]=useState<Definition[]>([]);
  const [version,setVersion]=useState<number|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const generation=useRef(0);
  useEffect(()=>{generation.current++;setCandidates([]);setDefinitions([]);setVersion(null);setBusy(false);setError('');
    return ()=>{generation.current++;};},[personId]);
  const load=async(catalog:boolean)=>{
    const current=++generation.current;setBusy(true);setError('');
    try {
      if(catalog){
        const value=await get<{items:Definition[]}>('/talents/tag-catalog');
        if(current===generation.current)setDefinitions(value.items);
      } else {
        const value=await get<{items:Candidate[];profile_version:number}>(`/talents/${personId}/tag-suggestions`);
        if(current===generation.current){setCandidates(value.items);setVersion(value.profile_version);}
      }
    }catch(e){if(current===generation.current)setError(getErrorMessage(e));}
    finally{if(current===generation.current)setBusy(false);}
  };
  return <Space direction="vertical" style={{width:'100%'}}>
    <Space wrap>
      <Button disabled={busy} onClick={()=>void load(false)}>查看原文标签建议</Button>
      <Button disabled={busy} onClick={()=>void load(true)}>选择标准标签</Button>
      {busy&&<Typography.Text role="status">加载中…</Typography.Text>}
    </Space>
    {error&&<Alert type="warning" message={error} showIcon/>}
    {definitions.length>0&&<>
      <Typography.Text type="secondary">目录仅用于统一分类，不代表该人员已经具备相关能力。</Typography.Text>
      <Select aria-label="标准标签目录" showSearch value={null} placeholder="搜索标准标签"
        style={{width:'100%'}} options={definitions.map(d=>({value:d.code,label:d.name}))}
        optionFilterProp="label" onChange={code=>{const d=definitions.find(d=>d.code===code);if(d)onChoose(d.name,d.type);}}/>
    </>}
    {version!==null&&<><Alert type="info" message={`档案版本 ${version} 原文提及的候选词，仅作待核实建议；请核对语境后选用。`}/>
      <List dataSource={candidates} locale={{emptyText:'原文未命中标准词，不补造标签。'}} renderItem={row=><List.Item
        actions={[<Button key="choose" size="small" onClick={()=>onChoose(row.name,row.type)}>选用 {row.name}</Button>]}>
        <Space direction="vertical"><Typography.Text strong>{row.name}</Typography.Text>
          <Typography.Text type="secondary">{row.filename||`原件 #${row.file_id}`} · {row.page_no==null?'文档文本':`第 ${row.page_no} 页`}</Typography.Text>
          <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{row.snippet}</Typography.Paragraph>
        </Space>
      </List.Item>}/></>}
  </Space>;
}
