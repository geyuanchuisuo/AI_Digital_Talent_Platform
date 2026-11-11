/**
 * 问数页（T-D01b 切片 + T-D10 多维分析/自动出图/决策建议 + T-D12 反馈入口）。
 *
 * 口径（docs/modules/A/指标口径交付.md §1）：活跃岗位数 = is_active=1 且按 title
 * 去重取最新 version。支持按部门分组（group_by），图表数据由后端从同一表格派生
 * （图表数据与表格一致），解读分段带 fact/inference 标记（机器可区分）。
 * T-D12：结果卡「标记不准确」入口（仅结果创建者，幂等）+ 管理侧反馈列表。
 */

'use client';

import { FlagOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Bar } from '@ant-design/charts';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Space,
  Statistic,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import {
  createFeedback,
  fetchActiveJobs,
  fetchFeedback,
  streamActiveJobsEvents,
  type ActiveJobsData,
  type InterpretationSegment,
  type MetricFeedbackItem,
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
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [groupBy, setGroupBy] = useState(false);
  const [sseStage, setSseStage] = useState<SseStage | null>(null);
  const [sseRunning, setSseRunning] = useState(false);
  // T-D12 反馈
  const [fbOpen, setFbOpen] = useState(false);
  const [fbComment, setFbComment] = useState('');
  const [fbSubmitting, setFbSubmitting] = useState(false);
  const [feedbacks, setFeedbacks] = useState<MetricFeedbackItem[] | null>(null);

  const load = useCallback(async (gb: boolean) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchActiveJobs(gb ? 'department' : undefined));
    } catch (e) {
      // 403（个人角色无此指标）/ 422（指标未登记）/ 网络错误：http 已统一提示，
      // 此处只把文案落到页面（不再静默 console），避免把错误响应喂给下游组件。
      const msg = getErrorMessage(e);
      setError(msg);
      console.error(msg);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFeedback = useCallback(async () => {
    try {
      setFeedbacks(await fetchFeedback());
    } catch {
      // 403（非管理侧）→ 隐藏反馈列表
      setFeedbacks(null);
    }
  }, []);

  useEffect(() => {
    void load(groupBy);
    void loadFeedback();
  }, [groupBy, load, loadFeedback]);

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

  const submitFeedback = useCallback(async () => {
    if (!data?.snapshot_id) return;
    setFbSubmitting(true);
    try {
      await createFeedback(data.snapshot_id, fbComment || undefined);
      message.success('已收到反馈，感谢纠错（不影响当次结果）');
      setFbOpen(false);
      setFbComment('');
      void loadFeedback();
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setFbSubmitting(false);
    }
  }, [data?.snapshot_id, fbComment, loadFeedback]);

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
      {error && (
        <Alert
          style={{ marginTop: 16 }}
          type="error"
          showIcon
          message="查询失败"
          description={error}
          action={
            <Button size="small" onClick={() => void load(groupBy)}>
              重试
            </Button>
          }
        />
      )}
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
        <div style={{ marginTop: 16 }}>
          <div style={{ height: 240 }}>
            <Bar
              data={chart.data}
              xField="value"
              yField="name"
              height={230}
              axis={{ x: { title: false }, y: { title: false } }}
            />
          </div>
          {/* T-D-SELF-12：截断必须可见，避免「图表只画前 20 项」被误读为全量 */}
          {chart.truncated_series && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              仅显示前 {chart.data.length} 项
              （共 {data?.table?.rows?.length ?? chart.data.length} 项，其余未绘制）
            </Text>
          )}
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
        <Space>
          <Button
            icon={<FlagOutlined />}
            onClick={() => setFbOpen(true)}
            disabled={!data?.snapshot_id}
          >
            标记不准确
          </Button>
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
        </Space>
      </div>

      {feedbacks !== null && (
        <div style={{ marginTop: 16 }}>
          <Text strong>反馈记录（管理侧可见）</Text>
          {feedbacks.length === 0 ? (
            <Empty description="暂无反馈" style={{ marginTop: 8 }} />
          ) : (
            <List
              size="small"
              dataSource={feedbacks}
              style={{ marginTop: 8 }}
              renderItem={(fb: MetricFeedbackItem) => (
                <List.Item style={{ padding: '6px 0' }}>
                  <Space size={6} align="start">
                    <Tag>{fb.snapshot_metric_id ?? `快照#${fb.snapshot_id}`}</Tag>
                    <Text>{fb.comment || '（未填说明）'}</Text>
                    <Text type="secondary">
                      {fb.created_at.replace('T', ' ').slice(0, 19)}
                    </Text>
                  </Space>
                </List.Item>
              )}
            />
          )}
        </div>
      )}

      <Modal
        title="标记结果不准确"
        open={fbOpen}
        onOk={() => void submitFeedback()}
        onCancel={() => setFbOpen(false)}
        confirmLoading={fbSubmitting}
        okText="提交反馈"
        cancelText="取消"
      >
        <Text type="secondary">
          反馈仅用于纠错复核（关联本次查询快照 # {data?.snapshot_id ?? '-'}），不会修改当次结果。
        </Text>
        <Input.TextArea
          rows={3}
          maxLength={500}
          showCount
          value={fbComment}
          onChange={(e) => setFbComment(e.target.value)}
          placeholder="说明不准确之处（可选，≤500 字）"
          style={{ marginTop: 12 }}
        />
      </Modal>
    </Card>
  );
}
