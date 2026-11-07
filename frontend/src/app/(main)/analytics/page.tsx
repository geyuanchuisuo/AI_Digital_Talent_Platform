/**
 * 问数指标卡（T-D01b 垂直切片）：活跃岗位数。
 *
 * 口径（docs/modules/A/指标口径交付.md §1）：is_active=1 且按 title 去重取最新 version。
 * 端到端：PgSQL 副本（未配置降级主库）→ Redis 缓存 → 查询快照落库；
 * SSE 进度按钮演示流式阶段（started/computed/done）。
 */

'use client';

import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Button, Card, Space, Statistic, Tag, Typography } from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { fetchActiveJobs, streamActiveJobsEvents, type ActiveJobsData, type SseStage } from './api';

const { Text } = Typography;

const SOURCE_TEXT: Record<ActiveJobsData['source'], string> = {
  replica: 'PgSQL 副本',
  primary: '主库直查',
};

export default function AnalyticsPage() {
  const [data, setData] = useState<ActiveJobsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [sseStage, setSseStage] = useState<SseStage | null>(null);
  const [sseRunning, setSseRunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchActiveJobs());
    } catch (e) {
      // 403（个人角色无此指标）/网络错误由 http 统一提示；这里只兜底文案
      console.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runSse = useCallback(async () => {
    setSseRunning(true);
    setSseStage(null);
    try {
      await streamActiveJobsEvents((event, payload) => {
        if (event === 'progress' && typeof payload.stage === 'string') {
          setSseStage(payload.stage as SseStage);
        }
        if (event === 'done') setSseStage('done');
      });
    } catch (e) {
      console.error(getErrorMessage(e));
    } finally {
      setSseRunning(false);
    }
  }, []);

  return (
    <Card
      title="问数指标（切片演示）"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      }
      style={{ maxWidth: 480 }}
    >
      <Statistic title="活跃岗位数" value={data?.value ?? '-'} loading={loading} />
      {data && (
        <Space size={4} wrap style={{ marginTop: 12 }}>
          <Tag color={data.source === 'replica' ? 'green' : 'orange'}>来源：{SOURCE_TEXT[data.source]}</Tag>
          <Tag color={data.cache_hit ? 'blue' : 'default'}>{data.cache_hit ? '缓存命中' : '缓存未命中'}</Tag>
          <Text type="secondary">数据时间 {data.data_time.replace('T', ' ').slice(0, 19)}</Text>
        </Space>
      )}
      <div style={{ marginTop: 16 }}>
        <Button
          icon={<ThunderboltOutlined />}
          onClick={() => void runSse()}
          loading={sseRunning}
          disabled={!data}
        >
          计算进度（SSE）
        </Button>
        {sseStage && (
          <Text code style={{ marginLeft: 8 }}>
            {sseStage}
          </Text>
        )}
      </div>
    </Card>
  );
}
