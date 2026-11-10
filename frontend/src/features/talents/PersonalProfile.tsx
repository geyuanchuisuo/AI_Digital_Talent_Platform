'use client';
import {Alert, Button, Card, Descriptions, Empty, List, Space, Spin, Tag, Typography, message} from 'antd';
import {useEffect, useState} from 'react';
import {getUser} from '@/lib/auth';
import {get, downloadFile, getErrorMessage} from '@/lib/http';
import type {TalentDetailData} from '@/app/(main)/talents/types';
import SourceEvidence from './SourceEvidence';
import ProfileQuestion from './ProfileQuestion';

export default function PersonalProfile() {
  const [detail, setDetail] = useState<TalentDetailData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [personId, setPersonId] = useState<number | null>(null);
  const [downloading, setDownloading] = useState<number | null>(null);
  useEffect(() => {
    let active = true;
    const user = getUser();
    if (!user?.person_id || user.role !== 'personal') {
      setError('当前账号尚未绑定人才档案，请联系 HR。'); setLoading(false);
      return;
    }
    setPersonId(user.person_id);
    void get<TalentDetailData>(`/talents/${user.person_id}`)
      .then(data => {if (active) setDetail(data);})
      .catch(e => {if (active) setError(getErrorMessage(e));})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, []);
  if (loading) return <Spin tip="正在加载本人档案"><div style={{minHeight:120}} /></Spin>;
  if (error) return <Alert type="warning" showIcon message={error} />;
  if (!detail) return <Empty description="暂无可查看的档案" />;
  const p = detail.profile;
  const download = async (id: number, name: string) => {
    setDownloading(id);
    try {await downloadFile(`/talents/${personId}/resume-file?resume_file_id=${id}`, name);}
    catch (e) {message.error(getErrorMessage(e));}
    finally {setDownloading(null);}
  };
  return <Space direction="vertical" size="large" style={{width:'100%'}}>
    <Card title="我的档案">
      <Typography.Title level={4}>{p.name}</Typography.Title>
      <Descriptions column={{xs:1, sm:2}}>
        <Descriptions.Item label="城市">{p.city || '未提供'}</Descriptions.Item>
        <Descriptions.Item label="学历">{p.degree || '未提供'}</Descriptions.Item>
        <Descriptions.Item label="当前职位">{p.current_title || '未提供'}</Descriptions.Item>
        <Descriptions.Item label="手机">{p.phone || '未提供'}</Descriptions.Item>
        <Descriptions.Item label="邮箱">{p.email || '未提供'}</Descriptions.Item>
      </Descriptions>
      <Space wrap>{(p.skills || []).map(skill => <Tag key={skill}>{skill}</Tag>)}</Space>
      <Typography.Paragraph type="secondary" style={{marginTop:16}}>档案如需更正，请联系 HR。AI 画像不代表正式测评成绩。</Typography.Paragraph>
    </Card>
    {(['educations','employments','projects','certificates'] as const).map((key, i) =>
      <Card key={key} title={['教育经历','工作经历','项目经历','证书'][i]}>
        <List dataSource={detail[key] || []} locale={{emptyText:'资料中未提供'}} renderItem={entry =>
          <List.Item><div style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{Object.values(entry).filter(v => v != null && v !== '').join(' · ')}</div></List.Item>} />
      </Card>)}
    {personId != null && <><SourceEvidence personId={personId} /><ProfileQuestion personId={personId} /></>}
    <Card title="原始资料">
      <List dataSource={detail.resume_files || []} locale={{emptyText:'暂无原始资料'}} renderItem={file =>
        <List.Item><Space wrap><span style={{overflowWrap:'anywhere'}}>{file.filename}</span>
          <Button loading={downloading === file.resume_file_id} onClick={() => void download(file.resume_file_id,file.filename)}>下载原件</Button>
        </Space></List.Item>} />
    </Card>
  </Space>;
}
