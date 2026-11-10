/**
 * AI 面试间：管理员创建会话与题目、查看模型评分；候选人逐题作答并结束面试。
 * 评分为真实模型调用，失败题目可重试；未评分题目不计入总分。
 */

'use client';

import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

import { getUser } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import {
  addQuestion,
  answerQuestion,
  createSession,
  fetchQuestions,
  fetchSessions,
  finishSession,
  retryQuestion,
  startSession,
} from './api';
import type { AiQuestionItem, AiSessionItem } from './api';

function sessionTag(status: string) {
  if (status === 'completed') return <Tag color="success">已完成</Tag>;
  if (status === 'in_progress') return <Tag color="processing">进行中</Tag>;
  return <Tag>待开始</Tag>;
}

function questionTag(status: string) {
  if (status === 'graded') return <Tag color="success">已评分</Tag>;
  if (status === 'failed') return <Tag color="error">评分失败</Tag>;
  return <Tag>待作答</Tag>;
}

export default function AiInterviewPage() {
  const [isAdmin, setIsAdmin] = useState(false);
  const [userId, setUserId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<AiSessionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [current, setCurrent] = useState<AiSessionItem | null>(null);
  const [questions, setQuestions] = useState<AiQuestionItem[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [questionForm] = Form.useForm();
  const [answerDrafts, setAnswerDrafts] = useState<Record<number, string>>({});

  useEffect(() => {
    const user = getUser();
    setIsAdmin(user?.role === 'admin' || user?.role === 'hr');
    setUserId(user?.id ?? null);
  }, []);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await fetchSessions());
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const loadQuestions = useCallback(async (sessionId: number) => {
    try {
      setQuestions(await fetchQuestions(sessionId));
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  }, []);

  const selectSession = (row: AiSessionItem) => {
    setCurrent(row);
    void loadQuestions(row.id);
  };

  const submitCreate = async () => {
    const values = await createForm.validateFields().catch(() => null);
    if (!values) return;
    try {
      await createSession(values);
      message.success('面试已创建');
      setCreateOpen(false);
      createForm.resetFields();
      void loadSessions();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitQuestion = async () => {
    if (!current) return;
    const values = await questionForm.validateFields().catch(() => null);
    if (!values) return;
    try {
      await addQuestion(current.id, values);
      message.success('题目已添加');
      questionForm.resetFields();
      void loadQuestions(current.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const submitAnswer = async (question: AiQuestionItem) => {
    if (!current) return;
    const text = (answerDrafts[question.id] ?? '').trim();
    if (!text) {
      message.warning('请先填写作答内容');
      return;
    }
    try {
      const result = await answerQuestion(question.id, text);
      if (result.status === 'failed') {
        message.warning('模型评分失败，可稍后重试');
      } else {
        message.success('已提交并评分');
      }
      void loadQuestions(current.id);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const sessionColumns: ColumnsType<AiSessionItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '候选人',
      dataIndex: 'candidate_user_id',
      width: 100,
      render: (v) => (v ? `#${v}` : '—'),
    },
    { title: '状态', dataIndex: 'status', width: 100, render: sessionTag },
    {
      title: '总分',
      dataIndex: 'overall_score',
      width: 90,
      render: (v) => (v === null ? '—' : Number(v).toFixed(1)),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_, row) => (
        <Space size={8} wrap>
          <a onClick={() => selectSession(row)}>题目</a>
          {row.status === 'pending' && (
            <a
              onClick={async () => {
                try {
                  await startSession(row.id);
                  message.success('已开始');
                  void loadSessions();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              开始
            </a>
          )}
          {row.status === 'in_progress' && (
            <a
              onClick={async () => {
                try {
                  const res = await finishSession(row.id);
                  message.success(res.overall_score === null ? '已结束（暂无评分）' : `已结束，总分 ${res.overall_score.toFixed(1)}`);
                  void loadSessions();
                } catch (error) {
                  message.error(getErrorMessage(error));
                }
              }}
            >
              结束
            </a>
          )}
        </Space>
      ),
    },
  ];

  const canAnswer = Boolean(current) && current!.status !== 'completed' && (!isAdmin || current!.candidate_user_id === userId);

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Card
        title="AI 面试间"
        extra={
          isAdmin ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建面试
            </Button>
          ) : null
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="AI 评分由真实模型给出，仅文本问答，不含音视频或情绪推断；评分失败题目可重试，未评分题目不计入总分。"
        />
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={sessionColumns}
          dataSource={sessions}
          pagination={false}
        />
      </Card>

      {current && (
        <Card
          title={`会话 #${current.id} 题目`}
          extra={
            <Space>
              <Button icon={<ReloadOutlined />} onClick={() => void loadQuestions(current.id)}>
                刷新
              </Button>
              {isAdmin && current.status !== 'completed' && (
                <Button type="primary" onClick={() => void finishSession(current.id).then(() => loadSessions())}>
                  结束面试
                </Button>
              )}
            </Space>
          }
        >
          {isAdmin && current.status !== 'completed' && (
            <Form form={questionForm} layout="inline" style={{ marginBottom: 16 }} onFinish={() => void submitQuestion()}>
              <Form.Item name="question_text" rules={[{ required: true, min: 1 }]} style={{ flex: 1, minWidth: 400 }}>
                <Input placeholder="题干，例如：请描述一次你主导的跨部门协作" />
              </Form.Item>
              <Form.Item name="question_type" initialValue="structured">
                <Select
                  style={{ width: 130 }}
                  options={[{ value: 'structured', label: '结构化' }, { value: 'case', label: '案例' }]}
                />
              </Form.Item>
              <Button type="primary" htmlType="submit">
                添加题目
              </Button>
            </Form>
          )}
          <List
            bordered
            dataSource={questions}
            locale={{ emptyText: '暂无题目' }}
            renderItem={(q) => (
              <List.Item>
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space wrap>
                    <Typography.Text strong>
                      {q.sequence_no}. {q.question_text}
                    </Typography.Text>
                    {questionTag(q.status)}
                    {q.ai_score !== null && <Tag color="blue">得分 {Number(q.ai_score).toFixed(1)}</Tag>}
                  </Space>
                  {q.answer_text && <Typography.Text type="secondary">作答：{q.answer_text}</Typography.Text>}
                  {q.ai_feedback?.feedback && (
                    <Typography.Text type="secondary">评语：{q.ai_feedback.feedback}</Typography.Text>
                  )}
                  {canAnswer && (q.status === 'pending' || q.status === 'failed') && (
                    <Space.Compact style={{ width: '100%' }}>
                      <Input.TextArea
                        rows={2}
                        placeholder="填写作答内容"
                        value={answerDrafts[q.id] ?? (q.answer_text ?? '')}
                        onChange={(e) => setAnswerDrafts({ ...answerDrafts, [q.id]: e.target.value })}
                      />
                      <Button type="primary" onClick={() => void submitAnswer(q)}>
                        {q.status === 'failed' ? '重新作答' : '提交作答'}
                      </Button>
                    </Space.Compact>
                  )}
                  {q.status === 'failed' && isAdmin && (
                    <a
                      onClick={async () => {
                        try {
                          await retryQuestion(q.id);
                          message.success('已重试评分');
                          void loadQuestions(current.id);
                        } catch (error) {
                          message.error(getErrorMessage(error));
                        }
                      }}
                    >
                      重试评分
                    </a>
                  )}
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}

      <Modal title="新建 AI 面试" open={createOpen} onOk={() => void submitCreate()} onCancel={() => setCreateOpen(false)}>
        <Form form={createForm} layout="vertical">
          <Form.Item name="candidate_user_id" label="候选人 user_id">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="talent_profile_id" label="人才档案 ID（可选）">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="job_id" label="岗位 ID（可选）">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="application_id" label="招聘申请 ID（可选，待 A 提供契约）">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
