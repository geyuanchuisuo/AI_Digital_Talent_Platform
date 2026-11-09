/**
 * C 培训成长页：员工查看本人已分配计划、自报学习状态与成长记录；
 * 管理员维护真实课程、手工/AI 草稿计划、确认分配与修订版本。
 * 培训仅面向员工；完成状态为员工自报，不代表能力评级变化。
 */

'use client';

import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Rate,
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
import type { Dayjs } from 'dayjs';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getUser } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import {
  assignPlan,
  createCourse,
  createPlan,
  createSession,
  draftPlan,
  extendPlanItem,
  fetchAttendance,
  fetchCourses,
  fetchFeedbackSummary,
  fetchGrowth,
  fetchMyPlans,
  fetchPlans,
  fetchSessions,
  getPlan,
  markAttendance,
  refreshFeedbackThemes,
  revisePlan,
  submitFeedback,
  updateCourse,
  updateFeedback,
  updateFeedbackNote,
  updateProgress,
  withdrawFeedback,
} from './api';
import type {
  GrowthEvent,
  TrainingAttendance,
  TrainingCourse,
  TrainingFeedbackSummary,
  TrainingPlan,
  TrainingPlanItem,
  TrainingSession,
} from './types';

const PAGE_SIZE = 10;

const STATUS_LABEL = { active: '有效', inactive: '已下线' } as const;
const LEARNING_LABEL = { not_started: '未开始', learning: '学习中', completed: '已完成' } as const;
const LEARNING_COLOR = { not_started: 'default', learning: 'processing', completed: 'success' } as const;

function courseStatusTag(status: string) {
  return status === 'active' ? <Tag color="success">有效</Tag> : <Tag>已下线</Tag>;
}

function learningStatusTag(status: string) {
  return (
    <Tag color={LEARNING_COLOR[status as keyof typeof LEARNING_COLOR] ?? 'default'}>
      {LEARNING_LABEL[status as keyof typeof LEARNING_LABEL] ?? status}
    </Tag>
  );
}

/** 员工视角：我的培训计划 + 学习状态自报 + 成长记录 */
function MyTraining() {
  const [plans, setPlans] = useState<TrainingPlan[]>([]);
  const [growth, setGrowth] = useState<GrowthEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [growthLoading, setGrowthLoading] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackItem, setFeedbackItem] = useState<TrainingPlanItem | null>(null);
  const [feedbackForm] = Form.useForm();

  const loadPlans = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchMyPlans();
      setPlans(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadGrowth = useCallback(async () => {
    setGrowthLoading(true);
    try {
      const data = await fetchGrowth({ page: 1, page_size: 20 });
      setGrowth(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setGrowthLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPlans();
    void loadGrowth();
  }, [loadPlans, loadGrowth]);

  const report = async (item: TrainingPlanItem, status: string) => {
    try {
      await updateProgress(item.id, { status });
      message.success(`已更新为${LEARNING_LABEL[status as keyof typeof LEARNING_LABEL] ?? status}`);
      await loadPlans();
      await loadGrowth();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const openFeedback = (item: TrainingPlanItem) => {
    setFeedbackItem(item);
    feedbackForm.setFieldsValue({
      content_score: item.feedback?.feedback?.content_score ?? 5,
      instructor_score: item.feedback?.feedback?.instructor_score ?? 5,
      comment: item.feedback?.feedback?.comment ?? '',
    });
    setFeedbackOpen(true);
  };

  const saveFeedback = async () => {
    if (!feedbackItem) return;
    const values = await feedbackForm.validateFields();
    try {
      const payload = {
        content_score: values.content_score,
        instructor_score: values.instructor_score,
        comment: (values.comment ?? '').trim() || null,
      };
      if (feedbackItem.feedback?.feedback) {
        await updateFeedback(feedbackItem.course_id, payload);
      } else {
        await submitFeedback(feedbackItem.course_id, payload);
      }
      message.success('评价已保存');
      setFeedbackOpen(false);
      await loadPlans();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const removeFeedback = async () => {
    if (!feedbackItem) return;
    try {
      await withdrawFeedback(feedbackItem.course_id);
      message.success('评价已撤回，不再计入汇总');
      setFeedbackOpen(false);
      await loadPlans();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const planColumns: ColumnsType<TrainingPlan> = [
    {
      title: '目标岗位',
      dataIndex: 'target_job_title',
      render: (v) => v ?? '—',
    },
    { title: '来源', dataIndex: 'source', width: 80, render: (v) => (v === 'ai' ? 'AI 草稿' : '手工') },
    { title: '版本', dataIndex: 'version', width: 70, render: (v) => `v${v}` },
    {
      title: '进度',
      key: 'progress',
      width: 170,
      render: (_, row) => {
        const items = row.items ?? [];
        const done = items.filter((item) => item.learning.status === 'completed').length;
        return `${done}/${items.length} 已完成`;
      },
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
          查看课程
        </a>
      ),
    },
  ];

  const activePlan = plans.find((plan) => plan.id === active) ?? null;

  const itemColumns: ColumnsType<TrainingPlanItem> = [
    {
      title: '课程',
      key: 'course',
      render: (_, row) =>
        row.course_snapshot?.material_url ? (
          <a href={row.course_snapshot.material_url} target="_blank" rel="noreferrer">
            {row.course_snapshot.title || `课程 #${row.course_id}`}
          </a>
        ) : (
          row.course_snapshot?.title || `课程 #${row.course_id}`
        ),
    },
    { title: '对应维度', dataIndex: 'dimension_name', width: 120, render: (v) => v ?? '—' },
    { title: '推荐理由', dataIndex: 'reason', render: (v) => v ?? '—' },
    { title: '到期日', dataIndex: 'due_date', width: 120, render: (v) => v ?? '—' },
    {
      title: '学习状态',
      key: 'learning',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          <Space size={4} wrap>
            {learningStatusTag(row.learning.status)}
            {row.learning.formal_completed ? <Tag color="gold">已认定</Tag> : null}
          </Space>
          {row.learning.record_id ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              记录 #{row.learning.record_id}
            </Typography.Text>
          ) : null}
          <Select
            size="small"
            value={row.learning.status}
            style={{ width: 110 }}
            options={[
              { value: 'not_started', label: '未开始' },
              { value: 'learning', label: '学习中' },
              { value: 'completed', label: '已完成' },
            ]}
            onChange={(value) => void report(row, value)}
          />
        </Space>
      ),
    },
    {
      title: '课程评价',
      key: 'feedback',
      width: 130,
      render: (_, row) => {
        const feedback = row.feedback;
        if (!feedback?.attended && !feedback?.feedback) {
          return <Typography.Text type="secondary">未参加</Typography.Text>;
        }
        const submitted = feedback?.feedback?.status === 'submitted';
        return (
          <Space direction="vertical" size={4}>
            {feedback?.feedback ? (
              <Tag color={submitted ? 'success' : 'default'}>{submitted ? '已评价' : '已撤回'}</Tag>
            ) : null}
            <a onClick={() => openFeedback(row)}>{feedback?.eligible ? (submitted ? '修改评价' : '评价课程') : '查看评价'}</a>
          </Space>
        );
      },
    },
  ];

  const growthColumns: ColumnsType<GrowthEvent> = [
    { title: '时间', dataIndex: 'created_at', width: 170 },
    { title: '类型', dataIndex: 'event_type', width: 150 },
    { title: '说明', dataIndex: 'summary', render: (v) => v ?? '—' },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="我的培训计划"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void loadPlans()}>
            刷新
          </Button>
        }
      >
        <Table rowKey="id" loading={loading} columns={planColumns} dataSource={plans} pagination={false} />
      </Card>

      <Card title="我的成长记录">
        <Table
          rowKey="id"
          loading={growthLoading}
          columns={growthColumns}
          dataSource={growth}
          pagination={false}
          locale={{ emptyText: '暂无成长记录' }}
        />
      </Card>

      <Drawer
        title={activePlan ? `培训计划 v${activePlan.version}` : '培训计划'}
        width={720}
        open={open}
        onClose={() => setOpen(false)}
      >
        {!activePlan ? (
          <Empty />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="目标岗位">{activePlan.target_job_title || '—'}</Descriptions.Item>
              <Descriptions.Item label="来源">{activePlan.source === 'ai' ? 'AI 草稿' : '手工'}</Descriptions.Item>
              <Descriptions.Item label="差距说明" span={2}>
                {activePlan.gap_summary || '—'}
              </Descriptions.Item>
            </Descriptions>
            <Table rowKey="id" columns={itemColumns} dataSource={activePlan.items ?? []} pagination={false} />
            <Typography.Text type="secondary">
              学习状态由本人自报，用于记录进度；完成课程不等同于能力评级变化。
            </Typography.Text>
          </Space>
        )}
      </Drawer>

      <Modal
        title={feedbackItem ? `课程评价 · ${feedbackItem.course_snapshot?.title || `#${feedbackItem.course_id}`}` : '课程评价'}
        width={560}
        open={feedbackOpen}
        footer={null}
        destroyOnClose
        onCancel={() => setFeedbackOpen(false)}
      >
        {!feedbackItem ? (
          <Empty />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {feedbackItem.feedback?.reason ? (
              <Typography.Text type="secondary">{feedbackItem.feedback.reason}</Typography.Text>
            ) : null}
            {feedbackItem.feedback?.window_closes_at ? (
              <Typography.Text type="secondary">
                反馈截止：{feedbackItem.feedback.window_closes_at}（提交后 7 天内可修改或撤回）
              </Typography.Text>
            ) : null}
            {feedbackItem.feedback?.improvement_note ? (
              <Card size="small" title="培训统一改进说明">
                {feedbackItem.feedback.improvement_note}
              </Card>
            ) : null}
            <Form form={feedbackForm} layout="vertical" disabled={!feedbackItem.feedback?.eligible}>
              <Form.Item name="content_score" label="课程内容满意度" rules={[{ required: true }]}>
                <Rate count={5} />
              </Form.Item>
              <Form.Item name="instructor_score" label="讲师满意度" rules={[{ required: true }]}>
                <Rate count={5} />
              </Form.Item>
              <Form.Item name="comment" label="文字意见（匿名，不对外展示原文）" extra="最多 1000 字；仅用于主题归纳">
                <Input.TextArea rows={3} maxLength={1000} showCount />
              </Form.Item>
            </Form>
            <Space>
              {feedbackItem.feedback?.eligible ? (
                <>
                  <Button type="primary" onClick={() => void saveFeedback()}>
                    保存评价
                  </Button>
                  {feedbackItem.feedback?.feedback?.status === 'submitted' ? (
                    <Button danger onClick={() => void removeFeedback()}>
                      撤回评价
                    </Button>
                  ) : null}
                </>
              ) : (
                <Button onClick={() => setFeedbackOpen(false)}>关闭</Button>
              )}
            </Space>
          </Space>
        )}
      </Modal>
    </Space>
  );
}

/** 管理员：课程期次与实际出勤登记 */
function SessionManager({ course, open, onClose }: { course: TrainingCourse | null; open: boolean; onClose: () => void }) {
  const [sessions, setSessions] = useState<TrainingSession[]>([]);
  const [selected, setSelected] = useState<TrainingSession | null>(null);
  const [attendance, setAttendance] = useState<TrainingAttendance[]>([]);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();
  const [attendanceForm] = Form.useForm();

  const loadSessions = useCallback(async (courseId: number) => {
    setLoading(true);
    try {
      const data = await fetchSessions(courseId);
      setSessions(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAttendance = useCallback(async (sessionId: number) => {
    try {
      const data = await fetchAttendance(sessionId);
      setAttendance(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, []);

  useEffect(() => {
    if (!open || !course) return;
    form.resetFields();
    setSelected(null);
    setAttendance([]);
    void loadSessions(course.id);
  }, [open, course, form, loadSessions]);

  const createOne = async () => {
    if (!course) return;
    const values = await form.validateFields();
    try {
      await createSession(course.id, {
        instructor_name: values.instructor_name ?? null,
        starts_at: values.starts_at,
        ends_at: values.ends_at,
      });
      message.success('期次已创建');
      form.resetFields();
      await loadSessions(course.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const choose = async (session: TrainingSession) => {
    setSelected(session);
    await loadAttendance(session.id);
  };

  const addAttendance = async () => {
    if (!selected) return;
    const values = await attendanceForm.validateFields();
    try {
      await markAttendance(selected.id, values.employee_user_id);
      message.success('出勤已登记');
      attendanceForm.resetFields();
      await loadAttendance(selected.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const sessionColumns: ColumnsType<TrainingSession> = [
    { title: '讲师', dataIndex: 'instructor_name', render: (v) => v ?? '—' },
    { title: '开始', dataIndex: 'starts_at', width: 170 },
    { title: '结束', dataIndex: 'ends_at', width: 170 },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, row) => <a onClick={() => void choose(row)}>管理出勤</a>,
    },
  ];

  const attendanceColumns: ColumnsType<TrainingAttendance> = [
    { title: '员工 user_id', dataIndex: 'employee_user_id', render: (v) => `#${v}` },
    { title: '登记时间', dataIndex: 'attended_at' },
  ];

  return (
    <Drawer
      title={course ? `期次与出勤 · ${course.title}` : '期次与出勤'}
      width={760}
      open={open}
      onClose={onClose}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small" title="新建期次">
          <Form form={form} layout="inline">
            <Form.Item name="instructor_name" label="讲师">
              <Input placeholder="可选" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="starts_at" label="开始" rules={[{ required: true, message: '请填开始时间' }]}>
              <Input placeholder="2026-11-06T09:00" style={{ width: 170 }} />
            </Form.Item>
            <Form.Item name="ends_at" label="结束" rules={[{ required: true, message: '请填结束时间' }]}>
              <Input placeholder="2026-11-06T17:00" style={{ width: 170 }} />
            </Form.Item>
            <Button type="primary" onClick={() => void createOne()}>
              创建
            </Button>
          </Form>
          <Typography.Text type="secondary">时间格式 YYYY-MM-DDTHH:mm；反馈窗口从结束时间起 7 天。</Typography.Text>
        </Card>

        <Table rowKey="id" loading={loading} columns={sessionColumns} dataSource={sessions} pagination={false} />

        {selected ? (
          <Card size="small" title={`出勤名单 · 期次 #${selected.id}`}>
            <Form form={attendanceForm} layout="inline" style={{ marginBottom: 12 }}>
              <Form.Item name="employee_user_id" rules={[{ required: true, message: '请填员工 user_id' }]}>
                <InputNumber min={1} placeholder="员工 user_id" />
              </Form.Item>
              <Button type="primary" onClick={() => void addAttendance()}>
                登记出勤
              </Button>
            </Form>
            <Table rowKey="id" columns={attendanceColumns} dataSource={attendance} pagination={false} />
          </Card>
        ) : null}
      </Space>
    </Drawer>
  );
}

/** 管理员：匿名反馈汇总、主题归纳与统一改进说明 */
function CourseFeedback({ course, open, onClose }: { course: TrainingCourse | null; open: boolean; onClose: () => void }) {
  const [summary, setSummary] = useState<TrainingFeedbackSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [noteForm] = Form.useForm();

  const load = useCallback(async (courseId: number) => {
    setLoading(true);
    try {
      const data = await fetchFeedbackSummary(courseId);
      setSummary(data);
      noteForm.setFieldsValue({ note: data.improvement_note ?? '' });
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [noteForm]);

  useEffect(() => {
    if (!open || !course) return;
    setSummary(null);
    void load(course.id);
  }, [open, course, load]);

  const refreshThemes = async () => {
    if (!course) return;
    try {
      const data = await refreshFeedbackThemes(course.id);
      if (!data.generated) message.warning(data.reason || '暂无可归纳文字意见');
      else message.success(`已归纳 ${data.theme_count} 个达到 5 份支持的主题`);
      await load(course.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const publishNote = async () => {
    if (!course) return;
    const values = await noteForm.validateFields();
    try {
      await updateFeedbackNote(course.id, values.note);
      message.success('改进说明已发布');
      await load(course.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  return (
    <Drawer
      title={course ? `匿名反馈汇总 · ${course.title}` : '匿名反馈汇总'}
      width={680}
      open={open}
      onClose={onClose}
    >
      {!summary ? (
        <Empty description={loading ? '加载中' : '暂无数据'} />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {summary.enough_samples ? (
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="有效反馈">{summary.feedback_count} 份</Descriptions.Item>
              <Descriptions.Item label="课程均分">{summary.content_avg ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="讲师均分">{summary.instructor_avg ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="主题数量">{summary.themes.length}</Descriptions.Item>
            </Descriptions>
          ) : (
            <Typography.Text type="secondary">
              有效反馈 {summary.feedback_count} 份，少于 5 份不展示均分与主题。
            </Typography.Text>
          )}
          <Card
            size="small"
            title="文字意见主题（每个主题至少 5 份支持）"
            extra={
              <Button size="small" onClick={() => void refreshThemes()}>
                重新归纳
              </Button>
            }
          >
            {summary.themes.length ? (
              <Space wrap>
                {summary.themes.map((item) => (
                  <Tag key={item.theme} color="blue">
                    {item.theme} · {item.count} 份
                  </Tag>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">暂无达到门槛的主题；不展示原始文字意见。</Typography.Text>
            )}
          </Card>
          <Card size="small" title="统一改进说明">
            <Form form={noteForm} layout="vertical">
              <Form.Item name="note" rules={[{ required: true, max: 2000, message: '请填写改进说明' }]}>
                <Input.TextArea rows={3} maxLength={2000} showCount placeholder="依据匿名汇总统一发布，不针对个人" />
              </Form.Item>
            </Form>
            <Button type="primary" onClick={() => void publishNote()}>
              发布说明
            </Button>
            {summary.improvement_note_at ? (
              <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                上次发布：{summary.improvement_note_at}
              </Typography.Text>
            ) : null}
          </Card>
        </Space>
      )}
    </Drawer>
  );
}

/** 管理员视角：课程目录与培训计划 */
function CourseAdmin() {
  const [rows, setRows] = useState<TrainingCourse[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TrainingCourse | null>(null);
  const [sessionCourse, setSessionCourse] = useState<TrainingCourse | null>(null);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [feedbackCourse, setFeedbackCourse] = useState<TrainingCourse | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCourses({ page, page_size: PAGE_SIZE, keyword: keyword || undefined });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [page, keyword]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ status: 'active', dimension_names: [] });
    setModalOpen(true);
  };

  const openEdit = (row: TrainingCourse) => {
    setEditing(row);
    form.setFieldsValue({
      title: row.title,
      description: row.description,
      material_url: row.material_url,
      dimension_names: row.dimension_names,
      status: row.status,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await updateCourse(editing.id, values);
        message.success('课程已更新');
      } else {
        await createCourse(values);
        message.success('课程已创建');
      }
      setModalOpen(false);
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const toggleStatus = async (row: TrainingCourse) => {
    const next = row.status === 'active' ? 'inactive' : 'active';
    try {
      await updateCourse(row.id, { status: next });
      message.success(next === 'active' ? '课程已上线' : '课程已下线');
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<TrainingCourse> = [
    { title: '课程', dataIndex: 'title' },
    {
      title: '材料链接',
      dataIndex: 'material_url',
      render: (v) => (
        <a href={v} target="_blank" rel="noreferrer">
          打开
        </a>
      ),
    },
    {
      title: '对应维度',
      dataIndex: 'dimension_names',
      render: (v: string[]) => (v?.length ? v.map((name) => <Tag key={name}>{name}</Tag>) : '—'),
    },
    { title: '状态', dataIndex: 'status', width: 90, render: courseStatusTag },
    { title: '版本', dataIndex: 'version', width: 70, render: (v) => `v${v}` },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_, row) => (
        <Space size={8} wrap>
          <a onClick={() => openEdit(row)}>编辑</a>
          <a onClick={() => void toggleStatus(row)}>{row.status === 'active' ? '下线' : '上线'}</a>
          <a
            onClick={() => {
              setSessionCourse(row);
              setSessionOpen(true);
            }}
          >
            期次/出勤
          </a>
          <a
            onClick={() => {
              setFeedbackCourse(row);
              setFeedbackOpen(true);
            }}
          >
            反馈汇总
          </a>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="课程目录"
      extra={
        <Space>
          <Input.Search
            allowClear
            placeholder="按标题搜索"
            style={{ width: 220 }}
            onSearch={(value) => {
              setPage(1);
              setKeyword(value.trim());
            }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增课程
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ current: page, pageSize: PAGE_SIZE, total, onChange: setPage, showSizeChanger: false }}
      />
      <Modal
        title={editing ? '编辑课程' : '新增课程'}
        open={modalOpen}
        width={620}
        onOk={() => void submit()}
        onCancel={() => setModalOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="课程标题" rules={[{ required: true, max: 150 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="material_url" label="材料链接（http/https）" rules={[{ required: true, max: 500 }]}>
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="description" label="课程说明">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="dimension_names" label="对应能力维度（可多个）">
            <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入维度名后回车" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: 'active', label: '有效' },
                { value: 'inactive', label: '已下线' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <SessionManager course={sessionCourse} open={sessionOpen} onClose={() => setSessionOpen(false)} />
      <CourseFeedback course={feedbackCourse} open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
    </Card>
  );
}

function PlanAdmin() {
  const [rows, setRows] = useState<TrainingPlan[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [detail, setDetail] = useState<TrainingPlan | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [courses, setCourses] = useState<TrainingCourse[]>([]);
  const [form] = Form.useForm();
  const [draftForm] = Form.useForm();
  const [extendForm] = Form.useForm();
  const [extendTarget, setExtendTarget] = useState<TrainingPlanItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchPlans({ page, page_size: PAGE_SIZE });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [page]);

  const loadCourses = useCallback(async () => {
    try {
      const data = await fetchCourses({ page: 1, page_size: 100, status: 'active' });
      setCourses(data.items);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, []);

  useEffect(() => {
    void load();
    void loadCourses();
  }, [load, loadCourses]);

  const courseOptions = useMemo(
    () => courses.map((course) => ({ value: course.id, label: course.title })),
    [courses],
  );

  const openDetail = async (id: number) => {
    try {
      const data = await getPlan(id);
      setDetail(data);
      setDrawerOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitExtend = async () => {
    if (!extendTarget) return;
    try {
      const values = await extendForm.validateFields();
      await extendPlanItem(extendTarget.id, {
        new_due_date: values.new_due_date.format('YYYY-MM-DD'),
        reason: values.reason,
      });
      message.success('已延长期限并通知员工');
      const planId = extendTarget.plan_id;
      setExtendTarget(null);
      const data = await getPlan(planId);
      setDetail(data);
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitManual = async () => {
    const values = await form.validateFields();
    try {
      await createPlan({
        employee_user_id: values.employee_user_id,
        talent_profile_id: values.talent_profile_id ?? null,
        target_job_title: values.target_job_title ?? null,
        gap_summary: values.gap_summary ?? null,
        items: (values.items ?? []).map(
          (item: { course_id: number; dimension_name?: string; reason?: string; due_date?: Dayjs }) => ({
            course_id: item.course_id,
            dimension_name: item.dimension_name ?? null,
            reason: item.reason ?? null,
            due_date: item.due_date ? item.due_date.format('YYYY-MM-DD') : null,
          }),
        ),
      });
      message.success('培训草稿已创建');
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitDraft = async () => {
    const values = await draftForm.validateFields();
    try {
      const data = await draftPlan({
        employee_user_id: values.employee_user_id,
        target_job_title: values.target_job_title ?? null,
        dimension_gaps: values.dimension_gaps ?? [],
        assessment_assignment_id: values.assessment_assignment_id ?? null,
        profile_summary: values.profile_summary ?? null,
      });
      if (!data.generated) {
        message.warning(data.reason || 'AI 未生成培训草稿');
        return;
      }
      message.success('AI 培训草稿已生成');
      setDraftOpen(false);
      draftForm.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const action = async (fn: () => Promise<unknown>, success: string) => {
    try {
      await fn();
      message.success(success);
      void load();
      if (detail) {
        const refreshed = await getPlan(detail.id);
        setDetail(refreshed);
      }
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<TrainingPlan> = [
    { title: '员工 user_id', dataIndex: 'employee_user_id', width: 110, render: (v) => `#${v}` },
    { title: '目标岗位', dataIndex: 'target_job_title', render: (v) => v ?? '—' },
    { title: '来源', dataIndex: 'source', width: 90, render: (v) => (v === 'ai' ? 'AI 草稿' : '手工') },
    { title: '版本', dataIndex: 'version', width: 70, render: (v) => `v${v}` },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v) => (v === 'assigned' ? <Tag color="success">已分配</Tag> : <Tag color="processing">草稿</Tag>),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, row) => (
        <Space size={8} wrap>
          <a onClick={() => void openDetail(row.id)}>详情</a>
          {row.status === 'draft' && <a onClick={() => void action(() => assignPlan(row.id), '计划已分配')}>分配</a>}
          {row.status === 'assigned' && <a onClick={() => void action(() => revisePlan(row.id), '已生成新版本草稿')}>修订</a>}
        </Space>
      ),
    },
  ];

  const detailItemColumns: ColumnsType<TrainingPlanItem> = [
    { title: '课程', key: 'course', render: (_, row) => row.course_snapshot?.title || `课程 #${row.course_id}` },
    { title: '对应维度', dataIndex: 'dimension_name', width: 120, render: (v) => v ?? '—' },
    { title: '理由', dataIndex: 'reason', render: (v) => v ?? '—' },
    { title: '到期日', dataIndex: 'due_date', width: 120, render: (v) => v ?? '—' },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, row) =>
        detail?.status === 'assigned' ? (
          <a
            onClick={() => {
              extendForm.resetFields();
              setExtendTarget(row);
            }}
          >
            延期
          </a>
        ) : null,
    },
    { title: '学习状态', key: 'learning', width: 100, render: (_, row) => learningStatusTag(row.learning.status) },
  ];

  return (
    <Card
      title="培训计划"
      extra={
        <Space>
          <Button onClick={() => setDraftOpen(true)}>AI 生成草稿</Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              form.resetFields();
              form.setFieldsValue({ items: [{}] });
              setModalOpen(true);
            }}
          >
            手工创建
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ current: page, pageSize: PAGE_SIZE, total, onChange: setPage, showSizeChanger: false }}
      />

      <Modal title="手工创建培训草稿" open={modalOpen} width={720} onOk={() => void submitManual()} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="employee_user_id" label="员工 user_id" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="talent_profile_id" label="人才档案 ID（可选）" style={{ flex: 1 }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="target_job_title" label="目标岗位" style={{ flex: 2 }}>
              <Input />
            </Form.Item>
          </Space>
          <Form.Item name="gap_summary" label="差距说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Divider orientation="left">课程项</Divider>
          <Form.List name="items">
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {fields.map((field) => (
                  <Space key={field.key} size={8} style={{ display: 'flex' }} align="baseline">
                    <Form.Item
                      name={[field.name, 'course_id']}
                      rules={[{ required: true, message: '请选择课程' }]}
                      style={{ flex: 2, marginBottom: 8 }}
                    >
                      <Select placeholder="选择有效课程" options={courseOptions} showSearch optionFilterProp="label" />
                    </Form.Item>
                    <Form.Item name={[field.name, 'dimension_name']} style={{ flex: 1, marginBottom: 8 }}>
                      <Input placeholder="对应维度" />
                    </Form.Item>
                    <Form.Item name={[field.name, 'reason']} style={{ flex: 2, marginBottom: 8 }}>
                      <Input placeholder="推荐理由" />
                    </Form.Item>
                    <Form.Item name={[field.name, 'due_date']} style={{ width: 150, marginBottom: 8 }}>
                      <DatePicker placeholder="到期日" style={{ width: '100%' }} />
                    </Form.Item>
                    <a onClick={() => remove(field.name)}>删除</a>
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add()} block>
                  添加课程项
                </Button>
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Modal
        title="延长必修期限"
        open={!!extendTarget}
        onOk={() => void submitExtend()}
        onCancel={() => setExtendTarget(null)}
        destroyOnClose
      >
        <Form form={extendForm} layout="vertical">
          <Form.Item name="new_due_date" label="新到期日" rules={[{ required: true, message: '请选择日期' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="reason" label="延期原因（必填）" rules={[{ required: true, message: '请填写原因' }]}>
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="AI 生成培训草稿" open={draftOpen} width={620} onOk={() => void submitDraft()} onCancel={() => setDraftOpen(false)}>
        <Form form={draftForm} layout="vertical">
          <Form.Item name="employee_user_id" label="员工 user_id" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="target_job_title" label="目标岗位">
            <Input />
          </Form.Item>
          <Form.Item name="dimension_gaps" label="待补足维度">
            <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入维度后回车" />
          </Form.Item>
          <Form.Item
            name="assessment_assignment_id"
            label="关联员工测评任务 ID（可选）"
            extra="仅员工测评可作为输入；招聘测评会返回跳过原因，不生成候选人培训。"
          >
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="profile_summary" label="档案摘要（可选）">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={detail ? `培训计划 #${detail.id} · v${detail.version}` : '培训计划'}
        width={760}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {!detail ? (
          <Empty />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="员工 user_id">#{detail.employee_user_id}</Descriptions.Item>
              <Descriptions.Item label="状态">
                {detail.status === 'assigned' ? <Tag color="success">已分配</Tag> : <Tag color="processing">草稿</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="目标岗位">{detail.target_job_title || '—'}</Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source === 'ai' ? 'AI 草稿' : '手工'}</Descriptions.Item>
              <Descriptions.Item label="差距说明" span={2}>
                {detail.gap_summary || '—'}
              </Descriptions.Item>
            </Descriptions>
            <Table rowKey="id" columns={detailItemColumns} dataSource={detail.items ?? []} pagination={false} />
            <Card size="small" title="版本记录">
              {detail.versions?.length ? (
                <Timeline
                  items={detail.versions.map((version) => ({
                    children: `v${version.version} · ${version.status} · ${version.created_at}`,
                  }))}
                />
              ) : (
                <Typography.Text type="secondary">暂无版本记录</Typography.Text>
              )}
            </Card>
          </Space>
        )}
      </Drawer>
    </Card>
  );
}

export default function TrainingPage() {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const role = getUser()?.role;
    setIsAdmin(role === 'admin' || role === 'hr');
  }, []);

  return (
    <Card title="培训成长">
      <Tabs
        items={
          isAdmin
            ? [
                { key: 'courses', label: '课程目录', children: <CourseAdmin /> },
                { key: 'plans', label: '培训计划', children: <PlanAdmin /> },
              ]
            : [{ key: 'mine', label: '我的培训', children: <MyTraining /> }]
        }
      />
    </Card>
  );
}
