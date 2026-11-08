/**
 * 问数页（T-D01b 切片 + T-D10 多维分析/自动出图/决策建议）。
 *
 * 口径（docs/modules/A/指标口径交付.md §1）：活跃岗位数 = is_active=1 且按 title
 * 去重取最新 version。支持按部门分组（group_by），图表数据由后端从同一表格派生
 * （图表数据与表格一致），解读分段带 fact/inference 标记（机器可区分）。
 */

'use client';

import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Bar } from '@ant-design/charts';
import { Button, Card, Empty, List, Space, Statistic, Switch, Tag, Typography } from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import {
  fetchActiveJobs,
  streamActiveJobsEvents,
  type ActiveJobsData,
  type InterpretationSegment,
  type SseStage,
} from './api';

const { Text } = Typography;

const SOURCE_TEXT: Record<ActiveJobsData['source'], string> = {
  replica: 'PgSQL 副本',
  primary: '主库直查',
};

const SEGMENT_TAG: Record<InterpretationSegment['kind'], { color: string; text: string }> = {
  fact: { color: 'blue', text: '事实' },
  inference: { color: 'purple', text: '推断' },
};

export default function AnalyticsPage() {
  const [data, setData] = useState<ActiveJobsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [groupBy, setGroupBy] = useState(false);
  const [sseStage, setSseStage] = useState<SseStage | null>(null);
  const [sseRunning, setSseRunning] = useState(false);

  const load = useCallback(async (gb: boolean) => {
    setLoading(true);
    try {
      setData(await fetchActiveJobs(gb ? 'department' : undefined));
    } catch (e) {
      // 403（个人角色无此指标）/网络错误由 http 统一提示；这里只兜底文案
      console.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(groupBy);
  }, [groupBy, load]);

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

  const chart = data?.chart;
  const interp = data?.interpretation;

  return (
    <Card
      title="问数指标（多维分析）"
      extra={
        <Space>
          <Text type="secondary">按部门分组</Text>
          <Switch checked={groupBy} onChange={(v) => setGroupBy(v)} />
          <Button icon={<ReloadOutlined />} onClick={() => void load(groupBy)} loading={loading}>
            刷新
          </Button>
        </Space>
      }
      style={{ maxWidth: 640 }}
    >
      <Statistic title="活跃岗位数" value={data?.value ?? '-'} loading={loading} />
      {data && (
        <Space size={4} wrap style={{ marginTop: 12 }}>
          <Tag color={data.source === 'replica' ? 'green' : 'orange'}>来源：{SOURCE_TEXT[data.source]}</Tag>
          <Tag color={data.cache_hit ? 'blue' : 'default'}>{data.cache_hit ? '缓存命中' : '缓存未命中'}</Tag>
          {data.group_by && <Tag>分组：按部门</Tag>}
          <Text type="secondary">数据时间 {data.data_time.replace('T', ' ').slice(0, 19)}</Text>
        </Space>
      )}

      {chart?.chart_type === 'number' && chart.value != null && (
        <Statistic
          title={chart.label ?? '数值'}
          value={chart.value}
          style={{ marginTop: 16 }}
        />
      )}
      {chart?.chart_type === 'bar' && chart.data && chart.data.length > 0 && (
        <div style={{ marginTop: 16, height: 240 }}>
          <Bar
            data={chart.data}
            xField="value"
            yField="name"
            height={230}
            axis={{ x: { title: false }, y: { title: false } }}
          />
        </div>
      )}
      {chart?.chart_type === 'none' && (
        <Empty description={chart.reason ?? '暂无数据'} style={{ marginTop: 16 }} />
      )}

      {interp && interp.segments.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Text strong>解读（事实/推断已分段）</Text>
          <List
            size="small"
            dataSource={interp.segments}
            renderItem={(seg: InterpretationSegment) => (
              <List.Item style={{ padding: '6px 0' }}>
                <Space size={6} align="start">
                  <Tag color={SEGMENT_TAG[seg.kind].color}>{SEGMENT_TAG[seg.kind].text}</Tag>
                  <Text>{seg.text}</Text>
                </Space>
              </List.Item>
            )}
          />
        </div>
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
