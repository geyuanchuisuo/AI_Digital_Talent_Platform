/**
 * 培训报名审批与有效学习证据：员工报名必修/选修课程并提交学习证据；
 * 管理员审批报名（24h 未审批由后端自动释放）、查看证据并认定正式完成。
 */

'use client';

import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getUser, isManagement } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import {
  adminReviewEnrollment,
  applyEnrollment,
  confirmEnrollment,
  confirmFormalCompletion,
  decideEnrollment,
  fetchCourses,
  fetchEnrollments,
  fetchLearningEvidence,
  fetchMyPlans,
  managerDecideEnrollment,
  releaseExpiredEnrollments,
  sendStudyHeartbeat,
  startLearningSession,
  submitLearningEvidence,
} from '../api';
import type { LearningEvidence, TrainingCourse, TrainingEnrollment } from '../types';

const ENROLL_STATUS: Record<string, { color: string; text: string }> = {
  pending: { color: 'processing', text: '待审批' },
  pending_admin_review: { color: 'gold', text: '待管理员复核' },
  approved: { color: 'success', text: '已通过' },
  rejected: { color: 'error', text: '已拒绝' },
  waitlisted: { color: 'warning', text: '候补' },
  awaiting_confirmation: { color: 'gold', text: '待确认名额' },
  cancelled: { color: 'default', text: '已取消' },
  expired: { color: 'default', text: '超时释放' },
};

function enrollTag(status: string) {
  const item = ENROLL_STATUS[status] ?? { color: 'default', text: status };
  return <Tag color={item.color}>{item.text}</Tag>;
}

const LEARNING_STATUS: Record<string, string> = {
  not_started: '未开始',
  learning: '学习中',
  completed: '已完成',
};

function useCourses() {
  const [courses, setCourses] = useState<TrainingCourse[]>([]);

  useEffect(() => {
    fetchCourses({ status: 'active', page: 1, page_size: 200 })
      .then((data) => setCourses(data.items))
      .catch((error) => message.error(getErrorMessage(error)));
  }, []);

  return courses;
}

function EnrollmentTable({ isAdmin, isManager }: { isAdmin: boolean; isManager: boolean }) {
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

  const confirmSeat = async (row: TrainingEnrollment) => {
    try {
      await confirmEnrollment(row.id);
      message.success('名额已确认，进入审批');
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const managerDecide = async (row: TrainingEnrollment, status: 'approved' | 'rejected') => {
    try {
      const res = await managerDecideEnrollment(row.id, { status });
      message.success(
        status === 'rejected'
          ? '经理已拒绝'
          : res.status === 'pending_admin_review'
            ? '经理已通过，涉费/工时转管理员复核'
            : '经理已通过',
      );
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const adminReview = async (row: TrainingEnrollment, status: 'approved' | 'rejected') => {
    try {
      await adminReviewEnrollment(row.id, { status });
      message.success(status === 'approved' ? '复核通过' : '复核拒绝');
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
            width: 170,
            render: (_: unknown, row: TrainingEnrollment) =>
              row.status === 'pending_admin_review' ? (
                <Space size={8}>
                  <a onClick={() => void adminReview(row, 'approved')}>复核通过</a>
                  <a style={{ color: '#cf1322' }} onClick={() => void adminReview(row, 'rejected')}>
                    复核拒绝
                  </a>
                </Space>
              ) : row.status === 'pending' ? (
                <Space size={8}>
                  <a onClick={() => void decide(row, 'approved')}>通过</a>
                  <a style={{ color: '#cf1322' }} onClick={() => void decide(row, 'rejected')}>
                    拒绝
                  </a>
                </Space>
              ) : row.status === 'waitlisted' ? (
                <a style={{ color: '#cf1322' }} onClick={() => void decide(row, 'cancelled')}>
                  取消排队
                </a>
              ) : row.status === 'approved' ? (
                <a style={{ color: '#cf1322' }} onClick={() => void decide(row, 'cancelled')}>
                  取消并递补
                </a>
              ) : (
                '—'
              ),
          },
        ]
      : isManager
        ? [
            {
              title: '操作',
              key: 'action',
              width: 150,
              render: (_: unknown, row: TrainingEnrollment) =>
                row.status === 'pending' ? (
                  <Space size={8}>
                    <a onClick={() => void managerDecide(row, 'approved')}>经理通过</a>
                    <a style={{ color: '#cf1322' }} onClick={() => void managerDecide(row, 'rejected')}>
                      经理拒绝
                    </a>
                  </Space>
                ) : (
                  '—'
                ),
            },
          ]
        : [
            {
              title: '操作',
              key: 'action',
              width: 120,
              render: (_: unknown, row: TrainingEnrollment) =>
                row.status === 'awaiting_confirmation' ? (
                  <a onClick={() => void confirmSeat(row)}>确认名额</a>
                ) : (
                  '—'
                ),
            },
          ]),
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
          ? '待审批报名需在截止时间内处理；经理已通过且涉费/工时的报名进入「待管理员复核」，由管理员复核。候补报名须先递补并由员工本人确认后才能审批，不可直接通过。超时未审批可点「释放超时占位」自动释放并递补候补。'
          : isManager
            ? '仅显示本部门员工的报名。manager 模式课程由直属经理本人审批，管理员不可越过经理环节；涉费/工时报名经理通过后转管理员复核。候补递补须员工本人确认，经理不可代确认。'
            : '报名后按课程审批模式等待审批；名额满时进入候补，收到递补通知后需在 24 小时内本人确认，确认后仍走审批规则，超时未确认名额将释放给下一位。'}
      </Typography.Paragraph>
      <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows} pagination={false} />
    </Card>
  );
}

function EmployeeApply() {
  const courses = useCourses();
  const [form] = Form.useForm();

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
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
  const [recordOptions, setRecordOptions] = useState<{ value: number; label: string }[]>([]);
  const [session, setSession] = useState<{ recordId: number; token: string } | null>(null);
  const [heartbeatCount, setHeartbeatCount] = useState(0);
  const [activeMinutes, setActiveMinutes] = useState(0);
  const [pauseReason, setPauseReason] = useState<'hidden' | 'idle' | null>(null);
  const heartbeatTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastInteractionRef = useRef(0);
  const pausedRef = useRef(false);
  const idleMsRef = useRef(90_000);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatTimer.current) {
      clearInterval(heartbeatTimer.current);
      heartbeatTimer.current = null;
    }
  }, []);

  const beat = useCallback(
    async (id: number, token: string) => {
      try {
        const res = await sendStudyHeartbeat(id, token);
        setHeartbeatCount(res.heartbeat_count);
        setActiveMinutes(res.active_minutes);
      } catch (error) {
        message.error(getErrorMessage(error));
        stopHeartbeat();
        setSession(null);
      }
    },
    [stopHeartbeat],
  );

  // C08：心跳仅在页面可见且近期有真实学习交互时发送；隐藏/空闲即暂停并明示，
  // 恢复交互时立即重发心跳重新确认会话。定时器本身不构成学习证据。
  useEffect(() => {
    if (!session) return;
    const idleMs = idleMsRef.current;
    const setPaused = (reason: 'hidden' | 'idle' | null) => {
      pausedRef.current = reason !== null;
      setPauseReason(reason);
    };
    const markInteraction = () => {
      lastInteractionRef.current = Date.now();
      if (pausedRef.current && document.visibilityState === 'visible') {
        setPaused(null);
        void beat(session.recordId, session.token);
      }
    };
    const onVisibility = () => {
      if (document.visibilityState !== 'visible') setPaused('hidden');
      // 回到前台不自动恢复计时：等待真实学习交互重新确认
    };
    const tick = () => {
      if (document.visibilityState !== 'visible') {
        setPaused('hidden');
        return;
      }
      if (Date.now() - lastInteractionRef.current > idleMs) {
        setPaused('idle');
        return;
      }
      setPaused(null);
      void beat(session.recordId, session.token);
    };
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll'] as const;
    events.forEach((e) => window.addEventListener(e, markInteraction, { passive: true }));
    document.addEventListener('visibilitychange', onVisibility);
    heartbeatTimer.current = setInterval(tick, idleMs);
    return () => {
      events.forEach((e) => window.removeEventListener(e, markInteraction));
      document.removeEventListener('visibilitychange', onVisibility);
      stopHeartbeat();
    };
  }, [session, beat, stopHeartbeat]);

  useEffect(() => {
    if (isAdmin) return;
    fetchMyPlans()
      .then((data) => {
        const options = data.items.flatMap((plan) =>
          (plan.items ?? [])
            .filter((item) => item.learning.record_id)
            .map((item) => ({
              value: item.learning.record_id as number,
              label: `#${item.learning.record_id} ${item.course_snapshot.title}（${
                LEARNING_STATUS[item.learning.status] ?? item.learning.status
              }${item.learning.formal_completed ? '·已认定' : ''}）`,
            })),
        );
        setRecordOptions(options);
      })
      .catch((error) => message.error(getErrorMessage(error)));
  }, [isAdmin]);

  const recordIdValue = (): number | null => {
    const raw = form.getFieldValue('learning_record_id') as string | number | undefined;
    const num = Number(raw);
    return Number.isInteger(num) && num > 0 ? num : null;
  };

  const load = async (id: number) => {
    try {
      setRows(await fetchLearningEvidence(id));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
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
      await submitLearningEvidence({
        ...values,
        learning_record_id: Number(values.learning_record_id),
        payload,
      });
      message.success('证据已提交');
      form.resetFields(['evidence_type', 'payload']);
      if (recordId) void load(recordId);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const startSession = async () => {
    const id = recordIdValue();
    if (!id) {
      message.warning('请先选择或输入学习记录');
      return;
    }
    try {
      const res = await startLearningSession(id);
      form.setFieldValue('evidence_type', 'study_session');
      form.setFieldValue('payload', JSON.stringify({ session_token: res.session_token }));
      stopHeartbeat();
      idleMsRef.current = Math.max(1, res.heartbeat_interval_seconds) * 1000;
      lastInteractionRef.current = Date.now();
      pausedRef.current = false;
      setPauseReason(null);
      setSession({ recordId: id, token: res.session_token });
      setHeartbeatCount(0);
      setActiveMinutes(0);
      message.success(
        `已领取学习会话凭证，${res.expires_at} 前结算；仅页面可见且有学习操作时累计学时，需达到最少心跳次数与有效学时要求（挂机等待不计学时）`,
      );
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const stopSession = () => {
    stopHeartbeat();
    setSession(null);
    pausedRef.current = false;
    setPauseReason(null);
    message.info('已停止学习会话心跳，可提交证据结算');
  };

  const confirm = async () => {
    const id = recordIdValue();
    if (!id) {
      message.warning('请先选择或输入学习记录');
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
        message="正式完成必须有服务端校验的学习证据：出勤/已通过的员工测评/有效学习会话；分数与验证人由后端计算，前端不能自报。无证据或未达阈值时后端拒绝认定。"
      />
      <Form form={form} layout="vertical">
        <Space size={8} wrap>
          <Form.Item
            name="learning_record_id"
            label="学习记录"
            rules={[
              { required: true, message: '请选择或输入学习记录' },
              {
                validator: (_, value) =>
                  value === undefined || value === null || value === ''
                    ? Promise.resolve()
                    : Number.isInteger(Number(value)) && Number(value) > 0
                      ? Promise.resolve()
                      : Promise.reject(new Error('学习记录 ID 需为正整数')),
              },
            ]}
          >
            <AutoComplete
              style={{ width: 300 }}
              options={recordOptions}
              placeholder={isAdmin ? '输入学习记录 ID' : '选择我的学习记录（也可输入 ID）'}
              filterOption={(input, option) =>
                String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
              onChange={(v) => {
                const num = Number(v);
                setRecordId(Number.isInteger(num) && num > 0 ? num : null);
              }}
            />
          </Form.Item>
          <Form.Item name="evidence_type" label="证据类型" rules={[{ required: true }]}>
            <Select
              style={{ width: 180 }}
              options={[
                { value: 'attendance', label: '线下出勤' },
                { value: 'assessment', label: '已通过测评' },
                { value: 'study_session', label: '学习会话' },
              ]}
            />
          </Form.Item>
          <Button onClick={() => void startSession()} disabled={Boolean(session)}>
            领取学习会话凭证
          </Button>
          {session && (
            <>
              <Button onClick={() => void beat(session.recordId, session.token)}>立即发送心跳</Button>
              <Button danger onClick={stopSession}>
                停止会话
              </Button>
            </>
          )}
        </Space>
        {session && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            {pauseReason
              ? `学习计时已暂停（${pauseReason === 'hidden' ? '页面切到后台' : '长时间无学习操作'}）：暂停期间不计学时，回到页面并继续操作后自动恢复。`
              : `学习会话进行中：已上报心跳 ${heartbeatCount} 次，累计有效学时 ${activeMinutes.toFixed(1)} 分钟。`}
            需达到最少心跳次数与有效学时要求方可结算；仅页面可见且有学习操作时累计，挂机/后台不计学时。
          </Typography.Paragraph>
        )}
        <Form.Item name="payload" label="证据内容（仅学习会话需填写 session_token）">
          <Input.TextArea rows={2} placeholder='{"session_token": "…"}' />
        </Form.Item>
        <Space>
          <Button type="primary" onClick={() => void submit()}>
            提交证据
          </Button>
          {isAdmin && <Button onClick={() => void confirm()}>认定正式完成</Button>}
          <Button
            onClick={() => {
              const id = recordIdValue();
              if (!id) {
                message.warning('请先选择或输入学习记录');
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
  const [isManager, setIsManager] = useState(false);

  useEffect(() => {
    setIsAdmin(isManagement());
    setIsManager(getUser()?.role === 'dept_manager');
  }, []);

  const listLabel = isAdmin ? '报名审批' : isManager ? '部门报名审批' : '我的报名';

  return (
    <Card title="培训报名与学习证据">
      <Tabs
        items={[
          ...(isAdmin || isManager ? [] : [{ key: 'apply', label: '课程报名', children: <EmployeeApply /> }]),
          {
            key: 'list',
            label: listLabel,
            children: <EnrollmentTable isAdmin={isAdmin} isManager={isManager} />,
          },
          { key: 'evidence', label: '学习证据', children: <EvidencePanel isAdmin={isAdmin} /> },
        ]}
      />
    </Card>
  );
}
