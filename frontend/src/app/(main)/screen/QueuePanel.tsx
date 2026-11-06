/**
 * 解析队列面板（T-15，需求 F1-2 / 接口 §4）。
 *
 * 口径：
 * 1. **2s 轮询**走 `usePolling`；队列是列表不是批次，永不"终态"，`shouldStop` 恒 false。
 * 2. `summary` 只按 `upload_batch_id` 收窄，**不受 status 影响**（状态 Tab 角标要全量数）；
 *    `status` 只筛列表，因此 Tab 上的计数与表格条数本来就可能不一致。
 * 3. 按钮显隐直接读后端 `can_retry` / `can_requeue`，前端不重算规则。
 *    "移除"没有对应字段，按接口 §4 的可删状态判断（running 400、已 cancelled 404）。
 * 4. 并发设置写 `sys_settings.task_concurrency`（1–5），保存后**下一个扫描周期生效**；
 *    轮询会回填最新值，但只在首次/保存成功时更新草稿，避免覆盖用户正在编辑的值。
 */

'use client';

import { ReloadOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getErrorMessage } from '@/lib/http';
import { usePolling } from '@/lib/usePolling';

import {
  cancelTask,
  fetchBatches,
  fetchQueue,
  requeueTask,
  retryTask,
  updateConcurrency,
} from './api';
import type { BatchSummaryItem, QueueItem, QueueListData, QueueStatus, QueueSummary } from './types';
import {
  STATUS_TABS,
  canDeleteStatus,
  contractStatusMeta,
  formatBatchId,
  formatTime,
  stageLabel,
  summaryCount,
} from './utils';

const PAGE_SIZE = 10;

const SUMMARY_CARDS: { key: keyof QueueSummary; label: string; color?: string }[] = [
  { key: 'total', label: '队列总数' },
  { key: 'pending', label: '待处理' },
  { key: 'running', label: '解析中' },
  { key: 'success', label: '成功', color: '#389e0d' },
  { key: 'failed', label: '失败', color: '#cf1322' },
  { key: 'duplicate_pending', label: '查重待确认', color: '#d48806' },
  { key: 'cancelled', label: '已取消' },
];

export interface QueuePanelProps {
  /** 父组件在上传/重新入队后自增，用于立即刷新队列与批次下拉 */
  reloadSignal: number;
}

export default function QueuePanel({ reloadSignal }: QueuePanelProps) {
  const [status, setStatus] = useState<QueueStatus | 'all'>('all');
  const [batchId, setBatchId] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [batches, setBatches] = useState<BatchSummaryItem[]>([]);
  const [concurrency, setConcurrency] = useState<number | null>(null);
  const [savingConcurrency, setSavingConcurrency] = useState(false);

  const fetcher = useCallback(
    () =>
      fetchQueue({
        status: status === 'all' ? undefined : status,
        upload_batch_id: batchId,
        page,
        page_size: PAGE_SIZE,
      }),
    [status, batchId, page],
  );

  const { data, error, loading, refresh } = usePolling<QueueListData>(fetcher, {
    // 队列没有终态：失败/取消的任务仍要展示，持续轮询才能看到状态流转
    shouldStop: () => false,
  });

  // 首次拿到数据时初始化并发草稿；之后只在保存成功后更新，避免轮询覆盖编辑中的值
  useEffect(() => {
    if (data && concurrency === null) setConcurrency(data.concurrency);
  }, [data, concurrency]);

  const loadBatches = useCallback(async () => {
    try {
      const response = await fetchBatches();
      setBatches(response.items);
    } catch {
      // 统一错误提示由 lib/http 拦截器负责
    }
  }, []);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  // 筛选条件变化时立即拉一次；轮询本来也会用新条件，这里只是消除最多 2s 的等待
  const firstFilterRender = useRef(true);
  useEffect(() => {
    if (firstFilterRender.current) {
      firstFilterRender.current = false;
      return;
    }
    refresh();
  }, [status, batchId, page, refresh]);

  // 上传/重新入队完成 → 刷新列表与批次下拉
  const firstSignalRender = useRef(true);
  useEffect(() => {
    if (firstSignalRender.current) {
      firstSignalRender.current = false;
      return;
    }
    void loadBatches();
    refresh();
  }, [reloadSignal, loadBatches, refresh]);

  const runAction = async (action: () => Promise<unknown>, successText: string) => {
    try {
      await action();
      message.success(successText);
      refresh();
    } catch {
      // 统一错误提示由 lib/http 拦截器负责
    }
  };

  const saveConcurrency = async () => {
    if (concurrency === null) return;
    setSavingConcurrency(true);
    try {
      const response = await updateConcurrency(concurrency);
      setConcurrency(response.concurrency);
      message.success('并发数已保存，下一个扫描周期生效');
      refresh();
    } catch {
      // 同上
    } finally {
      setSavingConcurrency(false);
    }
  };

  const summary = data?.summary;

  const columns: ColumnsType<QueueItem> = [
    {
      title: '文件名',
      dataIndex: 'filename',
      ellipsis: true,
      render: (value: string, row) => (
        <Space size={4} wrap>
          <span>{value}</span>
          {row.truncated && (
            <Tooltip title="纯文本超过上限，已按截断内容解析">
              <Tag color="orange">截断</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (_, row) => {
        const meta = contractStatusMeta(row.task_status, row.duplicate_pending, row.status);
        const tag = <Tag color={meta.color}>{meta.label}</Tag>;
        return row.duplicate_pending ? (
          <Tooltip title="需在人才画像页完成查重确认，任务才会继续">{tag}</Tooltip>
        ) : (
          tag
        );
      },
    },
    {
      title: '阶段',
      dataIndex: 'stage',
      width: 100,
      render: (value: string | null) => stageLabel(value),
    },
    { title: '重试', dataIndex: 'retries', width: 70, align: 'center' },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (value: string | null) =>
        value ? (
          <Tooltip title={value}>
            <Typography.Text type="danger" ellipsis style={{ maxWidth: 240 }}>
              {value}
            </Typography.Text>
          </Tooltip>
        ) : (
          '—'
        ),
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string) => formatTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 230,
      render: (_, row) => (
        <Space size={0} wrap>
          {row.can_retry && (
            <Button
              type="link"
              size="small"
              onClick={() => runAction(() => retryTask(row.task_id), '已重试，将从断点续跑')}
            >
              重试
            </Button>
          )}
          {row.can_requeue && (
            <Button
              type="link"
              size="small"
              onClick={() => runAction(() => requeueTask(row.task_id), '已重新入队，将从头重跑')}
            >
              重新入队
            </Button>
          )}
          {canDeleteStatus(row.status) && (
            <Popconfirm
              title="从队列移除该简历？"
              description="仅软取消：原文件与已生成画像都会保留，之后可用「重新入队」恢复。"
              okText="移除"
              cancelText="取消"
              onConfirm={() => runAction(() => cancelTask(row.task_id), '已移出队列（软取消）')}
            >
              <Button type="link" size="small" danger>
                移除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="解析队列"
      extra={<Typography.Text type="secondary">每 2 秒自动刷新</Typography.Text>}
    >
      <Row gutter={[12, 12]}>
        {SUMMARY_CARDS.map((item) => (
          <Col key={item.key} xs={12} sm={8} md={6} lg={3}>
            <Card size="small">
              <Statistic
                title={item.label}
                value={summaryCount(summary, item.key)}
                valueStyle={item.color ? { color: item.color } : undefined}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Space wrap style={{ marginTop: 16 }} align="center">
        <Typography.Text strong>解析并发</Typography.Text>
        <InputNumber
          min={1}
          max={5}
          precision={0}
          value={concurrency}
          disabled={concurrency === null || savingConcurrency}
          onChange={(value) => setConcurrency(typeof value === 'number' ? value : null)}
          style={{ width: 80 }}
        />
        <Button
          type="primary"
          loading={savingConcurrency}
          disabled={concurrency === null}
          onClick={saveConcurrency}
        >
          保存
        </Button>
        <Typography.Text type="secondary">
          1–5，TaskRunner 全局并发；保存后下一个扫描周期（约 2 秒）生效
        </Typography.Text>
      </Space>

      <Space wrap style={{ marginTop: 16, marginBottom: 8 }}>
        <Typography.Text>上传批次</Typography.Text>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="全部批次"
          style={{ width: 300 }}
          value={batchId}
          onChange={(value) => {
            setBatchId(value);
            setPage(1);
          }}
          options={batches.map((item) => ({
            value: item.upload_batch_id,
            label: `${formatBatchId(item.upload_batch_id)} · ${item.file_count} 份`,
          }))}
        />
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          刷新
        </Button>
      </Space>

      <Tabs
        size="small"
        activeKey={status}
        onChange={(key) => {
          setStatus(key as QueueStatus | 'all');
          setPage(1);
        }}
        items={STATUS_TABS.map((tabItem) => ({
          key: tabItem.key,
          label: `${tabItem.label}（${summaryCount(summary, tabItem.key)}）`,
        }))}
      />

      {error ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="队列刷新失败，正在自动重试…"
          description={getErrorMessage(error)}
        />
      ) : null}

      <Table<QueueItem>
        rowKey="task_id"
        size="small"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={loading && !data}
        locale={{ emptyText: <Empty description="暂无解析任务，先上传简历吧" /> }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 条`,
          onChange: (nextPage) => setPage(nextPage),
        }}
        scroll={{ x: 900 }}
      />
    </Card>
  );
}
