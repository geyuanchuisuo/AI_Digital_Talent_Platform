/**
 * 岗位匹配面板（T-28，需求 F3-2~F3-7 / 接口 §8）。
 *
 * 口径：
 * 1. **发起匹配**：岗位下拉 + 三种范围（all / filter / upload_batch）；
 *    filter 展开学历/年限/标签输入（tags 逗号分隔，**AND 语义**，后端 §8 取舍）。
 * 2. **批次列表 2s 轮询**；`vector_degraded=true` 黄色标记
 *    （提示"本批次向量依赖不可用，经历维度仅按 LLM 评分，不宜与正常批次直接比较"）。
 * 3. **名次表**：rank（查询时计算）、score、硬淘汰 score=0 置底红色标记；
 *    点行 → 理由详情弹窗（四维分 + vector_similarity + hits/misses + reason）。
 * 4. failed 批次 retry（复用原批次）。
 */

'use client';

import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';
import { usePolling } from '@/lib/usePolling';

import { fetchJobOptions } from '../screen/api';
import { formatBatchId, formatTime } from '../screen/utils';
import {
  createMatchBatch,
  fetchMatchBatchDetail,
  fetchMatchBatches,
  fetchMatchResultDetail,
  fetchMatchResults,
  retryMatchBatch,
} from './api';
import type {
  MatchBatchDetail,
  MatchBatchListItem,
  MatchResultDetail,
  MatchResultItem,
} from './types';

const { Text } = Typography;
const { Search } = Input;

const DIM_LABELS: Record<string, string> = {
  skills: '技能',
  experience: '经验',
  education: '学历',
  others: '其他',
};

export default function MatchPanel() {
  // —— 发起匹配 ——
  const [jobs, setJobs] = useState<{ id: number; title: string; version: number }[]>([]);
  const [jobId, setJobId] = useState<number | undefined>(undefined);
  const [scopeType, setScopeType] = useState<'all' | 'filter' | 'upload_batch'>('all');
  const [uploadBatchId, setUploadBatchId] = useState<string | undefined>(undefined);
  const [filterDegree, setFilterDegree] = useState<string | undefined>(undefined);
  const [filterMinYears, setFilterMinYears] = useState<number | undefined>(undefined);
  const [filterTags, setFilterTags] = useState('');
  const [creating, setCreating] = useState(false);

  // —— 批次与结果 ——
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MatchBatchDetail | null>(null);
  const [results, setResults] = useState<MatchResultItem[]>([]);
  const [resultTotal, setResultTotal] = useState(0);
  const [resultPage, setResultPage] = useState(1);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [detailRow, setDetailRow] = useState<MatchResultDetail | null>(null);
  const [retrying, setRetrying] = useState<number | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const options = await fetchJobOptions();
      setJobs(options);
      setJobId((prev) => prev ?? options[0]?.id);
    } catch {
      /* 统一错误提示由 lib/http 拦截器负责 */
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const batchesFetcher = useCallback(() => fetchMatchBatches(undefined, 1, 20), []);
  const { data: batchesData, refresh: refreshBatches } = usePolling(batchesFetcher, {
    shouldStop: () => false,
  });
  const batches = batchesData?.items ?? [];

  const detailFetcher = useCallback(
    () => (selectedBatchId ? fetchMatchBatchDetail(selectedBatchId) : Promise.resolve(null)),
    [selectedBatchId],
  );
  const { data: polledDetail } = usePolling<MatchBatchDetail | null>(detailFetcher, {
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
      const data = await fetchMatchResults(selectedBatchId, resultPage, 10);
      setResults(data.items);
      setResultTotal(data.total);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setResultsLoading(false);
    }
  }, [selectedBatchId, resultPage]);

  useEffect(() => {
    void loadResults();
  }, [loadResults]);

  const launch = async () => {
    if (!jobId) {
      message.warning('请选择岗位');
      return;
    }
    let scope: Parameters<typeof createMatchBatch>[1];
    if (scopeType === 'upload_batch') {
      if (!uploadBatchId) {
        message.warning('请选择上传批次');
        return;
      }
      scope = { type: 'upload_batch', upload_batch_id: uploadBatchId };
    } else if (scopeType === 'filter') {
      scope = {
        type: 'filter',
        degree: filterDegree,
        min_years: filterMinYears,
        tags: filterTags
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean),
      };
    } else {
      scope = { type: 'all' };
    }
    setCreating(true);
    try {
      const data = await createMatchBatch(jobId, scope);
      message.success(`匹配批次 #${data.batch_id} 已创建（${data.total} 人）`);
      setSelectedBatchId(data.batch_id);
      refreshBatches();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  const handleRetry = async (batchId: number) => {
    setRetrying(batchId);
    try {
      await retryMatchBatch(batchId);
      message.success('已重新入队（复用原批次，只补缺不重跑）');
      refreshBatches();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setRetrying(null);
    }
  };

  const openDetail = async (resultId: number) => {
    try {
      setDetailRow(await fetchMatchResultDetail(resultId));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const batchColumns: ColumnsType<MatchBatchListItem> = [
    {
      title: '批次',
      dataIndex: 'batch_id',
      width: 100,
      render: (v) => <a onClick={() => setSelectedBatchId(v)}>{`#${v}`}</a>,
    },
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—', width: 140 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) =>
        v === 'done' ? (
          <Tag color="success">完成</Tag>
        ) : v === 'running' ? (
          <Tag color="processing">执行中</Tag>
        ) : v === 'failed' ? (
          <Tag color="error">失败</Tag>
        ) : (
          <Tag>排队中</Tag>
        ),
    },
    {
      title: '进度',
      key: 'progress',
      width: 100,
      render: (_, row) => (
        <Text>
          {row.progress}/{row.total}
        </Text>
      ),
    },
    { title: '硬淘汰', dataIndex: 'hard_failed', width: 80 },
    {
      title: '向量',
      dataIndex: 'vector_degraded',
      width: 90,
      render: (degraded: boolean) =>
        degraded ? (
          <Tag color="warning">已降级</Tag>
        ) : (
          <Tag color="success">正常</Tag>
        ),
    },
    { title: '重试', dataIndex: 'retry_count', width: 60 },
    { title: '创建时间', dataIndex: 'created_at', width: 140, render: (v) => formatTime(v) },
    {
      title: '操作',
      key: 'action',
      width: 80,
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

  const resultColumns: ColumnsType<MatchResultItem> = [
    { title: '名次', dataIndex: 'rank', width: 70 },
    {
      title: '姓名',
      dataIndex: 'name',
      width: 120,
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
    { title: '职位', dataIndex: 'current_title', width: 130, render: (v) => v ?? '—' },
    {
      title: '匹配分',
      dataIndex: 'score',
      width: 90,
      render: (v: number, row) =>
        row.hard_fail ? <Text type="danger">{v}</Text> : <Text strong>{v}</Text>,
    },
    {
      title: '硬门槛',
      dataIndex: 'hard_fail',
      width: 100,
      render: (fail: boolean, row) =>
        fail ? (
          <Tooltip title={row.hard_fail_reason}>
            <Tag color="error">不通过</Tag>
          </Tooltip>
        ) : (
          <Tag color="success">通过</Tag>
        ),
    },
    { title: '命中/缺失', key: 'counts', width: 110, render: (_, row) => `${row.hits_count} / ${row.misses_count}` },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, row) => <a onClick={() => void openDetail(row.result_id)}>理由</a>,
    },
  ];

  return (
    <Card title="岗位匹配">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card type="inner" title="发起匹配">
          <Space wrap>
            <Select
              style={{ width: 220 }}
              placeholder="选择岗位"
              value={jobId}
              onChange={setJobId}
              options={jobs.map((j) => ({ value: j.id, label: `${j.title}（v${j.version}）` }))}
            />
            <Select
              style={{ width: 150 }}
              value={scopeType}
              onChange={setScopeType}
              options={[
                { value: 'all', label: '全部画像' },
                { value: 'filter', label: '按条件过滤' },
                { value: 'upload_batch', label: '按上传批次' },
              ]}
            />
            {scopeType === 'upload_batch' && (
              <Input
                style={{ width: 260 }}
                placeholder="上传批次 ID（screen 页可复制）"
                value={uploadBatchId}
                onChange={(event) => setUploadBatchId(event.target.value)}
              />
            )}
            {scopeType === 'filter' && (
              <>
                <Select
                  allowClear
                  style={{ width: 130 }}
                  placeholder="学历"
                  value={filterDegree}
                  onChange={setFilterDegree}
                  options={['博士', '硕士', '本科', '大专', '高中中专', '其他'].map((value) => ({
                    value,
                    label: value,
                  }))}
                />
                <InputNumber
                  min={0}
                  max={80}
                  placeholder="年限≥"
                  value={filterMinYears}
                  onChange={(value) => setFilterMinYears(value ?? undefined)}
                  style={{ width: 100 }}
                />
                <Search
                  allowClear
                  style={{ width: 240 }}
                  placeholder="标签（逗号分隔，AND）"
                  value={filterTags}
                  onChange={(event) => setFilterTags(event.target.value)}
                />
              </>
            )}
            <Button type="primary" loading={creating} onClick={() => void launch()}>
              发起匹配
            </Button>
          </Space>
        </Card>

        <Card type="inner" title="批次列表（2s 自动刷新）">
          <Table<MatchBatchListItem>
            rowKey="batch_id"
            size="small"
            loading={!batchesData}
            columns={batchColumns}
            dataSource={batches}
            locale={{ emptyText: <Empty description="暂无匹配批次" /> }}
            pagination={false}
          />
        </Card>

        {detail && (
          <Card
            type="inner"
            title={`批次 #${detail.batch_id} 名次`}
            extra={
              <Text type="secondary">
                有效完成 {detail.processed}/{detail.total} · 硬淘汰 {detail.hard_failed}
              </Text>
            }
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {detail.vector_degraded && (
                <Alert
                  type="warning"
                  showIcon
                  message="本批次向量依赖不可用，经历维度仅按 LLM 评分，不宜与正常批次直接比较"
                />
              )}
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
              <Table<MatchResultItem>
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
        title={`匹配详情：${detailRow?.name ?? ''}（第 ${detailRow?.rank ?? '—'} 名）`}
        open={detailRow !== null}
        onCancel={() => setDetailRow(null)}
        footer={null}
        width={620}
      >
        {detailRow === null ? (
          <Spin />
        ) : (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {detailRow.vector_degraded && (
              <Alert type="warning" showIcon message="本批次向量降级，经历维度仅按 LLM 评分" />
            )}
            <Space wrap size={16}>
              <Text strong>
                总分：<Text type={detailRow.hard_fail ? 'danger' : undefined}>{detailRow.score}</Text>
              </Text>
              {detailRow.hard_fail && <Tag color="error">硬淘汰：{detailRow.hard_fail_reason}</Tag>}
              {detailRow.vector_similarity !== null && (
                <Text type="secondary">向量相似度：{Math.round(detailRow.vector_similarity * 100)}%</Text>
              )}
            </Space>
            <Space wrap size={8}>
              {Object.entries(detailRow.dimension_scores)
                .filter(([key]) => key !== 'vector_similarity')
                .map(([key, value]) => (
                  <Tag key={key}>
                    {DIM_LABELS[key] ?? key}：{value ?? '—'}
                  </Tag>
                ))}
            </Space>
            <div>
              <Text type="success">命中：</Text>
              <Text>{detailRow.hits.join('、') || '—'}</Text>
            </div>
            <div>
              <Text type="danger">缺失：</Text>
              <Text>{detailRow.misses.join('、') || '—'}</Text>
            </div>
            <div>
              <Text strong>理由：</Text>
              <Text type="secondary">{detailRow.reason ?? '—'}</Text>
            </div>
          </Space>
        )}
      </Modal>
    </Card>
  );
}

