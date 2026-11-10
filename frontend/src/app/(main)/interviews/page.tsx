/**
 * C 人工面试页：候选人查看本人安排；面试人查看被授权轮次并填写结构化反馈；
 * 管理员手动新增/改期/完成/取消轮次。面试不计算总分、不自动推进招聘。
 */

'use client';

import { PlusOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

import { getUser, isManagement } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import { cancelRound, createRound, fetchMyRounds, fetchRounds, getRound, saveFeedback, updateRound } from './api';
import type { InterviewRound, MyRoundItem, RoundDetail } from './types';

const PAGE_SIZE = 10;

const MODE_LABEL = { onsite: '现场', online: '线上', phone: '电话' } as const;

function statusTag(status: string) {
  if (status === 'completed') return <Tag color="success">已完成</Tag>;
  if (status === 'cancelled') return <Tag color="default">已取消</Tag>;
  if (status === 'in_progress') return <Tag color="warning">进行中</Tag>;
  return <Tag color="processing">待进行</Tag>;
}

function RoundDrawer({
  roundId,
  open,
  onClose,
  onChanged,
}: {
  roundId: number | null;
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<RoundDetail | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    if (!roundId) return;
    try {
      const data = await getRound(roundId);
      setDetail(data);
      if (data.feedback) form.setFieldsValue(data.feedback);
      else form.resetFields();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, [roundId, form]);

  useEffect(() => {
    if (open && roundId) void load();
  }, [open, roundId, load]);

  const internal = Boolean(detail?.internal_note !== undefined || detail?.events);
  const canFeedback = internal && detail?.status === 'completed';

  const submitFeedback = async () => {
    if (!roundId) return;
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    try {
      await saveFeedback(roundId, values);
      message.success('反馈已保存');
      await load();
      onChanged();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  return (
    <Drawer title={detail ? `第 ${detail.round_no} 轮 · ${detail.round_name}` : '面试轮次'} width={680} open={open} onClose={onClose}>
      {!detail ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="岗位">{detail.job_title || '—'}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
            <Descriptions.Item label="时间">{detail.scheduled_at || '待定'}</Descriptions.Item>
            <Descriptions.Item label="方式">{MODE_LABEL[detail.mode]}</Descriptions.Item>
            <Descriptions.Item label="地点/链接" span={2}>
              {detail.location_or_link || '—'}
            </Descriptions.Item>
            {internal && (
              <Descriptions.Item label="面试人（内部）" span={2}>
                user_id：{detail.interviewer_user_ids.join('、')}
              </Descriptions.Item>
            )}
            {internal && detail.internal_note && (
              <Descriptions.Item label="内部备注" span={2}>
                {detail.internal_note}
              </Descriptions.Item>
            )}
          </Descriptions>

          {internal && detail.events && (
            <Card size="small" title="变更记录">
              <Timeline
                items={detail.events.map((event) => ({
                  children: `${event.event_type} · ${event.created_at}`,
                }))}
              />
            </Card>
          )}

          {canFeedback && (
            <Card size="small" title={detail.feedback ? `结构化反馈 v${detail.feedback.version}` : '填写结构化反馈'}>
              <AlertHint />
              <Form form={form} layout="vertical">
                <Form.Item name="evaluation" label="本轮评价" rules={[{ required: true }]}>
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="evidence" label="事实依据">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name="strengths" label="优势">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name="weaknesses" label="不足">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name="recommendation" label="推进建议" rules={[{ required: true }]}>
                  <Select
                    options={[
                      { value: 'advance', label: '建议推进' },
                      { value: 'hold', label: '暂缓' },
                      { value: 'reject', label: '不建议推进' },
                    ]}
                  />
                </Form.Item>
                <Button type="primary" onClick={() => void submitFeedback()}>
                  保存反馈
                </Button>
              </Form>
            </Card>
          )}
        </Space>
      )}
    </Drawer>
  );
}

function AlertHint() {
  return (
    <Typography.Paragraph type="secondary">
      反馈仅管理员与本轮面试人可见，候选人不展示；本轮不计算面试总分，也不会自动推进招聘状态。
    </Typography.Paragraph>
  );
}

function MyRounds() {
  const [rows, setRows] = useState<MyRoundItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchMyRounds();
      setRows(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const columns: ColumnsType<MyRoundItem> = [
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
    { title: '轮次', render: (_, row) => `第 ${row.round_no} 轮 · ${row.round_name}` },
    { title: '时间', dataIndex: 'scheduled_at', render: (v) => v || '待定' },
    { title: '方式', dataIndex: 'mode', width: 80, render: (v) => MODE_LABEL[v as keyof typeof MODE_LABEL] },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '视角',
      dataIndex: 'view',
      width: 90,
      render: (v) => (v === 'candidate' ? <Tag>候选人</Tag> : <Tag color="blue">面试人</Tag>),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, row) => (
        <a
          onClick={() => {
            setActive(row.id);
            setOpen(true);
          }}
        >
          详情
        </a>
      ),
    },
  ];

  return (
    <Card>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={false} />
      <RoundDrawer roundId={active} open={open} onClose={() => setOpen(false)} onChanged={() => void load()} />
    </Card>
  );
}

function RoundAdmin() {
  const [rows, setRows] = useState<InterviewRound[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [rescheduling, setRescheduling] = useState<InterviewRound | null>(null);
  const [form] = Form.useForm();
  const [rescheduleForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchRounds({ page, page_size: PAGE_SIZE });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    try {
      await createRound({
        candidate_user_id: values.candidate_user_id,
        talent_profile_id: values.talent_profile_id ?? null,
        job_title: values.job_title,
        round_no: values.round_no,
        round_name: values.round_name,
        scheduled_at: values.scheduled_at ? values.scheduled_at.replace(' ', 'T') : null,
        mode: values.mode,
        location_or_link: values.location_or_link,
        interviewer_user_ids: values.interviewer_user_ids,
        internal_note: values.internal_note,
      });
      message.success('面试轮次已创建');
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitReschedule = async () => {
    if (!rescheduling) return;
    const values = await rescheduleForm.validateFields().catch(() => null);
    if (!values) return;
    try {
      await updateRound(rescheduling.id, {
        scheduled_at: values.scheduled_at ? values.scheduled_at.replace(' ', 'T') : null,
        mode: values.mode,
        location_or_link: values.location_or_link,
      });
      message.success('已改期');
      setRescheduling(null);
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<InterviewRound> = [
    { title: '候选人', dataIndex: 'candidate_user_id', width: 90, render: (v) => `#${v ?? '—'}` },
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
    { title: '轮次', render: (_, row) => `第 ${row.round_no} 轮 · ${row.round_name}` },
    { title: '时间', dataIndex: 'scheduled_at', render: (v) => v || '待定' },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 250,
      render: (_, row) => (
        <Space size={8} wrap>
          <a
            onClick={() => {
              setActive(row.id);
              setDrawerOpen(true);
            }}
          >
            详情/反馈
          </a>
          {(row.status === 'pending' || row.status === 'in_progress') && (
            <a
              onClick={() => {
                setRescheduling(row);
                rescheduleForm.setFieldsValue({
                  scheduled_at: row.scheduled_at ? row.scheduled_at.replace('T', ' ').slice(0, 16) : undefined,
                  mode: row.mode,
                  location_or_link: row.location_or_link ?? undefined,
                });
              }}
            >
              改期
            </a>
          )}
          {row.status === 'pending' && (
            <a
              onClick={async () => {
                try {
                  await updateRound(row.id, { status: 'in_progress' });
                  message.success('已记录开展');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              记录开展
            </a>
          )}
          {row.status === 'in_progress' && (
            <a
              onClick={async () => {
                try {
                  await updateRound(row.id, { status: 'completed' });
                  message.success('已标记完成，可填写反馈');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              标记完成
            </a>
          )}
          {row.status !== 'cancelled' && row.status !== 'completed' && (
            <a
              style={{ color: '#cf1322' }}
              onClick={async () => {
                try {
                  await cancelRound(row.id);
                  message.success('已取消');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              取消
            </a>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="面试轮次"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新增轮次
        </Button>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ current: page, pageSize: PAGE_SIZE, total, onChange: setPage, showSizeChanger: false }}
      />
      <Modal title="新增面试轮次" open={modalOpen} width={620} onOk={() => void submit()} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ round_no: 1, mode: 'onsite', interviewer_user_ids: [] }}>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="candidate_user_id" label="候选人 user_id" style={{ flex: 1 }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="talent_profile_id" label="人才档案 ID（可选）" style={{ flex: 1 }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="job_title" label="岗位" style={{ flex: 2 }}>
              <Input />
            </Form.Item>
            <Form.Item name="round_no" label="轮次序号" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} max={20} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="round_name" label="轮次名称" style={{ flex: 2 }} rules={[{ required: true }]}>
              <Input placeholder="如：技术一面" />
            </Form.Item>
          </Space>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="scheduled_at" label="时间（YYYY-MM-DD HH:mm）" style={{ flex: 1 }}>
              <Input placeholder="2026-11-08 10:00" />
            </Form.Item>
            <Form.Item name="mode" label="方式" style={{ flex: 1 }}>
              <Select options={[{ value: 'onsite', label: '现场' }, { value: 'online', label: '线上' }, { value: 'phone', label: '电话' }]} />
            </Form.Item>
          </Space>
          <Form.Item name="location_or_link" label="地点或会议链接">
            <Input />
          </Form.Item>
          <Form.Item name="interviewer_user_ids" label="面试人 user_id（可多选，逗号分隔填写）">
            <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入 user_id 后回车" />
          </Form.Item>
          <Form.Item name="internal_note" label="内部备注（候选人与会看不到）">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={rescheduling ? `改期：第 ${rescheduling.round_no} 轮 · ${rescheduling.round_name}` : '改期'}
        open={!!rescheduling}
        onOk={() => void submitReschedule()}
        onCancel={() => setRescheduling(null)}
      >
        <Form form={rescheduleForm} layout="vertical">
          <Form.Item name="scheduled_at" label="时间（YYYY-MM-DD HH:mm）">
            <Input placeholder="2026-11-10 14:00" />
          </Form.Item>
          <Form.Item name="mode" label="方式">
            <Select options={[{ value: 'onsite', label: '现场' }, { value: 'online', label: '线上' }, { value: 'phone', label: '电话' }]} />
          </Form.Item>
          <Form.Item name="location_or_link" label="地点或会议链接">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <RoundDrawer roundId={active} open={drawerOpen} onClose={() => setDrawerOpen(false)} onChanged={() => void load()} />
    </Card>
  );
}

export default function InterviewsPage() {
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    setIsAdmin(isManagement());
  }, []);

  return (
    <Card title="人工面试">
      <Tabs
        items={[
          { key: 'mine', label: '我的安排', children: <MyRounds /> },
          ...(isAdmin ? [{ key: 'admin', label: '轮次管理', children: <RoundAdmin /> }] : []),
        ]}
      />
    </Card>
  );
}
