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
  Descriptions,
  Divider,
  Drawer,
  Empty,
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
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getUser } from '@/lib/auth';
import { getErrorMessage } from '@/lib/http';

import {
  assignPlan,
  createCourse,
  createPlan,
  draftPlan,
  fetchCourses,
  fetchGrowth,
  fetchMyPlans,
  fetchPlans,
  getPlan,
  revisePlan,
  updateCourse,
  updateProgress,
} from './api';
import type { GrowthEvent, TrainingCourse, TrainingPlan, TrainingPlanItem } from './types';

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
    {
      title: '学习状态',
      key: 'learning',
      width: 160,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          {learningStatusTag(row.learning.status)}
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
    </Space>
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
      width: 150,
      render: (_, row) => (
        <Space size={8}>
          <a onClick={() => openEdit(row)}>编辑</a>
          <a onClick={() => void toggleStatus(row)}>{row.status === 'active' ? '下线' : '上线'}</a>
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

  const submitManual = async () => {
    const values = await form.validateFields();
    try {
      await createPlan({
        employee_user_id: values.employee_user_id,
        talent_profile_id: values.talent_profile_id ?? null,
        target_job_title: values.target_job_title ?? null,
        gap_summary: values.gap_summary ?? null,
        items: (values.items ?? []).map((item: { course_id: number; dimension_name?: string; reason?: string }) => ({
          course_id: item.course_id,
          dimension_name: item.dimension_name ?? null,
          reason: item.reason ?? null,
        })),
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
