'use client';
import {Alert, Button, Card, Empty, List, Space, Spin, Tag, Typography, message} from 'antd';
import {useEffect, useState} from 'react';
import {get, downloadFile, getErrorMessage} from '@/lib/http';

interface Evidence {
  field_path:string;
  file_id:number;
  filename?:string;
  page_no:number|null;
  locator_type?:string;
  snippet:string;
  source:string;
  confidence?:number|null;
  review_status:string;
}
interface EvidenceItem {version?:number; evidence:Evidence[]}
const LABELS:Record<string,string> = {'basic.name':'姓名','basic.phone':'手机','basic.email':'邮箱',
  'basic.city':'城市','profile.degree':'学历','profile.current_title':'当前职位',
  'profile.expected_title':'期望职位',skills:'技能',educations:'教育经历',employments:'工作经历',
  projects:'项目经历',certificates:'证书'};

export default function SourceEvidence({personId, refreshKey}:{personId:number; refreshKey?:string|number}) {
  const [data,setData]=useState<EvidenceItem|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [downloading,setDownloading]=useState<number|null>(null);
  useEffect(() => {
    let active=true;
    setLoading(true); setError(''); setData(null);
    void get<{items?:EvidenceItem[]}>(`/talents/${personId}/evidence`)
      .then(value => {if(active) setData(value.items?.[0] || {evidence:[]});})
      .catch(cause => {if(active) setError(getErrorMessage(cause));})
      .finally(() => {if(active) setLoading(false);});
    return () => {active=false;};
  },[personId,refreshKey]);
  const download=async (entry:Evidence) => {
    setDownloading(entry.file_id);
    try {await downloadFile(`/talents/${personId}/resume-file?resume_file_id=${entry.file_id}`,entry.filename || 'resume');}
    catch(cause) {message.error(getErrorMessage(cause));}
    finally {setDownloading(null);}
  };
  return <Card title="原文依据" extra={data?.version ? `档案版本 ${data.version}` : undefined}>
    <Typography.Paragraph type="secondary">下方引用来自资料原文，与 AI 生成的摘要和能力画像区分。自动匹配的候选依据需人工核实，未展示依据不代表没有相应能力。</Typography.Paragraph>
    {loading ? <Spin><div style={{minHeight:60}} /></Spin> : error ? <Alert type="warning" showIcon message={error} /> :
      !data?.evidence?.length ? <Empty description="暂无可核对的原文证据" /> :
      <List dataSource={data.evidence} renderItem={(entry,index) => <List.Item key={`${entry.file_id}-${entry.field_path}-${index}`}>
        <Space direction="vertical" style={{width:'100%'}}>
          <Space wrap><Typography.Text strong>{LABELS[entry.field_path] || entry.field_path}</Typography.Text>
            <Tag>{entry.source === 'ocr' ? 'OCR 识别' : '文本提取'}</Tag>
            <Tag color={entry.review_status === 'confirmed' ? 'success':'warning'}>{entry.review_status === 'confirmed' ? '已确认':'待核实'}</Tag>
          </Space>
          <Space wrap><span>{entry.filename || `原件 #${entry.file_id}`}</span>
            <span>{entry.locator_type === 'document_text' || entry.page_no == null ? '文档文本（无固定页码）':`第 ${entry.page_no} 页`}</span>
            {entry.confidence != null && <span>识别置信度 {Math.round(entry.confidence*100)}%</span>}
          </Space>
          <Typography.Paragraph style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',marginBottom:0}}>{entry.snippet}</Typography.Paragraph>
          <Button size="small" loading={downloading === entry.file_id} onClick={() => void download(entry)}>下载依据原件</Button>
        </Space>
      </List.Item>} />}
  </Card>;
}
