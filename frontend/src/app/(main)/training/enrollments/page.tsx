/**
 * 培训报名审批与有效学习证据：员工报名必修/选修课程并提交学习证据；
 * 管理员审批报名（24h 未审批由后端自动释放）、查看证据并认定正式完成。
 */

'use client';

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
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
  applyEnrollment,
  confirmFormalCompletion,
  decideEnrollment,
  fetchCourses,
  fetchEnrollments,
  fetchLearningEvidence,
  releaseExpiredEnrollments,
  submitLearningEvidence,
} from '../api';
import type { LearningEvidence, TrainingCourse, TrainingEnrollment } from '../types';

const ENROLL_STATUS: Record<string, { color: string; text: string }> = {
  pending: { color: 'processing', text: '待审批' },
  approved: { color: 'success', text: '已通过' },
  rejected: { color: 'error', text: '已拒绝' },
  waitlisted: { color: 'warning', text: '候补' },
  cancelled: { color: 'default', text: '已取消' },
  expired: { color: 'default', text: '超时释放' },
};

function enrollTag(status: string) {
  const item = ENROLL_STATUS[status] ?? { color: 'default', text: status };
  return <Tag color={item.color}>{item.text}</Tag>;
}

function useCourses() {
  const [courses, setCourses] = useState<TrainingCourse[]>([]);

  useEffect(() => {
    fetchCourses({ status: 'active', page: 1, page_size: 200 })
      .then((data) => setCourses(data.items))
      .catch((error) => message.error(getErrorMessage(error)));
  }, []);

  return courses;
}

function EnrollmentTable({ isAdmin }: { isAdmin: boolean }) {
  const [rows, setRows] = useState<TrainingEnrollment[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchEnrollments());
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (row: TrainingEnrollment, status: 'approved' | 'rejected' | 'cancelled') => {
    try {
      await decideEnrollment(row.id, { status });
      message.success(status === 'approved' ? '已通过' : status === 'rejected' ? '已拒绝' : '已取消');
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const releaseExpired = async () => {
    try {
      const res = await releaseExpiredEnrollments();
      message.success(`已释放 ${res.released} 条超时报名，递补 ${res.promoted} 条候补`);
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<TrainingEnrollment> = [
    { title: '报名 ID', dataIndex: 'id', width: 80 },
    { title: '课程 ID', dataIndex: 'course_id', width: 90 },
    { title: '员工', dataIndex: 'employee_user_id', width: 80, render: (v) => `#${v}` },
    { title: '类型', dataIndex: 'priority', width: 80, render: (v) => (v === 'required' ? '必修' : '选修') },
    { title: '状态', dataIndex: 'status', width: 100, render: enrollTag },
    {
      title: '审批截止',
      dataIndex: 'expires_at',
      render: (v) => (v ? new Date(v).toLocaleString() : '—'),
    },
    ...(isAdmin
      ? [
          {
            title: '操作',
            key: 'action',
            width: 150,
            render: (_: unknown, row: TrainingEnrollment) =>
              row.status === 'pending' || row.status === 'waitlisted' ? (
                <Space size={8}>
                  <a onClick={() => void decide(row, 'approved')}>通过</a>
                  <a style={{ color: '#cf1322' }} onClick={() => void decide(row, 'rejected')}>
                    拒绝
                  </a>
                </Space>
              ) : row.status === 'approved' ? (
                <a style={{ color: '#cf1322' }} onClick={() => void decide(row, 'cancelled')}>
                  取消并递补
                </a>
              ) : (
                '—'
              ),
          },
        ]
      : []),
  ];

  return (
    <Card
      extra={
        isAdmin ? (
          <Button onClick={() => void releaseExpired()}>释放超时占位</Button>
        ) : null
      }
    >
      <Typography.Paragraph type="secondary">
        {isAdmin
          ? '待审批报名需在截止时间内处理，超时未审批可点「释放超时占位」自动释放并递补候补。'
          : '报名后等待管理员审批；超时未审批会自动取消，可重新报名。'}
      </Typography.Paragraph>
      <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows} pagination={false} />
    </Card>
  );
}

function EmployeeApply() {
  const courses = useCourses();
  const [form] = Form.useForm();

  const submit = async () => {
    const values = await form.validateFields();
    try {
      const res = await applyEnrollment(values);
      message.success(`报名已提交（${ENROLL_STATUS[res.status]?.text ?? res.status}）`);
      form.resetFields();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  return (
    <Card title="课程报名">
      <Form form={form} layout="inline" onFinish={() => void submit()} initialValues={{ priority: 'elective' }}>
        <Form.Item name="course_id" rules={[{ required: true }]} style={{ minWidth: 320 }}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="选择课程"
            options={courses.map((c) => ({
              value: c.id,
              label: `#${c.id} ${c.title}${c.capacity ? `（名额 ${c.capacity}）` : ''}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="priority">
          <Select options={[{ value: 'required', label: '必修' }, { value: 'elective', label: '选修' }]} />
        </Form.Item>
        <Button type="primary" htmlType="submit">
          报名
        </Button>
      </Form>
    </Card>
  );
}

function EvidencePanel({ isAdmin }: { isAdmin: boolean }) {
  const [form] = Form.useForm();
  const [recordId, setRecordId] = useState<number | null>(null);
  const [rows, setRows] = useState<LearningEvidence[]>([]);

  const load = async (id: number) => {
    try {
      setRows(await fetchLearningEvidence(id));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    let payload: Record<string, unknown> = {};
    if (values.payload) {
      try {
        payload = JSON.parse(values.payload);
      } catch {
        message.error('证据内容需为合法 JSON');
        return;
      }
    }
    try {
      await submitLearningEvidence({ ...values, payload });
      message.success('证据已提交');
      form.resetFields(['evidence_type', 'payload', 'score']);
      if (recordId) void load(recordId);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const confirm = async () => {
    const id = form.getFieldValue('learning_record_id') as number | undefined;
    if (!id) {
      message.warning('请先填写学习记录 ID');
      return;
    }
    try {
      const res = await confirmFormalCompletion(id);
      message.success(res.formal_completed ? '已认定为正式完成' : '未通过（缺少有效证据）');
      setRecordId(id);
      void load(id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  return (
    <Card title="有效学习证据与正式完成">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="正式完成必须有有效证据，无证据时后端拒绝认定；个人手动勾选仅算自报，不当作学时证明。"
      />
      <Form form={form} layout="vertical">
        <Space size={8} wrap>
          <Form.Item name="learning_record_id" label="学习记录 ID" rules={[{ required: true }]}>
            <InputNumber
              min={1}
              onChange={(v) => setRecordId(typeof v === 'number' ? v : null)}
            />
          </Form.Item>
          <Form.Item name="evidence_type" label="证据类型" rules={[{ required: true }]}>
            <Select
              style={{ width: 150 }}
              options={[
                { value: 'video_heartbeat', label: '视频心跳' },
                { value: 'quiz', label: '测验成绩' },
                { value: 'attendance', label: '出勤' },
              ]}
            />
          </Form.Item>
          <Form.Item name="score" label="得分（0–100）" initialValue={0}>
            <InputNumber min={0} max={100} />
          </Form.Item>
        </Space>
        <Form.Item name="payload" label="证据内容（JSON，可选）">
          <Input.TextArea rows={2} placeholder='{"quiz_score": 85}' />
        </Form.Item>
        <Space>
          <Button type="primary" onClick={() => void submit()}>
            提交证据
          </Button>
          {isAdmin && <Button onClick={() => void confirm()}>认定正式完成</Button>}
          <Button
            onClick={() => {
              const id = form.getFieldValue('learning_record_id') as number | undefined;
              if (!id) {
                message.warning('请先填写学习记录 ID');
                return;
              }
              setRecordId(id);
              void load(id);
            }}
          >
            查看证据
          </Button>
        </Space>
      </Form>
      <Table
        rowKey="id"
        size="small"
        style={{ marginTop: 16 }}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: recordId ? '该记录暂无证据' : '填写记录 ID 后查看' }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          { title: '类型', dataIndex: 'evidence_type' },
          { title: '得分', dataIndex: 'score', width: 80 },
          { title: '核验人', dataIndex: 'verified_by', render: (v) => v || '—' },
          {
            title: '内容',
            dataIndex: 'payload',
            render: (v) => <Typography.Text type="secondary">{JSON.stringify(v)}</Typography.Text>,
          },
        ]}
      />
    </Card>
  );
}

export default function TrainingEnrollmentsPage() {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const role = getUser()?.role;
    setIsAdmin(role === 'admin' || role === 'hr');
  }, []);

  return (
    <Card title="培训报名与学习证据">
      <Tabs
        items={[
          ...(isAdmin ? [] : [{ key: 'apply', label: '课程报名', children: <EmployeeApply /> }]),
          { key: 'list', label: isAdmin ? '报名审批' : '我的报名', children: <EnrollmentTable isAdmin={isAdmin} /> },
          { key: 'evidence', label: '学习证据', children: <EvidencePanel isAdmin={isAdmin} /> },
        ]}
      />
    </Card>
  );
}
