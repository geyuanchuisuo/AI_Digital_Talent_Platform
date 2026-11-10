/**
 * C 测评页：自适应逐题答题（默认）+ 固定试卷兼容 + 管理员题库、方案、任务与修分。
 *
 * 自适应口径（用户 2026-11-07 裁决）：方案发布时冻结策略与题库快照，逐题按表现调整难度，
 * 结果给出能力雷达与评级；固定试卷仅保留兼容模式。招聘成绩需管理员公布，员工评分完成即可见。
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

import { getUser, isManagement } from '@/lib/auth';
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
  generateQuestionDrafts,
  getAssignment,
  getResult,
  manualScore,
  nextQuestion,
  openAnswers,
  publishPlan,
  publishResult,
  retryGrading,
  saveAnswer,
  startAssignment,
  submitAssignment,
  updatePlan,
  updateQuestion,
} from './api';
import type {
  AnswerScoreRow,
  AssessmentAssignmentItem,
  AssessmentDetail,
  AssessmentPlan,
  AssessmentQuestion,
  AssessmentResultOut,
  NextQuestionOut,
  QuestionPayload,
} from './types';
import RadarChart from './RadarChart';

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
  const levelMap = new Map((result.ability?.dimension_levels ?? []).map((item) => [item.name, item.level]));
  return (
    <Card size="small" title={`测评结果 v${result.version}`} style={{ marginTop: 12 }}>
      <Descriptions column={3} size="small">
        <Descriptions.Item label="总分">{result.total_score}</Descriptions.Item>
        <Descriptions.Item label="总分线">{result.pass_score}</Descriptions.Item>
        <Descriptions.Item label="结论">
          {result.passed ? <Tag color="success">通过</Tag> : <Tag color="error">未通过</Tag>}
        </Descriptions.Item>
      </Descriptions>
      {result.ability && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'center', marginTop: 12 }}>
          <RadarChart
            dimensions={result.ability.radar.dimensions}
            scores={result.ability.radar.scores}
            maxScore={result.ability.radar.max_score}
          />
          <div style={{ minWidth: 220 }}>
            <Typography.Text strong>综合评级：{result.ability.rating.level}</Typography.Text>
            <div style={{ marginTop: 6 }}>强项：{result.ability.strengths.join('、') || '—'}</div>
            <div>待提升：{result.ability.gaps.join('、') || '—'}</div>
            <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 0 }}>
              {result.ability.summary}
            </Typography.Paragraph>
          </div>
        </div>
      )}
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
          { title: '档位', render: (_, row) => levelMap.get(row.name) ?? '—' },
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
  const [next, setNext] = useState<NextQuestionOut | null>(null);
  const [answerDraft, setAnswerDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<(AssessmentResultOut & {
    review_items?: { question_id: number; is_completed: boolean; question_snapshot: { stem: string; correct_answer?: string; reference_points?: string } }[];
  }) | null>(null);

  const load = useCallback(async () => {
    if (!assignmentId) return null;
    try {
      const data = await getAssignment(assignmentId);
      setDetail(data);
      return data;
    } catch (error) {
      message.error(getErrorMessage(error));
      return null;
    }
  }, [assignmentId]);

  const ensureNext = useCallback(async () => {
    if (!assignmentId) return;
    try {
      setNext(await nextQuestion(assignmentId));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, [assignmentId]);

  useEffect(() => {
    if (open && assignmentId) {
      setResult(null);
      setNext(null);
      setAnswerDraft('');
      void (async () => {
        const data = await load();
        if (data?.assignment.mode === 'adaptive' && ['assigned', 'in_progress'].includes(data.assignment.status)) {
          await ensureNext();
        }
      })();
    }
  }, [open, assignmentId, load, ensureNext]);

  useEffect(() => {
    setAnswerDraft('');
  }, [next?.question?.question_id]);

  const handleSave = async (
    questionId: number,
    payload: { selected_option?: string; answer_text?: string },
    advance = false,
  ) => {
    if (!assignmentId) return;
    try {
      await saveAnswer(assignmentId, questionId, payload);
      if (detail?.assignment.mode === 'adaptive') {
        await load();
        if (advance && payload.selected_option) {
          await ensureNext();
        }
      }
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const advanceAdaptive = async (questionId: number, payload: { answer_text?: string }) => {
    if (!assignmentId) return;
    try {
      await saveAnswer(assignmentId, questionId, payload);
      await load();
      await ensureNext();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const doSubmit = async () => {
    if (!assignmentId) return;
    setSubmitting(true);
    try {
      const response = await submitAssignment(assignmentId);
      if (response.status === 'grading') {
        message.warning('部分简答题评分失败，成绩未生成；可稍后重试评分。');
      } else {
        message.success('已交卷');
      }
      setNext(null);
      await load();
      onChanged();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = () => {
    if (!assignmentId || !detail) return;
    let unanswered = 0;
    if (detail.assignment.mode === 'fixed') {
      unanswered = detail.questions.filter(
        (q) => !q.answer || (!q.answer.selected_option && !(q.answer.answer_text ?? '').trim()),
      ).length;
    } else if (next && !next.done) {
      unanswered = Math.max(0, next.target_question_count - next.answered_count);
    }
    if (unanswered > 0) {
      Modal.confirm({
        title: '确认交卷？',
        content: `还有 ${unanswered} 题未作答，未答题目按 0 分计入。`,
        okText: '确认交卷',
        cancelText: '继续作答',
        onOk: () => doSubmit(),
      });
      return;
    }
    void doSubmit();
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
            <Button type="primary" loading={submitting} onClick={handleSubmit}>
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
          {detail.assignment.mode === 'adaptive' && editable && (
            <Card size="small" title="自适应答题">
              <Typography.Text type="secondary">
                进度：已答 {next?.answered_count ?? 0} / 目标 {next?.target_question_count ?? assignment?.target_question_count ?? 0} 题；
                系统按已答表现动态调整后续难度。
              </Typography.Text>
              {next?.done ? (
                <Alert
                  style={{ marginTop: 8 }}
                  type="info"
                  showIcon
                  message={
                    next.reason === 'bank_exhausted'
                      ? '题库中可出题目已用尽，请交卷。'
                      : '已达到结束条件，请交卷。'
                  }
                />
              ) : next?.question ? (
                <div style={{ marginTop: 12 }}>
                  <Space>
                    <Tag>{next.question.dimension_name}</Tag>
                    <Tag color="processing">难度 {next.question.difficulty}</Tag>
                    <Tag>第 {next.sequence_no} 题</Tag>
                  </Space>
                  <Typography.Paragraph style={{ marginTop: 8 }}>{next.question.stem}</Typography.Paragraph>
                  {next.question.question_type === 'single_choice' ? (
                    <Radio.Group
                      value={undefined}
                      onChange={(event) =>
                        void handleSave(next.question!.question_id, { selected_option: event.target.value }, true)
                      }
                    >
                      <Space direction="vertical">
                        {next.question.options.map((option) => (
                          <Radio key={option} value={option}>
                            {option}
                          </Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                  ) : (
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input.TextArea
                        rows={4}
                        value={answerDraft}
                        onChange={(event) => setAnswerDraft(event.target.value)}
                        onBlur={() =>
                          void handleSave(next.question!.question_id, { answer_text: answerDraft })
                        }
                      />
                      <Button
                        type="primary"
                        onClick={() => void advanceAdaptive(next.question!.question_id, { answer_text: answerDraft })}
                      >
                        保存并下一题
                      </Button>
                    </Space>
                  )}
                  <Alert
                    style={{ marginTop: 8 }}
                    type="warning"
                    showIcon
                    message="交卷时当前题目若未作答，按 0 分计入。"
                  />
                </div>
              ) : (
                <Typography.Text type="secondary">正在获取题目…</Typography.Text>
              )}
            </Card>
          )}
          {(detail.assignment.mode === 'fixed' || detail.answers_visible) &&
            detail.questions.map((question, index) => {
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
  const [editing, setEditing] = useState<AssessmentQuestion | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [form] = Form.useForm<QuestionPayload & { options_text?: string }>();
  const [genForm] = Form.useForm();

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

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (row: AssessmentQuestion) => {
    setEditing(row);
    form.setFieldsValue({
      dimension_name: row.dimension_name,
      dimension_type: row.dimension_type,
      difficulty: row.difficulty,
      question_type: row.question_type,
      stem: row.stem,
      options_text: row.options.join('\n'),
      correct_answer: row.correct_answer ?? undefined,
      reference_points: row.reference_points ?? undefined,
      scoring_guideline: row.scoring_guideline ?? undefined,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const options = values.options_text
      ? values.options_text.split('\n').map((item) => item.trim()).filter(Boolean)
      : undefined;
    const { options_text: _optionsText, ...questionValues } = values;
    try {
      if (editing) {
        await updateQuestion(editing.id, { ...questionValues, options });
        message.success('题目已更新，重新进入待审核');
      } else {
        await createQuestion({ ...questionValues, options });
        message.success('题目已创建，待审核');
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitGen = async () => {
    const values = await genForm.validateFields().catch(() => null);
    if (!values) return;
    setGenerating(true);
    try {
      const generated = await generateQuestionDrafts(values);
      message.success(`已生成 ${generated.length} 道待审核题目，请审核后入库`);
      setGenOpen(false);
      genForm.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setGenerating(false);
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
      width: 130,
      render: (_, row) => (
        <Space size={8}>
          <a onClick={() => openEdit(row)}>编辑</a>
          {row.status !== 'approved' && (
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
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="题库"
      extra={
        <Space>
          <Button onClick={() => setGenOpen(true)}>AI 生成题目</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建题目
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
        title={editing ? `编辑题目 #${editing.id}` : '新建题目'}
        open={modalOpen}
        onOk={() => void submit()}
        onCancel={() => {
          setModalOpen(false);
          setEditing(null);
        }}
      >
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
      <Modal
        title="AI 生成题目"
        open={genOpen}
        confirmLoading={generating}
        onOk={() => void submitGen()}
        onCancel={() => setGenOpen(false)}
      >
        <Form
          form={genForm}
          layout="vertical"
          initialValues={{ dimension_type: 'professional', difficulty: 'medium', question_type: 'single_choice', count: 1 }}
        >
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
            <Form.Item name="count" label="数量" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Form.Item name="job_title" label="岗位名称（可选，用于贴合岗位语境）">
            <Input />
          </Form.Item>
          <Typography.Text type="secondary">
            由模型生成草稿，状态为待审核，需人工审核通过后才会进入组卷范围。
          </Typography.Text>
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
  const [editing, setEditing] = useState<AssessmentPlan | null>(null);
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
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const dimensions = (values.dimensions_text as string)
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [name, type, weight] = line.split(/[,，]/).map((item) => item.trim());
        return { name, type: type || 'professional', weight: Number(weight) };
      });
    const strategy =
      values.mode === 'adaptive'
        ? {
            min_question_count: values.min_question_count,
            max_question_count: values.max_question_count,
            start_difficulty: values.start_difficulty,
          }
        : undefined;
    try {
      if (editing) {
        await updatePlan(editing.id, {
          job_title: values.job_title,
          total_question_count: values.total_question_count,
          pass_score: values.pass_score,
          dimensions,
          mode: values.mode,
          strategy,
        });
        message.success('方案已更新');
      } else {
        await createPlan({
          purpose: values.purpose,
          job_id: values.job_id,
          job_title: values.job_title,
          total_question_count: values.total_question_count,
          pass_score: values.pass_score,
          dimensions,
          mode: values.mode,
          strategy,
        });
        message.success('方案已创建（草稿）');
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      void load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const openEdit = (row: AssessmentPlan) => {
    setEditing(row);
    form.setFieldsValue({
      purpose: row.purpose,
      job_id: row.job_id,
      job_title: row.job_title,
      dimensions_text: row.dimensions.map((d) => `${d.name},${d.type},${d.weight}`).join('\n'),
      mode: row.mode,
      total_question_count: row.total_question_count,
      pass_score: row.pass_score,
      min_question_count: row.strategy?.min_question_count,
      max_question_count: row.strategy?.max_question_count,
      start_difficulty: row.strategy?.start_difficulty ?? 'medium',
    });
    setModalOpen(true);
  };

  const publish = async (id: number) => {
    try {
      const published = await publishPlan(id);
      message.success(
        published.mode === 'adaptive' ? '已发布：策略与题库快照已冻结' : '已发布并冻结试卷',
      );
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
    {
      title: '模式',
      dataIndex: 'mode',
      width: 90,
      render: (v) => (v === 'adaptive' ? <Tag color="processing">自适应</Tag> : <Tag>固定试卷</Tag>),
    },
    { title: '维度', render: (_, row) => row.dimensions.map((d) => `${d.name}${d.weight}%`).join(' / ') },
    { title: '题量', dataIndex: 'total_question_count', width: 70 },
    { title: '总分线', dataIndex: 'pass_score', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
    {
      title: '操作',
      key: 'action',
      width: 130,
      render: (_, row) =>
        row.status === 'draft' ? (
          <Space size={8}>
            <a onClick={() => openEdit(row)}>编辑</a>
            <a onClick={() => void publish(row.id)}>发布</a>
          </Space>
        ) : row.mode === 'adaptive' ? (
          <Typography.Text type="secondary">题库 {row.bank_snapshot?.question_count ?? '—'}</Typography.Text>
        ) : (
          row.paper && <Typography.Text type="secondary">试卷 #{row.paper.id}</Typography.Text>
        ),
    },
  ];

  return (
    <Card
      title="测评方案"
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null);
            form.resetFields();
            setModalOpen(true);
          }}
        >
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
      <Modal
        title={editing ? `编辑方案 #${editing.id}` : '新建测评方案'}
        open={modalOpen}
        width={620}
        onOk={() => void submit()}
        onCancel={() => {
          setModalOpen(false);
          setEditing(null);
        }}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            purpose: 'recruitment',
            job_id: 1,
            total_question_count: 4,
            pass_score: 60,
            mode: 'adaptive',
            min_question_count: 4,
            max_question_count: 4,
            start_difficulty: 'medium',
          }}
        >
          <Space size={8} style={{ display: 'flex' }}>
            <Form.Item name="purpose" label="用途" style={{ flex: 1 }}>
              <Select disabled={!!editing} options={[{ value: 'recruitment', label: '招聘测评' }, { value: 'employee', label: '员工测评' }]} />
            </Form.Item>
            <Form.Item name="job_id" label="岗位 ID" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} disabled={!!editing} style={{ width: '100%' }} />
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
            <Form.Item name="mode" label="测评模式" style={{ flex: 1 }}>
              <Select
                options={[
                  { value: 'adaptive', label: '自适应（千人千卷）' },
                  { value: 'fixed', label: '固定试卷（兼容）' },
                ]}
              />
            </Form.Item>
            <Form.Item name="total_question_count" label="目标题量" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={1} max={200} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="pass_score" label="总分线" style={{ flex: 1 }} rules={[{ required: true }]}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Form.Item shouldUpdate noStyle>
            {({ getFieldValue }) =>
              getFieldValue('mode') === 'adaptive' ? (
                <Space size={8} style={{ display: 'flex' }}>
                  <Form.Item name="min_question_count" label="最少题量" style={{ flex: 1 }} rules={[{ required: true }]}>
                    <InputNumber min={1} max={200} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="max_question_count" label="最多题量" style={{ flex: 1 }} rules={[{ required: true }]}>
                    <InputNumber min={1} max={200} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="start_difficulty" label="起始难度" style={{ flex: 1 }}>
                    <Select options={['easy', 'medium', 'hard'].map((v) => ({ value: v, label: v }))} />
                  </Form.Item>
                </Space>
              ) : null
            }
          </Form.Item>
          <Typography.Text type="secondary">
            自适应：发布时冻结策略与题库快照，按已答表现升降难度并留痕，题量边界需满足
            「最少 ≤ 目标 ≤ 最多」且不少于维度数。固定试卷为兼容模式，按权重分配题量并冻结整卷。
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

  const openAssign = async (prefill?: { plan_id: number; user_id: number; attempt_no: number }) => {
    try {
      const data = await fetchPlans({ status: 'published', page: 1, page_size: 100 });
      setPlans(data.items);
      form.setFieldsValue(
        prefill ? { plan_id: prefill.plan_id, user_id: prefill.user_id, attempt_no: prefill.attempt_no } : { attempt_no: 1 },
      );
      setAssignOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitAssign = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    try {
      await assignAssessment({
        plan_id: values.plan_id,
        user_id: values.user_id,
        attempt_no: values.attempt_no ?? 1,
        idempotency_key: `ui-${values.plan_id}-${values.user_id}-${values.attempt_no ?? 1}-${Date.now()}`,
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
          {row.status === 'completed' && (
            <a
              onClick={() =>
                void openAssign({ plan_id: row.plan_id, user_id: row.user_id, attempt_no: row.attempt_no + 1 })
              }
            >
              复测
            </a>
          )}
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
          <Form.Item name="attempt_no" label="复测序号（首次为 1）">
            <InputNumber min={1} max={20} style={{ width: '100%' }} />
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
    setIsAdmin(isManagement());
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
    <Card title="岗位测评">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="测评按方案组卷：固定卷按既定题目，自适应卷按作答表现动态调整难度；招聘测评成绩需管理员确认公布后可见。"
      />
      <Tabs items={items} />
    </Card>
  );
}
