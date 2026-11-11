'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Input,List,Select,Space,Tag,Typography} from 'antd';
import {get,getErrorMessage} from '@/lib/http';
import type {TagProofDraft} from './tagTypes';

interface Definition {code:string;name:string;type:string}
interface Evidence {file_id:number;filename?:string;page_no:number|null;snippet:string;quality_status?:string}
interface Candidate extends Definition,Evidence {candidate_id?:string;context_status?:string;evidence?:Evidence[];evidence_truncated?:boolean}
interface QualityItem {tag:string;evidence_status:string;source_profile_version?:number;evidence:Evidence[]}
const contextLabels:Record<string,string>={mentioned:'原文提及（待核实）',negated:'否定／不足语境',aspirational:'学习／意向语境',mixed:'多种语境并存'};
const qualityLabels:Record<string,string>={reviewed_source:'已人工核对原文',needs_reconfirmation:'来源需重新核对',manual_unverified:'人工标签未附确认原文',ai_unverified:'AI标签未附确认原文'};
export default function TagSuggestions({personId,onChoose}:{personId:number;onChoose:(name:string,type:string,proof?:TagProofDraft)=>void}) {
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [definitions,setDefinitions]=useState<Definition[]>([]);
  const [version,setVersion]=useState<number|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [catalogVersion,setCatalogVersion]=useState('');
  const [reasons,setReasons]=useState<Record<string,string>>({});
  const [sourcePartial,setSourcePartial]=useState(false);
  const [sourceTruncated,setSourceTruncated]=useState(false);
  const [quality,setQuality]=useState<{profile_version:number;items:QualityItem[]}|null>(null);
  const [coverage,setCoverage]=useState<{vocabulary_size:number;by_type:Record<string,number>}|null>(null);
  const generation=useRef(0);
  useEffect(()=>{generation.current++;setCandidates([]);setDefinitions([]);setVersion(null);setBusy(false);setError('');
    setCatalogVersion('');setReasons({});setQuality(null);setCoverage(null);setSourcePartial(false);setSourceTruncated(false);
    return ()=>{generation.current++;};},[personId]);
  const load=async(kind:'catalog'|'suggestions'|'quality')=>{
    const current=++generation.current;setBusy(true);setError('');
    try {
      if(kind==='catalog'){
        const value=await get<{items:Definition[];coverage?:{vocabulary_size:number;by_type:Record<string,number>}}>('/talents/tag-catalog');
        if(current===generation.current){setDefinitions(value.items);setCoverage(value.coverage??null);}
      } else if(kind==='quality') {
        const value=await get<{profile_version:number;items:QualityItem[]}>(`/talents/${personId}/tag-quality`);
        if(current===generation.current)setQuality(value);
      } else {
        const value=await get<{items:Candidate[];profile_version:number;catalog_version:string;source_partial?:boolean;source_truncated?:boolean}>(`/talents/${personId}/tag-suggestions`);
        if(current===generation.current){setCandidates(value.items);setVersion(value.profile_version);setCatalogVersion(value.catalog_version);setReasons({});setSourcePartial(!!value.source_partial);setSourceTruncated(!!value.source_truncated);}
      }
    }catch(e){if(current===generation.current){
      setError(getErrorMessage(e));
      if([401,403,409].includes((e as {code?:number}).code??0)){
        setCandidates([]);setQuality(null);setVersion(null);setReasons({});setSourcePartial(false);setSourceTruncated(false);
      }
    }}
    finally{if(current===generation.current)setBusy(false);}
  };
  const location=(row:Evidence)=>`${row.filename||`原件 #${row.file_id}`} · ${row.page_no==null?'文档文本':`第 ${row.page_no} 页`}`;
  const risky=(row:Candidate)=>!!row.candidate_id&&(row.context_status!=='mentioned'||row.evidence_truncated||sourceTruncated||row.evidence?.some(e=>e.quality_status!=='ready'));
  const choose=(row:Candidate)=>{
    if(row.candidate_id&&version!==null){onChoose(row.name,row.type,{code:row.code,candidate_id:row.candidate_id,
      catalog_version:catalogVersion,profile_version:version,reason:(reasons[row.code]??'').trim()});}
    else onChoose(row.name,row.type);
  };
  return <Space direction="vertical" style={{width:'100%'}}>
    <Space wrap>
      <Button disabled={busy} onClick={()=>void load('suggestions')}>查看原文标签建议</Button>
      <Button disabled={busy} onClick={()=>void load('catalog')}>选择标准标签</Button>
      <Button disabled={busy} onClick={()=>void load('quality')}>查看已保存标签依据</Button>
      {busy&&<Typography.Text role="status">加载中…</Typography.Text>}
    </Space>
    {error&&<Alert type="warning" message={error} showIcon/>}
    {definitions.length>0&&<>
      <Typography.Text type="secondary">目录仅用于统一分类，不代表该人员已经具备相关能力。</Typography.Text>
      {coverage&&<Typography.Text type="secondary">词表 {coverage.vocabulary_size} 项，行业词 {coverage.by_type.industry??0} 项；行业业务评审尚未完成，不按个人凑标签。</Typography.Text>}
      <Select aria-label="标准标签目录" showSearch value={null} placeholder="搜索标准标签"
        style={{width:'100%'}} options={definitions.map(d=>({value:d.code,label:d.name}))}
        optionFilterProp="label" onChange={code=>{const d=definitions.find(d=>d.code===code);if(d)onChoose(d.name,d.type);}}/>
    </>}
    {quality&&<><Alert type="info" message={`已保存档案版本 ${quality.profile_version} 的标签依据；草稿尚未提交。这不是正式测评或能力评级。`}/>
      <List dataSource={quality.items} locale={{emptyText:'当前没有已保存标签。'}} renderItem={row=><List.Item><Space direction="vertical">
        <Typography.Text strong>{row.tag}</Typography.Text><Tag>{qualityLabels[row.evidence_status]??'依据待核实'}</Tag>
        {row.source_profile_version&&<Typography.Text type="secondary">来源档案版本 {row.source_profile_version}</Typography.Text>}
        {row.evidence.map((e,index)=><div key={index}><Typography.Text type="secondary">{location(e)}</Typography.Text><Typography.Paragraph>{e.snippet}</Typography.Paragraph></div>)}
      </Space></List.Item>}/></>}
    {version!==null&&<><Alert type="info" message={`档案版本 ${version} 原文提及的候选词，仅作待核实建议；请核对语境后选用。`}/>
      {sourcePartial&&<Alert type="warning" message="档案来源覆盖不完整，标签候选不是完整能力清单。"/>}
      <List dataSource={candidates} locale={{emptyText:'原文未命中标准词，不补造标签。'}} renderItem={row=><List.Item
        actions={[<Button key="choose" size="small" disabled={risky(row)&&!(reasons[row.code]??'').trim()} onClick={()=>choose(row)}>选用 {row.name}</Button>]}>
        <Space direction="vertical"><Typography.Text strong>{row.name}</Typography.Text>
          <Tag>{contextLabels[row.context_status??'mentioned']??'语境待核实'}</Tag>
          {(row.evidence??[row]).map((e,index)=><div key={index}><Typography.Text type="secondary">{location(e)}</Typography.Text>
            {e.quality_status&&e.quality_status!=='ready'&&<Typography.Text type="warning"> · 原文质量待核实（{e.quality_status}）</Typography.Text>}
            <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{e.snippet}</Typography.Paragraph></div>)}
          {row.evidence_truncated&&<Typography.Text type="warning">原文命中超过展示上限，请核对完整原件。</Typography.Text>}
          {row.candidate_id&&<><label htmlFor={`tag-reason-${row.code}`}>采用{row.name}的原因{risky(row)?'（必填）':'（选填）'}</label>
            <Input.TextArea id={`tag-reason-${row.code}`} aria-label={`采用${row.name}的原因`} maxLength={500} value={reasons[row.code]??''}
              onChange={e=>setReasons({...reasons,[row.code]:e.target.value})}/></>}
        </Space>
      </List.Item>}/></>}
  </Space>;
}
