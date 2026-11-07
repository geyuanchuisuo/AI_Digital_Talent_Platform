/**
 * F10 人才盘点页：员工查看本人复核与九宫格结论、提交异议；
 * 管理员创建/提交/确认复核、维护人才池、查看部门匿名汇总并发起发展计划。
 * 九宫格与内部评分仅管理员可见，员工视角由后端裁剪。
 */

'use client';

import { PlusOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

import { getUser } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import {
  confirmReview,
  createDispute,
  createReview,
  fetchPools,
  fetchReviews,
  fetchSummary,
  nominatePool,
  decidePool,
  submitReview,
  updateReview,
  upsertIdp,
} from './api';
import type { InventoryReview, InventorySummary, TalentPoolItem } from './types';

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  submitted: '已提交',
  confirmed: '已确认',
  cancelled: '已取消',
};

function statusTag(status: string) {
  if (status === 'confirmed') return <Tag color="success">已确认</Tag>;
  if (status === 'submitted') return <Tag color="processing">已提交</Tag>;
  if (status === 'cancelled') return <Tag>已取消</Tag>;
  return <Tag color="default">草稿</Tag>;
}

function MyReviews() {
  const [rows, setRows] = useState<InventoryReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [disputeTarget, setDisputeTarget] = useState<InventoryReview | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchReviews());
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submitDispute = async () => {
    if (!disputeTarget) return;
    const values = await form.validateFields();
    try {
      await createDispute(disputeTarget.id, values.content);
      message.success('异议已提交，等待 HR 回复');
      setDisputeTarget(null);
      form.resetFields();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<InventoryReview> = [
    { title: '周期', dataIndex: 'cycle_name' },
    { title: '类型', dataIndex: 'review_type', width: 90, render: (v) => (v === 'annual' ? '年度' : '临时') },
    { title: '状态', dataIndex: 'status', width: 100, render: statusTag },
    { title: '确认时间', dataIndex: 'confirmed_at', render: (v) => v ?? '—' },
    { title: '总体评语', dataIndex: 'summary', render: (v) => v || '—' },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, row) =>
        row.status === 'confirmed' ? <a onClick={() => setDisputeTarget(row)}>提交异议</a> : '—',
    },
  ];

  return (
    <Card>
      <Typography.Paragraph type="secondary">
        内部评级与九宫格不对员工展示；对已确认的复核如有异议，可提交一次说明，由 HR 回复。
      </Typography.Paragraph>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={false} />
      <Modal
        title="提交盘点异议"
        open={Boolean(disputeTarget)}
        onOk={() => void submitDispute()}
        onCancel={() => setDisputeTarget(null)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="content" label="异议说明" rules={[{ required: true, min: 1, max: 2000 }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function ReviewAdmin() {
  const [rows, setRows] = useState<InventoryReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<InventoryReview | null>(null);
  const [idpTarget, setIdpTarget] = useState<InventoryReview | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [idpForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchReviews());
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submitCreate = async () => {
    const values = await form.validateFields();
    try {
      await createReview(values);
      message.success('复核已创建');
      setCreateOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitEdit = async () => {
    if (!editTarget) return;
    const values = await editForm.validateFields();
    try {
      await updateReview(editTarget.id, values);
      message.success('评分已更新，九宫格已重算');
      setEditTarget(null);
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitIdp = async () => {
    if (!idpTarget) return;
    const values = await idpForm.validateFields();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(values.content as string);
    } catch {
      message.error('发展计划需为合法 JSON');
      return;
    }
    try {
      await upsertIdp(idpTarget.id, { content_json: parsed, status: values.status });
      message.success('发展计划已保存');
      setIdpTarget(null);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<InventoryReview> = [
    { title: '员工', dataIndex: 'employee_user_id', width: 80, render: (v) => `#${v}` },
    { title: '周期', dataIndex: 'cycle_name' },
    { title: '绩效', dataIndex: 'performance_score', width: 70, render: (v) => v ?? '—' },
    { title: '经理潜力', dataIndex: 'potential_manager_score', width: 90, render: (v) => v ?? '—' },
    { title: 'HR 潜力', dataIndex: 'potential_hr_score', width: 88, render: (v) => v ?? '—' },
    {
      title: '九宫格',
      dataIndex: 'grid_cell',
      width: 110,
      render: (v) => (v ? <Tag color="blue">{v}</Tag> : '—'),
    },
    { title: '状态', dataIndex: 'status', width: 100, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_, row) => (
        <Space size={8} wrap>
          {row.status === 'draft' && (
            <a
              onClick={() => {
                setEditTarget(row);
                editForm.setFieldsValue({
                  performance_score: row.performance_score,
                  potential_manager_score: row.potential_manager_score,
                  potential_hr_score: row.potential_hr_score,
                  summary: row.summary,
                });
              }}
            >
              评分
            </a>
          )}
          {row.status === 'draft' && (
            <a
              onClick={async () => {
                try {
                  await submitReview(row.id);
                  message.success('已提交，等待 HR 确认');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              提交
            </a>
          )}
          {row.status === 'submitted' && (
            <a
              onClick={async () => {
                try {
                  await confirmReview(row.id);
                  message.success('已确认生效');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              确认
            </a>
          )}
          {row.status === 'confirmed' && (
            <a
              onClick={() => {
                setIdpTarget(row);
                idpForm.setFieldsValue({ content: '{}', status: 'draft' });
              }}
            >
              发展计划
            </a>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="盘点复核"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建复核
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        九宫格按绩效与 HR 潜力计算（≥4 高、=3 中、&lt;3 低）；缺绩效值不入格。提交后由 HR 确认才生效。
      </Typography.Paragraph>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={false} />
      <Modal title="新建盘点复核" open={createOpen} width={620} onOk={() => void submitCreate()} onCancel={() => setCreateOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ review_type: 'annual', performance_source: 'none' }}>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="employee_user_id" label="员工 user_id" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="talent_profile_id" label="人才档案 ID（可选）" style={{ flex: 1 }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="cycle_name" label="周期名称" style={{ flex: 2 }} rules={[{ required: true }]}>
              <Input placeholder="如：2026 年度" />
            </Form.Item>
            <Form.Item name="review_type" label="类型" style={{ flex: 1 }}>
              <Select options={[{ value: 'annual', label: '年度' }, { value: 'ad_hoc', label: '临时' }]} />
            </Form.Item>
          </Space>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="performance_score" label="绩效（1–5，缺省不入格）" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="potential_hr_score" label="HR 潜力（1–5）" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="potential_manager_score" label="经理潜力（1–5）" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Form.Item name="summary" label="总体评语">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal title="更新评分" open={Boolean(editTarget)} onOk={() => void submitEdit()} onCancel={() => setEditTarget(null)}>
        <Form form={editForm} layout="vertical">
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="performance_score" label="绩效" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="potential_hr_score" label="HR 潜力" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="potential_manager_score" label="经理潜力" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Form.Item name="summary" label="总体评语">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal title="个人发展计划（IDP）" open={Boolean(idpTarget)} onOk={() => void submitIdp()} onCancel={() => setIdpTarget(null)}>
        <Form form={idpForm} layout="vertical">
          <Form.Item name="content" label="内容（JSON）" rules={[{ required: true }]}>
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={[{ value: 'draft', label: '草稿' }, { value: 'confirmed', label: '已确认' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function PoolAdmin() {
  const [rows, setRows] = useState<TalentPoolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchPools());
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    const values = await form.validateFields();
    try {
      await nominatePool(values);
      message.success('已提名');
      setOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<TalentPoolItem> = [
    { title: '员工', dataIndex: 'employee_user_id', width: 80, render: (v) => `#${v}` },
    { title: '池类型', dataIndex: 'pool_type' },
    { title: '状态', dataIndex: 'status', width: 100, render: (v) => <Tag>{v}</Tag> },
    { title: '备注', dataIndex: 'note', render: (v) => v || '—' },
    {
      title: '操作',
      key: 'action',
      width: 170,
      render: (_, row) =>
        row.status === 'nominated' ? (
          <Space size={8}>
            <a
              onClick={async () => {
                try {
                  await decidePool(row.id, { status: 'approved' });
                  message.success('已通过');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              通过
            </a>
            <a
              style={{ color: '#cf1322' }}
              onClick={async () => {
                try {
                  await decidePool(row.id, { status: 'rejected' });
                  message.success('已拒绝');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              拒绝
            </a>
          </Space>
        ) : (
          <a
            style={{ color: '#cf1322' }}
            onClick={async () => {
              try {
                await decidePool(row.id, { status: 'removed' });
                message.success('已移出');
                void load();
              } catch (error) {
                message.error(getErrorMessage(error));
              }
            }}
          >
            移出
          </a>
        ),
    },
  ];

  return (
    <Card
      title="人才池"
      extra={
        <Button icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          提名
        </Button>
      }
    >
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={false} />
      <Modal title="提名人选入池" open={open} onOk={() => void submit()} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="employee_user_id" label="员工 user_id" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="pool_type" label="池类型" rules={[{ required: true }]}>
            <Input placeholder="如：高潜池 / 关键岗位后备" />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function SummaryPanel() {
  const [data, setData] = useState<InventorySummary | null>(null);

  useEffect(() => {
    fetchSummary()
      .then(setData)
      .catch((error) => message.error(getErrorMessage(error)));
  }, []);

  if (!data) return <Card loading />;

  return (
    <Card>
      <Typography.Paragraph type="secondary">
        仅统计已确认复核；单格少于 5 人并入“其他”，永不展示个人身份。
      </Typography.Paragraph>
      <Space size={32} wrap>
        <Statistic title="已确认复核" value={data.total} suffix="人" />
        {Object.entries(data.cells)
          .filter(([cell]) => cell !== '_under_threshold')
          .map(([cell, count]) => (
            <Statistic key={cell} title={data.labels[cell] ?? cell} value={count} suffix="人" />
          ))}
        {data.cells._under_threshold ? (
          <Statistic title="其他（样本 <5 已隐藏）" value={data.cells._under_threshold} suffix="人" />
        ) : null}
      </Space>
      {Object.keys(data.cells).length === 0 && <Typography.Text type="secondary">暂无可展示的汇总数据。</Typography.Text>}
    </Card>
  );
}

export default function InventoryPage() {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const role = getUser()?.role;
    setIsAdmin(role === 'admin' || role === 'hr');
  }, []);

  return (
    <Card title="人才盘点">
      <Tabs
        items={[
          { key: 'mine', label: '我的复核', children: <MyReviews /> },
          ...(isAdmin
            ? [
                { key: 'reviews', label: '盘点复核', children: <ReviewAdmin /> },
                { key: 'pool', label: '人才池', children: <PoolAdmin /> },
                { key: 'summary', label: '部门汇总', children: <SummaryPanel /> },
              ]
            : []),
        ]}
      />
    </Card>
  );
}
