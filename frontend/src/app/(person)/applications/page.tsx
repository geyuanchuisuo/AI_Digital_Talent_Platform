'use client';

/**
 * 我的岗位申请（2026-11-07，T-A60f）：个人端第一块真实业务页面。
 *
 * 数据：GET /api/recruitment/applications?talent_profile_id=<本人>（A08 起后端
 * 以 AccountScope 为最终授权点强制收窄，忽略前端伪造的 talent_profile_id）。
 * 仅展示对外阶段（五阶段中文）与经 HR 确认的结果类别——内部阶段、推进原因、
 * 内部评分/面试反馈、申请输入快照均不在此接口响应中（V2.6 F01）。
 * 未绑定档案（person_id 为空）时提示联系 HR，不发请求。
 */

import { Card, Table, Tag, Typography, Alert, Spin } from 'antd';
import { useEffect, useState } from 'react';

import { getUser, type AuthUser } from '@/lib/auth';
import { get } from '@/lib/http';

interface ApplicationRow {
  id: number;
  job_profile_id: number;
  external_stage: 'submitted' | 'screening' | 'interviewing' | 'deciding' | 'closed';
  result_category: string | null;
  result_published: boolean;
  stage_updated_at: string | null;
}

const EXTERNAL_LABELS: Record<string, { label: string; color: string }> = {
  submitted: { label: '已提交', color: 'default' },
  screening: { label: '筛选中', color: 'processing' },
  interviewing: { label: '待面试', color: 'blue' },
  deciding: { label: '待决定', color: 'gold' },
  closed: { label: '已结束', color: 'default' },
};

export default function MyApplicationsPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [rows, setRows] = useState<ApplicationRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUser(getUser());
  }, []);

  useEffect(() => {
    if (!user?.person_id) return;
    setLoading(true);
    // lib/http.ts 约定：路径不含 /api 前缀（框架自动拼接 API_PREFIX）
    get<{ items: ApplicationRow[]; total: number }>(
      `/recruitment/applications?talent_profile_id=${user.person_id}&page_size=50`,
    )
      .then((data) => setRows(data.items))
      .catch(() => setError('申请记录加载失败，请稍后重试'))
      .finally(() => setLoading(false));
  }, [user?.person_id]);

  if (user && user.role === 'personal' && !user.person_id) {
    return (
      <Alert
        type="warning"
        showIcon
        message="当前账号尚未绑定人才档案"
        description="绑定后即可查看本人岗位申请与对外进度；请联系 HR 完成绑定。"
      />
    );
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        我的岗位申请
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        仅展示本人各申请的对外进度；内部评分与面试反馈不对外显示。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        locale={{ emptyText: loading ? <Spin /> : '暂无申请记录' }}
        columns={[
          {
            title: '申请编号',
            dataIndex: 'id',
            width: 100,
          },
          {
            title: '岗位编号',
            dataIndex: 'job_profile_id',
            width: 100,
          },
          {
            title: '当前进度',
            dataIndex: 'external_stage',
            width: 120,
            render: (stage: string) => {
              const meta = EXTERNAL_LABELS[stage] ?? { label: stage, color: 'default' };
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: '结果（经 HR 确认后公布）',
            dataIndex: 'result_published',
            render: (published: boolean, row: ApplicationRow) =>
              row.external_stage === 'closed'
                ? published
                  ? row.result_category === 'hired'
                    ? <Tag color="green">录用</Tag>
                    : <Tag>未录用</Tag>
                  : <Tag>待确认</Tag>
                : <Typography.Text type="secondary">—</Typography.Text>,
          },
          {
            title: '最近更新',
            dataIndex: 'stage_updated_at',
            render: (v: string | null) => v ?? '—',
          },
        ]}
      />
    </div>
  );
}
