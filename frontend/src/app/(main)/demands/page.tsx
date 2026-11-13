'use client';

/**
 * 人才需求与缺口（2026-11-14，T-A21/T-A24 前端收口）。
 *
 * 数据：GET /api/recruitment/demands/gap-summary（A 侧 T-A24 端点）。
 * 口径（指标口径交付 §7 + 需求 §12.3，页面明示）：
 * - 需求人数 = project_demands.headcount；
 * - 人工推进 = 同岗申请中曾被人工作业（internal_stage ≠ new）的候选人去重数，
 *   含后续录用/淘汰——人工推进 ≠ 已录用，二者分列不混称；
 * - 已录用 = offer_accepted 且 result_confirmed_by 非空（HR 确认口径）的
 *   候选人去重数；未经确认不计入；
 * - 剩余需求（缺口）= 需求人数 − 已录用；负值表示超编，如实显示。
 * 未关联岗位的需求无录用路径，人工推进/已录用恒为 0。
 */

import { CarOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Select, Space, Table, Tag, Typography } from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { get, getErrorMessage } from '@/lib/http';

interface GapRow {
  demand_id: number;
  position_name: string;
  project_id: number | null;
  job_profile_id: number | null;
  demand_status: string;
  headcount: number;
  advanced_count: number;
  hired_count: number;
  gap: number;
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  open: { label: '待启动', color: 'default' },
  in_progress: { label: '推进中', color: 'processing' },
  fulfilled: { label: '已满足', color: 'green' },
  closed: { label: '已关闭', color: 'default' },
};

const STATUS_OPTIONS = ['open', 'in_progress', 'fulfilled', 'closed'].map((v) => ({
  value: v,
  label: STATUS_META[v].label,
}));

export default function DemandsPage() {
  const [rows, setRows] = useState<GapRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | undefined>(undefined);

  const load = useCallback(
    async (statusFilter?: string) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page_size: '50' });
        if (statusFilter) params.set('status', statusFilter);
        const data = await get<{ items: GapRow[]; total: number }>(
          `/recruitment/demands/gap-summary?${params.toString()}`,
        );
        setRows(data.items);
        setTotal(data.total);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(undefined);
  }, [load]);

  return (
    <div>
      <Space align="center" style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          <CarOutlined /> 人才需求与缺口
        </Typography.Title>
        <Space>
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 140 }}
            options={STATUS_OPTIONS}
            value={status}
            onChange={(v) => {
              setStatus(v);
              void load(v);
            }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(status)} loading={loading}>
            刷新
          </Button>
        </Space>
      </Space>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="口径说明（§12.3：人工推进 ≠ 已录用）"
        description="已录用仅统计 offer_accepted 且经 HR 确认的候选人（去重）；人工推进为曾被人工作业推进阶段的候选人（去重，含录用/淘汰）；剩余需求 = 需求人数 − 已录用，负值表示超编。未关联岗位的需求无录用路径。"
      />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Table
        rowKey="demand_id"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 10, hideOnSinglePage: true, total, showSizeChanger: false }}
        locale={{ emptyText: '暂无人才需求' }}
        columns={[
          { title: '需求岗位', dataIndex: 'position_name', width: 220 },
          {
            title: '状态',
            dataIndex: 'demand_status',
            width: 100,
            render: (v: string) => {
              const meta = STATUS_META[v] ?? { label: v, color: 'default' };
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          { title: '需求人数', dataIndex: 'headcount', width: 100, align: 'center' as const },
          {
            title: '人工推进',
            dataIndex: 'advanced_count',
            width: 100,
            align: 'center' as const,
            render: (v: number, row: GapRow) => (row.job_profile_id == null ? '—' : v),
          },
          {
            title: '已录用（HR 确认）',
            dataIndex: 'hired_count',
            width: 150,
            align: 'center' as const,
            render: (v: number, row: GapRow) => (row.job_profile_id == null ? '—' : v),
          },
          {
            title: '剩余需求',
            dataIndex: 'gap',
            width: 110,
            align: 'center' as const,
            render: (v: number, row: GapRow) =>
              row.job_profile_id == null ? (
                <Typography.Text type="secondary">—</Typography.Text>
              ) : v < 0 ? (
                <Tag color="orange">超编 {Math.abs(v)}</Tag>
              ) : (
                <Typography.Text strong={v > 0}>{v}</Typography.Text>
              ),
          },
          { title: '岗位条件 ID', dataIndex: 'job_profile_id', render: (v: number | null) => v ?? '未关联' },
        ]}
      />
    </div>
  );
}
