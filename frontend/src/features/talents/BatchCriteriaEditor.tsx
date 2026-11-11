'use client';
import {Button,Input,Space,Typography} from 'antd';
import type {Criterion} from './useQuestionJob';
import {fieldLabels} from './BatchAnalysisResult';

export default function BatchCriteriaEditor({value,onChange,disabled}:{value:Criterion[];onChange:(v:Criterion[])=>void;disabled:boolean}) {
  const update=(index:number,item:Criterion)=>onChange(value.map((v,i)=>i===index?item:v));
  return <Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text>明确字段条件，多个条件须同时满足；不解析自然语言为统计条件。</Typography.Text>
    {value.map((item,index)=><Space wrap key={index}>
      <label>条件{index+1}字段 <select aria-label={`条件${index+1}字段`} disabled={disabled} value={item.field}
        onChange={e=>update(index,{field:e.target.value,operator:e.target.value==='skills'?'contains':e.target.value==='work_years'?'gte':'eq',value:''})}>
        {Object.entries(fieldLabels).map(([field,label])=><option key={field} value={field}>{label}</option>)}
      </select></label>
      {item.field==='degree'?<label>比较方式 <select aria-label={`条件${index+1}比较方式`} disabled={disabled} value={item.operator}
        onChange={e=>update(index,{...item,operator:e.target.value})}><option value="eq">等于</option><option value="gte">至少</option></select></label>
        :<Typography.Text>{item.operator==='gte'?'至少':item.operator==='contains'?'包含完整技能词':'等于'}</Typography.Text>}
      {item.field==='degree'?<label>条件{index+1}的值 <select aria-label={`条件${index+1}的值`} disabled={disabled} value={String(item.value)}
        onChange={e=>update(index,{...item,value:e.target.value})}><option value="">请选择学历</option>{['高中中专','大专','本科','硕士','博士'].map(v=><option key={v}>{v}</option>)}</select></label>
        :<Input aria-label={`条件${index+1}的值`} disabled={disabled} style={{width:180}} value={item.value} maxLength={100}
          type={item.field==='work_years'?'number':'text'} min={0} max={100}
          onChange={e=>update(index,{...item,value:e.target.value})}/>}
      <Button disabled={disabled} onClick={()=>onChange(value.filter((_,i)=>i!==index))}>删除条件{index+1}</Button>
    </Space>)}
    <Button disabled={disabled||value.length>=5} onClick={()=>onChange([...value,{field:'skills',operator:'contains',value:''}])}>添加条件</Button>
  </Space>;
}
