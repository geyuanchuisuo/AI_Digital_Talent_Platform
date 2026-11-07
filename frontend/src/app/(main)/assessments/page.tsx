/**
 * C 固定测评页：员工/候选人答题 + 管理员题库、方案、任务与修分。
 *
 * 一期口径：招聘与员工测评均为固定岗位试卷，不做自适应；招聘成绩需管理员公布，
 * 员工评分完成即可见；标准答案与复习入口由管理员独立开放。
 */

'use client';

import { PlusOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
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
  approveQuestion,
  assignAssessment,
  completeReviewItem,
  createPlan,
  createQuestion,
  fetchAnswerScores,
  fetchAssignments,
  fetchMyAssignments,
  fetchPlans,
  fetchQuestions,
  getAssignment,
  getResult,
  manualScore,
  openAnswers,
  publishPlan,
  publishResult,
  retryGrading,
  saveAnswer,
  startAssignment,
  submitAssignment,
} from './api';
import type {
  AnswerScoreRow,
  AssessmentAssignmentItem,
  AssessmentDetail,
  AssessmentPlan,
  AssessmentQuestion,
  AssessmentResultOut,
  QuestionPayload,
} from './types';

const PAGE_SIZE = 10;

function statusTag(status: string) {
  const map: Record<string, { color: string; text: string }> = {
    assigned: { color: 'default', text: '待开始' },
    in_progress: { color: 'processing', text: '作答中' },
    grading: { color: 'warning', text: '评分中' },
    completed: { color: 'success', text: '已完成' },
    cancelled: { color: 'default', text: '已取消' },
    draft: { color: 'default', text: '草稿' },
    published: { color: 'success', text: '已发布' },
    pending: { color: 'warning', text: '待审核' },
    approved: { color: 'success', text: '已通过' },
    disabled: { color: 'default', text: '已停用' },
  };
  const hit = map[status] ?? { color: 'default', text: status };
  return <Tag color={hit.color}>{hit.text}</Tag>;
}

function ResultBlock({ result }: { result: AssessmentResultOut }) {
  return (
    <Card size="small" title={`测评结果 v${result.version}`} style={{ marginTop: 12 }}>
      <Descriptions column={3} size="small">
        <Descriptions.Item label="总分">{result.total_score}</Descriptions.Item>
        <Descriptions.Item label="总分线">{result.pass_score}</Descriptions.Item>
        <Descriptions.Item label="结论">
          {result.passed ? <Tag color="success">通过</Tag> : <Tag color="error">未通过</Tag>}
        </Descriptions.Item>
      </Descriptions>
      <Table
        rowKey="name"
        size="small"
        pagination={false}
        style={{ marginTop: 8 }}
        dataSource={result.dimension_scores}
        columns={[
          { title: '维度', dataIndex: 'name' },
          { title: '权重', dataIndex: 'weight', render: (v) => `${v}%` },
          { title: '题量', dataIndex: 'question_count' },
          { title: '维度得分', dataIndex: 'score' },
        ]}
      />
    </Card>
  );
}

function AnswerDrawer({
  assignmentId,
  open,
  onClose,
  onChanged,
}: {
  assignmentId: number | null;
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<AssessmentDetail | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<(AssessmentResultOut & {
    review_items?: { question_id: number; is_completed: boolean; question_snapshot: { stem: string; correct_answer?: string; reference_points?: string } }[];
  }) | null>(null);

  const load = useCallback(async () => {
    if (!assignmentId) return;
    try {
      setDetail(await getAssignment(assignmentId));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, [assignmentId]);

  useEffect(() => {
    if (open && assignmentId) {
      setResult(null);
      void load();
    }
  }, [open, assignmentId, load]);

  const handleSave = async (questionId: number, payload: { selected_option?: string; answer_text?: string }) => {
    if (!assignmentId) return;
    try {
      await saveAnswer(assignmentId, questionId, payload);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const handleSubmit = async () => {
    if (!assignmentId) return;
    setSubmitting(true);
    try {
      const response = await submitAssignment(assignmentId);
      if (response.status === 'grading') {
        message.warning('部分简答题评分失败，成绩未生成；可稍后重试评分。');
      } else {
        message.success('已交卷');
      }
      await load();
      onChanged();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    if (!assignmentId) return;
    try {
      await retryGrading(assignmentId);
      message.success('已重试失败的评分项');
      await load();
      onChanged();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const viewResult = async () => {
    if (!assignmentId) return;
    try {
      setResult(await getResult(assignmentId));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const assignment = detail?.assignment;
  const editable = assignment && ['assigned', 'in_progress'].includes(assignment.status);

  return (
    <Drawer
      title={assignment ? `测评任务 #${assignment.id} · ${assignment.job_title ?? ''}` : '测评任务'}
      width={760}
      open={open}
      onClose={onClose}
      extra={
        <Space>
          {assignment?.status === 'grading' && <Button onClick={() => void handleRetry()}>重试评分</Button>}
          {assignment?.status === 'completed' && detail?.result && (
            <Button onClick={() => void viewResult()}>查看结果与复习</Button>
          )}
          {editable && (
            <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
              交卷
            </Button>
          )}
        </Space>
      }
    >
      {!detail ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {assignment?.purpose === 'recruitment' && assignment.status === 'completed' && !detail.result && (
            <Alert type="info" showIcon message="招聘测评成绩需管理员确认并公布后才能查看。" />
          )}
          {detail.result && <ResultBlock result={detail.result} />}
          {result && (
            <Card size="small" title="标准答案与复习事项">
              {result.review_items?.length ? (
                result.review_items.map((item) => (
                  <div key={item.question_id} style={{ marginBottom: 12 }}>
                    <Typography.Text strong>{item.question_snapshot.stem}</Typography.Text>
                    <div>
                      <Typography.Text type="secondary">
                        正确答案：{item.question_snapshot.correct_answer || '简答题见评分说明'}
                      </Typography.Text>
                    </div>
                    <Button
                      size="small"
                      disabled={item.is_completed}
                      onClick={async () => {
                        if (!assignmentId) return;
                        await completeReviewItem(assignmentId, item.question_id);
                        message.success('已标记复习完成');
                        void viewResult();
                      }}
                    >
                      {item.is_completed ? '已复习' : '标记复习完成'}
                    </Button>
                  </div>
                ))
              ) : (
                <Typography.Text type="secondary">暂无错题复习事项。</Typography.Text>
              )}
            </Card>
          )}
          {detail.questions.map((question, index) => {
            const saved = question.answer;
            return (
              <Card key={question.question_id} size="small" title={`${index + 1}. ${question.dimension_name}`}>
                <Typography.Paragraph>{question.stem}</Typography.Paragraph>
                {question.question_type === 'single_choice' ? (
                  <Radio.Group
                    disabled={!editable}
                    value={saved?.selected_option ?? undefined}
                    onChange={(event) =>
                      void handleSave(question.question_id, { selected_option: event.target.value })
                    }
                  >
                    <Space direction="vertical">
                      {question.options.map((option) => (
                        <Radio key={option} value={option}>
                          {option}
                        </Radio>
                      ))}
                    </Space>
                  </Radio.Group>
                ) : (
                  <Input.TextArea
                    rows={4}
                    disabled={!editable}
                    defaultValue={saved?.answer_text ?? ''}
                    onBlur={(event) => void handleSave(question.question_id, { answer_text: event.target.value })}
                  />
                )}
                {detail.answers_visible && question.correct_answer && (
                  <Typography.Text type="success">正确答案：{question.correct_answer}</Typography.Text>
                )}
                {saved?.score !== null && saved?.score !== undefined && (
                  <Typography.Text type="secondary"> 得分：{saved.score}</Typography.Text>
                )}
              </Card>
            );
          })}
        </Space>
      )}
    </Drawer>
  );
}

function MyAssignments() {
  const [rows, setRows] = useState<AssessmentAssignmentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchMyAssignments();
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

  const openTask = async (id: number, start: boolean) => {
    try {
      if (start) await startAssignment(id);
      setActive(id);
      setDrawerOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<AssessmentAssignmentItem> = [
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
    {
      title: '用途',
      dataIndex: 'purpose',
      width: 100,
      render: (v) => (v === 'recruitment' ? '招聘测评' : '员工测评'),
    },
    { title: '状态', dataIndex: 'status', width: 100, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, row) =>
        row.status === 'assigned' ? (
          <a onClick={() => void openTask(row.id, true)}>开始答题</a>
        ) : (
          <a onClick={() => void openTask(row.id, false)}>
            {row.status === 'in_progress' ? '继续答题' : '查看'}
          </a>
        ),
    },
  ];

  return (
    <Card>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={false} />
      <AnswerDrawer
        assignmentId={active}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onChanged={() => void load()}
      />
    </Card>
  );
}

function QuestionAdmin() {
  const [rows, setRows] = useState<AssessmentQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm<QuestionPayload & { options_text?: string }>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchQuestions({ page, page_size: PAGE_SIZE });
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
    const values = await form.validateFields();
    const options = values.options_text
      ? values.options_text.split('\n').map((item) => item.trim()).filter(Boolean)
      : undefined;
    const { options_text: _optionsText, ...questionValues } = values;
    try {
      await createQuestion({ ...questionValues, options });
      message.success('题目已创建，待审核');
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<AssessmentQuestion> = [
    { title: '题干', dataIndex: 'stem', ellipsis: true },
    { title: '维度', dataIndex: 'dimension_name', width: 110 },
    { title: '难度', dataIndex: 'difficulty', width: 80 },
    {
      title: '题型',
      dataIndex: 'question_type',
      width: 90,
      render: (v) => (v === 'single_choice' ? '单选' : '简答'),
    },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) =>
        row.status !== 'approved' ? (
          <a
            onClick={async () => {
              try {
                await approveQuestion(row.id);
                message.success('已审核通过');
                void load();
              } catch (error) {
                message.error(getErrorMessage(error));
              }
            }}
          >
            审核通过
          </a>
        ) : (
          '—'
        ),
    },
  ];

  return (
    <Card
      title="题库"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新建题目
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
      <Modal title="新建题目" open={modalOpen} onOk={() => void submit()} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ dimension_type: 'professional', difficulty: 'medium', question_type: 'single_choice' }}>
          <Form.Item name="dimension_name" label="考核维度" rules={[{ required: true }]}>
            <Input placeholder="如：数据库基础" />
          </Form.Item>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="dimension_type" label="维度类型" style={{ flex: 1 }}>
              <Select options={[{ value: 'professional', label: '专业' }, { value: 'general', label: '通用' }]} />
            </Form.Item>
            <Form.Item name="difficulty" label="难度" style={{ flex: 1 }}>
              <Select options={['easy', 'medium', 'hard'].map((v) => ({ value: v, label: v }))} />
            </Form.Item>
            <Form.Item name="question_type" label="题型" style={{ flex: 1 }}>
              <Select options={[{ value: 'single_choice', label: '单选' }, { value: 'short_answer', label: '简答' }]} />
            </Form.Item>
          </Space>
          <Form.Item name="stem" label="题干" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="options_text" label="选项（每行一个，单选题必填）">
            <Input.TextArea rows={3} placeholder={'A. …\nB. …'} />
          </Form.Item>
          <Form.Item name="correct_answer" label="标准答案（选项原文）">
            <Input />
          </Form.Item>
          <Form.Item name="reference_points" label="参考要点（简答题）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="scoring_guideline" label="评分说明（简答题必填）">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function PlanAdmin() {
  const [rows, setRows] = useState<AssessmentPlan[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();

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

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    const values = await form.validateFields();
    const dimensions = (values.dimensions_text as string)
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [name, type, weight] = line.split(/[,，]/).map((item) => item.trim());
        return { name, type: type || 'professional', weight: Number(weight) };
      });
    try {
      await createPlan({
        purpose: values.purpose,
        job_id: values.job_id,
        job_title: values.job_title,
        total_question_count: values.total_question_count,
        pass_score: values.pass_score,
        dimensions,
      });
      message.success('方案已创建（草稿）');
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const publish = async (id: number) => {
    try {
      await publishPlan(id);
      message.success('已发布并冻结试卷');
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<AssessmentPlan> = [
    { title: '岗位', dataIndex: 'job_title' },
    {
      title: '用途',
      dataIndex: 'purpose',
      width: 100,
      render: (v) => (v === 'recruitment' ? '招聘测评' : '员工测评'),
    },
    { title: '维度', render: (_, row) => row.dimensions.map((d) => `${d.name}${d.weight}%`).join(' / ') },
    { title: '题量', dataIndex: 'total_question_count', width: 70 },
    { title: '总分线', dataIndex: 'pass_score', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) =>
        row.status === 'draft' ? (
          <a onClick={() => void publish(row.id)}>发布</a>
        ) : (
          row.paper && <Typography.Text type="secondary">试卷 #{row.paper.id}</Typography.Text>
        ),
    },
  ];

  return (
    <Card
      title="测评方案"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新建方案
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
      <Modal title="新建测评方案" open={modalOpen} width={620} onOk={() => void submit()} onCancel={() => setModalOpen(false)}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ purpose: 'recruitment', job_id: 1, total_question_count: 4, pass_score: 60 }}
        >
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="purpose" label="用途" style={{ flex: 1 }}>
              <Select options={[{ value: 'recruitment', label: '招聘测评' }, { value: 'employee', label: '员工测评' }]} />
            </Form.Item>
            <Form.Item name="job_id" label="岗位 ID" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="job_title" label="岗位名称" style={{ flex: 2 }} rules={[{ required: true }]}>
              <Input />
            </Form.Item>
          </Space>
          <Form.Item
            name="dimensions_text"
            label="考核维度（每行：名称,类型,权重；权重合计 100）"
            rules={[{ required: true }]}
            extra="类型填 professional 或 general；每个维度至少 1 题"
          >
            <Input.TextArea rows={4} placeholder={'数据库基础,professional,50\n沟通协作,general,50'} />
          </Form.Item>
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="total_question_count" label="总题量" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} max={200} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="pass_score" label="总分线" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Typography.Text type="secondary">
            系统按权重自动分配各维度题量，默认全部 medium 难度；题库不足时发布会被拒绝并列出缺口。
          </Typography.Text>
        </Form>
      </Modal>
    </Card>
  );
}

function AssignmentAdmin() {
  const [rows, setRows] = useState<AssessmentAssignmentItem[]>([]);
  const [plans, setPlans] = useState<AssessmentPlan[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [scoreOpen, setScoreOpen] = useState(false);
  const [scoreRows, setScoreRows] = useState<AnswerScoreRow[]>([]);
  const [scoreTarget, setScoreTarget] = useState<number | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAssignments({ page, page_size: PAGE_SIZE });
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

  const openAssign = async () => {
    try {
      const data = await fetchPlans({ status: 'published', page: 1, page_size: 100 });
      setPlans(data.items);
      setAssignOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitAssign = async () => {
    const values = await form.validateFields();
    try {
      await assignAssessment({
        plan_id: values.plan_id,
        user_id: values.user_id,
        idempotency_key: `ui-${values.plan_id}-${values.user_id}-${Date.now()}`,
      });
      message.success('已分配测评');
      setAssignOpen(false);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const openScores = async (id: number) => {
    try {
      const data = await fetchAnswerScores(id);
      setScoreRows(data.items);
      setScoreTarget(id);
      setScoreOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const columns: ColumnsType<AssessmentAssignmentItem> = [
    { title: '任务', dataIndex: 'id', width: 70 },
    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
    { title: '答题人', dataIndex: 'user_id', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '公开',
      width: 110,
      render: (_, row) =>
        row.published_result_version
          ? `成绩 v${row.published_result_version} 已公布${row.answers_open ? ' · 答案已开放' : ''}`
          : row.answers_open
            ? '答案已开放'
            : '未公布',
    },
    {
      title: '操作',
      key: 'action',
      width: 210,
      render: (_, row) => (
        <Space size={8} wrap>
          <a onClick={() => void openScores(row.id)}>评分/修分</a>
          {row.status === 'completed' && (
            <a
              onClick={async () => {
                try {
                  await publishResult(row.id);
                  message.success('成绩已公布');
                  void load();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              公布成绩
            </a>
          )}
          <a
            onClick={async () => {
              try {
                await openAnswers(row.id);
                message.success('已开放标准答案与复习');
                void load();
              } catch (error) {
                message.error(getErrorMessage(error));
              }
            }}
          >
            开放答案
          </a>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="测评任务"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => void openAssign()}>
          分配测评
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
      <Modal title="分配测评" open={assignOpen} onOk={() => void submitAssign()} onCancel={() => setAssignOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="plan_id" label="已发布方案" rules={[{ required: true }]}>
            <Select
              options={plans.map((plan) => ({
                value: plan.id,
                label: `#${plan.id} ${plan.job_title}（${plan.purpose === 'recruitment' ? '招聘' : '员工'}）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="user_id" label="答题人 user_id" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer
        title={`任务 #${scoreTarget ?? ''} 答案与修分`}
        width={680}
        open={scoreOpen}
        onClose={() => setScoreOpen(false)}
      >
        {scoreRows.map((row) => (
          <Card key={row.question_id} size="small" style={{ marginBottom: 10 }}>
            <Typography.Text strong>题目 #{row.question_id}</Typography.Text>
            <div>
              <Typography.Text type="secondary">
                作答：{row.selected_option || row.answer_text || '（未作答）'} · 当前得分：
                {row.score ?? '—'} · {row.grading_status}
              </Typography.Text>
            </div>
            <ManualScoreForm
              assignmentId={scoreTarget}
              questionId={row.question_id}
              onSaved={async () => {
                if (scoreTarget) await openScores(scoreTarget);
                void load();
              }}
            />
          </Card>
        ))}
      </Drawer>
    </Card>
  );
}

function ManualScoreForm({
  assignmentId,
  questionId,
  onSaved,
}: {
  assignmentId: number | null;
  questionId: number;
  onSaved: () => Promise<void>;
}) {
  const [score, setScore] = useState<number>(0);
  const [reason, setReason] = useState('');
  return (
    <Space.Compact style={{ marginTop: 8, width: '100%' }}>
      <InputNumber min={0} max={100} value={score} onChange={(v) => setScore(Number(v ?? 0))} style={{ width: 110 }} />
      <Input placeholder="修分理由（必填，写入审计）" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button
        type="primary"
        disabled={!assignmentId || !reason.trim()}
        onClick={async () => {
          if (!assignmentId) return;
          try {
            await manualScore(assignmentId, questionId, { score, reason: reason.trim() });
            message.success('已修分并生成新结果版本');
            setReason('');
            await onSaved();
          } catch (error) {
            message.error(getErrorMessage(error));
          }
        }}
      >
        保存
      </Button>
    </Space.Compact>
  );
}

export default function AssessmentsPage() {
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    const role = getUser()?.role;
    setIsAdmin(role === 'admin' || role === 'hr');
  }, []);

  const items = [
    { key: 'mine', label: '我的测评', children: <MyAssignments /> },
    ...(isAdmin
      ? [
          {
            key: 'questions',
            label: '题库',
            children: <QuestionAdmin />,
          },
          {
            key: 'plans',
            label: '方案',
            children: <PlanAdmin />,
          },
          {
            key: 'assignments',
            label: '任务与成绩',
            children: <AssignmentAdmin />,
          },
        ]
      : []),
  ];

  return (
    <Card title="固定岗位测评">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="一期测评按岗位固定标准与固定题量组卷，不做自适应升降难度；招聘成绩由管理员确认公布。"
      />
      <Tabs items={items} />
    </Card>
  );
}
