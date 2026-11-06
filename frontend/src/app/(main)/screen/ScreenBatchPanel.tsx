/**
 * 筛选批次面板（T-27，需求 F1-6/F1-9~F1-12 / 接口 §7）——简历筛选页下半。
 *
 * 口径：
 * 1. **发起筛选**：岗位下拉（启用岗位）+ 四种范围（附录 B）；`resume_file_ids`
 *    用逗号分隔文件 id；202 后刷新批次列表（不轮询详情）。
 * 2. **批次列表 2s 轮询**（usePolling）：pending/running 批次需要看到进度流转；
 *    `progress/total` 差值即软删跳过，列表展示 `retry_count`。
 * 3. **选中批次 → 结果表**：三档 Tab（按**有效结论**过滤）+ 全部；硬淘汰置底
 *    （后端排序）；改判 = 三选一弹窗，撤销传 null；`skipped_deleted>0` 提示。
 * 4. **failed 批次 retry**（D-E 复用原批次，`retry_count` 展示）。
 */

'use client';

import {
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getErrorMessage } from '@/lib/http';
import { usePolling } from '@/lib/usePolling';

import {
  createScreenBatch,
  fetchBatches,
  fetchJobOptions,
  fetchScreenBatchDetail,
  fetchScreenBatches,
  fetchScreenResults,
  retryScreenBatch,
  setScreenManualVerdict,
} from './api';
import type {
  JobOption,
  ScreenBatchDetail,
  ScreenBatchListItem,
  ScreenResultItem,
  Verdict,
} from './types';
import { formatBatchId, formatTime } from './utils';

const { Text } = Typography;

const VERDICT_TABS: { key: Verdict | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'passed', label: '通过' },
  { key: 'pending', label: '待定' },
  { key: 'rejected', label: '淘汰' },
];

const VERDICT_META: Record<Verdict, { label: string; color: string }> = {
  passed: { label: '通过', color: 'success' },
  pending: { label: '待定', color: 'warning' },
  rejected: { label: '淘汰', color: 'error' },
};

export default function ScreenBatchPanel() {
  // —— 发起筛选 ——
  const [jobs, setJobs] = useState<JobOption[]>([]);
  const [jobId, setJobId] = useState<number | undefined>(undefined);
  const [scopeType, setScopeType] = useState<'all' | 'all_unscreened' | 'upload_batch' | 'resume_file_ids'>('all');
  const [uploadBatchId, setUploadBatchId] = useState<string | undefined>(undefined);
  const [fileIds, setFileIds] = useState('');
  const [creating, setCreating] = useState(false);
  const [batchOptions, setBatchOptions] = useState<{ label: string; value: string }[]>([]);

  // —— 批次列表（2s 轮询）——
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ScreenBatchDetail | null>(null);

  // —— 结果 ——
  const [verdict, setVerdict] = useState<Verdict | 'all'>('all');
  const [results, setResults] = useState<ScreenResultItem[]>([]);
  const [resultTotal, setResultTotal] = useState(0);
  const [resultPage, setResultPage] = useState(1);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [judging, setJudging] = useState<ScreenResultItem | null>(null);
  const [retrying, setRetrying] = useState<number | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const options = await fetchJobOptions();
      setJobs(options);
      setJobId((prev) => prev ?? options[0]?.id);
    } catch {
      // 统一错误提示由 lib/http 拦截器负责
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const batchesFetcher = useCallback(() => fetchScreenBatches(undefined, 1, 20), []);
  const { data: batchesData, refresh: refreshBatches } = usePolling(batchesFetcher, {
    shouldStop: () => false,
  });
  const batches = batchesData?.items ?? [];

  const loadBatchOptions = useCallback(async () => {
    try {
      const data = await fetchBatches(1, 100);
      setBatchOptions(data.items.map((b) => ({ label: formatBatchId(b.upload_batch_id), value: b.upload_batch_id })));
    } catch {
      /* 忽略 */
    }
  }, []);

  useEffect(() => {
    void loadBatchOptions();
  }, [loadBatchOptions]);

  const detailFetcher = useCallback(
    () => (selectedBatchId ? fetchScreenBatchDetail(selectedBatchId) : Promise.resolve(null)),
    [selectedBatchId],
  );
  const { data: polledDetail } = usePolling<ScreenBatchDetail | null>(detailFetcher, {
    shouldStop: () => false,
  });
  useEffect(() => {
    if (polledDetail) setDetail(polledDetail);
  }, [polledDetail]);

  const loadResults = useCallback(async () => {
    if (!selectedBatchId) {
      setResults([]);
      setResultTotal(0);
      return;
    }
    setResultsLoading(true);
    try {
      const data = await fetchScreenResults(selectedBatchId, {
        verdict: verdict === 'all' ? undefined : verdict,
        page: resultPage,
        page_size: 10,
      });
      setResults(data.items);
      setResultTotal(data.total);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setResultsLoading(false);
    }
  }, [selectedBatchId, verdict, resultPage]);

  useEffect(() => {
    void loadResults();
  }, [loadResults]);

  const launch = async () => {
    if (!jobId) {
      message.warning('请选择岗位');
      return;
    }
    let scope: Parameters<typeof createScreenBatch>[1];
    if (scopeType === 'upload_batch') {
      if (!uploadBatchId) {
        message.warning('请选择上传批次');
        return;
      }
      scope = { type: 'upload_batch', upload_batch_id: uploadBatchId };
    } else if (scopeType === 'resume_file_ids') {
      const ids = fileIds
        .split(/[,，\s]+/)
        .map((v) => Number(v.trim()))
        .filter((v) => Number.isInteger(v) && v > 0);
      if (!ids.length) {
        message.warning('请输入文件 ID（逗号分隔）');
        return;
      }
      scope = { type: 'resume_file_ids', ids };
    } else {
      scope = { type: scopeType };
    }
    setCreating(true);
    try {
      const data = await createScreenBatch(jobId, scope);
      message.success(`筛选批次 #${data.batch_id} 已创建（${data.total} 人，预计 LLM 调用 ${data.estimated_llm_calls} 次）`);
      setSelectedBatchId(data.batch_id);
      refreshBatches();
      void loadBatchOptions();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  const handleRetry = async (batchId: number) => {
    setRetrying(batchId);
    try {
      await retryScreenBatch(batchId);
      message.success('已重新入队（复用原批次，只补缺不重跑）');
      refreshBatches();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setRetrying(null);
    }
  };

  const judge = async (manual: Verdict | null) => {
    if (!judging) return;
    try {
      await setScreenManualVerdict(judging.result_id, manual);
      message.success(manual === null ? '已撤销改判' : `已改判为${VERDICT_META[manual].label}`);
      setJudging(null);
      void loadResults();
      if (selectedBatchId) {
        setDetail(await fetchScreenBatchDetail(selectedBatchId).catch(() => detail));
      }
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const batchColumns: ColumnsType<ScreenBatchListItem> = [
    {
      title: '批次',
      dataIndex: 'batch_id',
      width: 110,
      render: (v, row) => (
        <a onClick={() => setSelectedBatchId(v)}>{`#${v}`}</a>
      ),
    },
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—', width: 140 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) =>
        v === 'done' ? <Tag color="success">完成</Tag> : v === 'running' ? <Tag color="processing">执行中</Tag> : v === 'failed' ? <Tag color="error">失败</Tag> : <Tag>排队中</Tag>,
    },
    {
      title: '进度',
      key: 'progress',
      width: 110,
      render: (_, row) => (
        <Text>
          {row.progress}/{row.total}
        </Text>
      ),
    },
    {
      title: '通过/待定/淘汰',
      key: 'counts',
      width: 140,
      render: (_, row) => (
        <Text>
          <Text type="success">{row.passed}</Text> / <Text type="warning">{row.pending_count}</Text> /{' '}
          <Text type="danger">{row.rejected}</Text>
        </Text>
      ),
    },
    { title: '重试', dataIndex: 'retry_count', width: 60 },
    { title: '创建时间', dataIndex: 'created_at', width: 140, render: (v) => formatTime(v) },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) =>
        row.status === 'failed' ? (
          <Popconfirm
            title="重试该批次？"
            description="复用原批次：只补缺不重跑已写结果。"
            okText="确定"
            cancelText="取消"
            onConfirm={() => void handleRetry(row.batch_id)}
          >
            <Button size="small" loading={retrying === row.batch_id}>
              重试
            </Button>
          </Popconfirm>
        ) : (
          <a onClick={() => setSelectedBatchId(row.batch_id)}>查看</a>
        ),
    },
  ];

  const resultColumns: ColumnsType<ScreenResultItem> = useMemo(
    () => [
      {
        title: '姓名',
        dataIndex: 'name',
        width: 110,
        render: (v: string, row) =>
          row.deleted ? (
            <Text type="secondary" delete>
              {v}
              <Tag color="default" style={{ marginLeft: 4 }}>
                已删除
              </Tag>
            </Text>
          ) : (
            <a href={`/talents/${row.talent_profile_id}`}>{v}</a>
          ),
      },
      { title: '职位', dataIndex: 'current_title', width: 120, render: (v) => v ?? '—' },
      {
        title: '得分',
        dataIndex: 'score',
        width: 80,
        render: (v: number | null) => (v === null ? '—' : v),
      },
      {
        title: '结论',
        key: 'effective_verdict',
        width: 90,
        render: (_, row) => {
          const meta = VERDICT_META[row.effective_verdict];
          return <Tag color={meta.color}>{meta.label}</Tag>;
        },
      },
      {
        title: '说明',
        key: 'reason',
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            {row.hard_fail && <Text type="danger">{row.hard_fail_reason}</Text>}
            {row.reason_summary && <Text type="secondary">{row.reason_summary}</Text>}
          </Space>
        ),
      },
      {
        title: '命中/缺失',
        key: 'hits',
        width: 180,
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Text type="secondary">命中：{row.hits.slice(0, 4).join('、') || '—'}</Text>
            <Text type="secondary">缺失：{row.misses.slice(0, 4).join('、') || '—'}</Text>
          </Space>
        ),
      },
      {
        title: '操作',
        key: 'action',
        width: 80,
        render: (_, row) => (
          <a
            onClick={() => setJudging(row)}
            data-testid={`judge-${row.result_id}`}
          >
            改判
          </a>
        ),
      },
    ],
    [],
  );

  return (
    <Card title="岗位筛选">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card type="inner" title="发起筛选">
          <Space wrap>
            <Select
              style={{ width: 220 }}
              placeholder="选择岗位"
              value={jobId}
              onChange={setJobId}
              options={jobs.map((j) => ({ value: j.id, label: `${j.title}（v${j.version}）` }))}
            />
            <Select
              style={{ width: 170 }}
              value={scopeType}
              onChange={setScopeType}
              options={[
                { value: 'all', label: '全部画像（复筛）' },
                { value: 'all_unscreened', label: '未筛选过的画像' },
                { value: 'upload_batch', label: '按上传批次' },
                { value: 'resume_file_ids', label: '按文件 ID' },
              ]}
            />
            {scopeType === 'upload_batch' && (
              <Select
                style={{ width: 220 }}
                placeholder="选择上传批次"
                value={uploadBatchId}
                onChange={setUploadBatchId}
                options={batchOptions}
              />
            )}
            {scopeType === 'resume_file_ids' && (
              <Input
                style={{ width: 280 }}
                placeholder="文件 ID，逗号分隔（如 9,10,11）"
                value={fileIds}
                onChange={(event) => setFileIds(event.target.value)}
              />
            )}
            <Button type="primary" loading={creating} onClick={() => void launch()}>
              发起筛选
            </Button>
          </Space>
        </Card>

        <Card type="inner" title="批次列表（2s 自动刷新）">
          <Table<ScreenBatchListItem>
            rowKey="batch_id"
            size="small"
            loading={!batchesData}
            columns={batchColumns}
            dataSource={batches}
            locale={{ emptyText: <Empty description="暂无筛选批次" /> }}
            pagination={false}
          />
        </Card>

        {detail && (
          <Card
            type="inner"
            title={`批次 #${detail.batch_id} 结果`}
            extra={
              <Space wrap>
                <Text type="secondary">
                  有效完成 {detail.processed}/{detail.total}
                  {detail.skipped_deleted > 0 && `（${detail.skipped_deleted} 人画像已删除被跳过）`}
                </Text>
              </Space>
            }
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {detail.status === 'failed' && (
                <Popconfirm
                  title="重试该批次？"
                  description="复用原批次：只补缺不重跑已写结果。"
                  okText="确定"
                  cancelText="取消"
                  onConfirm={() => void handleRetry(detail.batch_id)}
                >
                  <Button danger size="small" loading={retrying === detail.batch_id}>
                    批次失败：{detail.error ?? '未知错误'}（点击重试，已重试 {detail.retry_count} 次）
                  </Button>
                </Popconfirm>
              )}
              <Tabs
                activeKey={verdict}
                onChange={(key) => {
                  setVerdict(key as Verdict | 'all');
                  setResultPage(1);
                }}
                items={VERDICT_TABS.map((tab) => ({ key: tab.key, label: tab.label }))}
              />
              <Table<ScreenResultItem>
                rowKey="result_id"
                size="small"
                loading={resultsLoading}
                columns={resultColumns}
                dataSource={results}
                locale={{ emptyText: <Empty description="暂无结果" /> }}
                pagination={{
                  current: resultPage,
                  pageSize: 10,
                  total: resultTotal,
                  showSizeChanger: false,
                  onChange: (next) => setResultPage(next),
                }}
              />
            </Space>
          </Card>
        )}
      </Space>

      <Modal
        title={`改判：${judging?.name ?? ''}`}
        open={judging !== null}
        onCancel={() => setJudging(null)}
        footer={
          <Space>
            <Button onClick={() => setJudging(null)}>取消</Button>
            <Button onClick={() => void judge(null)} disabled={!judging?.manual_verdict}>
              撤销改判
            </Button>
            <Button onClick={() => void judge('passed')} type="primary">
              改为通过
            </Button>
            <Button onClick={() => void judge('pending')}>改为待定</Button>
            <Button danger onClick={() => void judge('rejected')}>
              改为淘汰
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={4}>
          <Text>
            当前有效结论：
            {judging && <Tag color={VERDICT_META[judging.effective_verdict].color}>{VERDICT_META[judging.effective_verdict].label}</Tag>}
            （系统判定 {judging?.verdict ?? '—'}
            {judging?.manual_verdict ? `，人工改判 ${judging.manual_verdict}` : ''}）
          </Text>
          <Text type="secondary">改判不改分数，只改有效结论；批次三档计数即时重算。</Text>
        </Space>
      </Modal>
    </Card>
  );
}
