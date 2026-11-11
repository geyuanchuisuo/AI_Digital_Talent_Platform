'use client';
import {Alert,List,Space,Typography} from 'antd';
import type {BatchAnalysis,Criterion} from './useQuestionJob';

export const fieldLabels:Record<string,string>={skills:'技能',degree:'学历',work_years:'工作年限',city:'城市',current_title:'当前职位'};
const opLabels:Record<string,string>={contains:'包含完整技能词',gte:'至少',eq:'等于'};
export const conditionText=(criterion:Criterion)=>`${fieldLabels[criterion.field]??criterion.field} ${opLabels[criterion.operator]??criterion.operator} ${criterion.value}`;

export default function BatchAnalysisResult({analysis,people,criteria}:{analysis:BatchAnalysis;people:{id:number;name:string}[];criteria:Criterion[]}) {
  const name=(id:number)=>`${people.find(p=>p.id===id)?.name??'人员'} · ID ${id}`;
  return <Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text>本次所选范围 {analysis.population} 人，以人员 ID 去重；仅核对档案字段记录。</Typography.Text>
    {criteria.length>0&&<>
      <Typography.Text>本次提交条件（同时满足）：{criteria.map(conditionText).join('；')}</Typography.Text>
      <Typography.Text>{analysis.exact?'字段统计完整':'已知匹配'} {analysis.matched_count} 人，字段未命中 {analysis.not_matched_count} 人，待核实 {analysis.unknown_count} 人</Typography.Text>
      {[['字段匹配名单',analysis.matched_ids],['字段未命中名单',analysis.not_matched_ids],['待核实名单',analysis.unknown_ids]].map(([label,ids])=><div key={label as string}>
        <Typography.Text strong>{label as string}</Typography.Text>
        <List size="small" dataSource={ids as number[]} locale={{emptyText:'无'}} renderItem={id=><List.Item>{name(id)}</List.Item>}/>
      </div>)}
    </>}
    {(analysis.coverage_incomplete||(criteria.length>0&&!analysis.exact))&&<Alert type="warning" showIcon message="覆盖不完整，不能将已知匹配人数当作最终总人数；资料缺失不代表本人不具备能力。"/>}
    <Typography.Text type="secondary">表格依据为固定版本的档案字段；原文引用见逐人回答，字段未命中不等于能力不足。</Typography.Text>
    {analysis.computed_at&&<Typography.Text type="secondary">核对时间：{analysis.computed_at}</Typography.Text>}
    {analysis.rows.length>0&&<div style={{overflowX:'auto',maxWidth:'100%'}} tabIndex={0} aria-label="所选档案比较表">
      <table style={{borderCollapse:'collapse',minWidth:Math.max(520,analysis.rows.length*210),width:'100%'}}>
        <caption>固定档案版本字段比较</caption>
        <thead><tr><th scope="col" style={{padding:8,textAlign:'left'}}>字段依据</th>{analysis.rows.map(row=><th key={row.person_id} scope="col" style={{padding:8,textAlign:'left'}}>
          {name(row.person_id)} · 版本 {row.profile_version}
        </th>)}</tr></thead>
        <tbody>{Object.entries(fieldLabels).map(([field,label])=><tr key={field}>
          <th scope="row" style={{padding:8,textAlign:'left',borderTop:'1px solid #ddd'}}>{label}</th>
          {analysis.rows.map(row=>{const value=row.fields[field]?.value;return <td key={row.person_id} style={{padding:8,verticalAlign:'top',borderTop:'1px solid #ddd'}}>
            {Array.isArray(value)?value.join('、')||'资料未记录':value==null||value===''?'资料未记录':String(value)}
          </td>;})}
        </tr>)}</tbody>
      </table>
    </div>}
  </Space>;
}
