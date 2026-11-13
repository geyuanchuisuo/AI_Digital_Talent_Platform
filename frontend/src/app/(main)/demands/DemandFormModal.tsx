/**
 * 人才需求表单弹窗（T-A21）。新建（POST）与编辑（PATCH 增量字段）共用：
 * - 新建必填：岗位名称 / 需求人数；项目可先留空（A11 退回补充口径允许后续补齐）；
 * - 编辑允许状态流转（白名单由后端校验，400 提示原样展示）；
 * - 重复需求（同岗位同项目在途）后端 409，原样提示不重复创建。
 */

'use client';

import { DatePicker, Form, Input, InputNumber, Modal, Select, Typography, message } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { createDemand, fetchJobOptions, fetchProjectGroups, fetchProjects, updateDemand } from './api';
import { DEMAND_PRIORITIES, DEMAND_STATUSES, type DemandItem, type DemandWritePayload, type DemandStatus } from './types';

const { TextArea } = Input;
const { Text } = Typography;

const PRIORITY_LABELS: Record<string, string> = { high: '高', medium: '中', low: '低' };
const STATUS_LABELS: Record<string, string> = {
  open: '待启动',
  in_progress: '推进中',
  fulfilled: '已满足',
  closed: '已关闭',
};

export interface DemandFormModalProps {
  open: boolean;
  /** 编辑对象（null = 新建） */
  editing: DemandItem | null;
  onClose: () => void;
  onSaved: () => void;
}

interface FormValues {
  position_name: string;
  headcount: number;
  project_group_id?: number;
  project_id?: number;
  job_profile_id?: number;
  expected_start_date?: Dayjs;
  priority?: string;
  remark?: string;
  status?: DemandStatus;
}

export default function DemandFormModal({ open, editing, onClose, onSaved }: DemandFormModalProps) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [groups, setGroups] = useState<{ value: number; label: string }[]>([]);
  const [projects, setProjects] = useState<{ value: number; label: string; group: number | null }[]>([]);
  const [jobs, setJobs] = useState<{ value: number; label: string }[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSubmitError(null);
    void fetchProjectGroups()
      .then((d) => setGroups(d.items.map((g) => ({ value: g.id, label: g.name }))))
      .catch(() => setGroups([]));
    void fetchProjects()
      .then((d) =>
        setProjects(d.items.map((p) => ({ value: p.id, label: p.name, group: p.project_group_id }))),
      )
      .catch(() => setProjects([]));
    void fetchJobOptions()
      .then((d) => setJobs(d.items.map((j) => ({ value: j.id, label: j.title }))))
      .catch(() => setJobs([]));
    if (editing) {
      form.setFieldsValue({
        position_name: editing.position_name,
        headcount: editing.headcount,
        project_group_id: editing.project_group_id ?? undefined,
        project_id: editing.project_id ?? undefined,
        job_profile_id: editing.job_profile_id ?? undefined,
        expected_start_date: editing.expected_start_date ? dayjs(editing.expected_start_date) : undefined,
        priority: editing.priority,
        remark: editing.remark ?? undefined,
        status: editing.status,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ headcount: 1, priority: 'medium' });
    }
  }, [open, editing, form]);

  const groupId = Form.useWatch('project_group_id', form);
  const projectOptions = groups.length === 0 ? projects : projects.filter((p) => !groupId || p.group === groupId);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const payload: DemandWritePayload = {
        position_name: values.position_name.trim(),
        headcount: values.headcount,
        project_id: values.project_id ?? null,
        project_group_id: values.project_group_id ?? null,
        job_profile_id: values.job_profile_id ?? null,
        expected_start_date: values.expected_start_date ? values.expected_start_date.format('YYYY-MM-DD') : null,
        priority: (values.priority ?? 'medium') as DemandWritePayload['priority'],
        remark: values.remark?.trim() || null,
      };
      if (editing) {
        if (values.status) payload.status = values.status;
        await updateDemand(editing.id, payload);
        void message.success('需求已更新');
      } else {
        await createDemand(payload);
        void message.success('需求已创建');
      }
      onSaved();
      onClose();
    } catch (err) {
      // 表单校验错误（err.errorFields）不打错误横幅；接口错误原样展示
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      setSubmitError(getErrorMessage(err));
    }
  };

  return (
    <Modal
      open={open}
      title={editing ? `编辑需求 #${editing.id}` : '新建人才需求'}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => {
        setSaving(true);
        void submit().finally(() => setSaving(false));
      }}
      onCancel={onClose}
      destroyOnClose
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="position_name"
          label="需求岗位名称"
          rules={[{ required: true, message: '请输入需求岗位名称' }, { max: 120, message: '最长 120 字' }]}
        >
          <Input placeholder="如：后端工程师（3 人）" />
        </Form.Item>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 12 }}>
          <Form.Item
            name="headcount"
            label="需求人数"
            rules={[{ required: true, message: '请输入需求人数' }]}
          >
            <InputNumber min={1} max={999} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <Select
              options={DEMAND_PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABELS[p] ?? p }))}
              placeholder="中（默认）"
            />
          </Form.Item>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 12 }}>
          <Form.Item name="project_group_id" label="项目群（可空）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              options={groups}
              placeholder="选择项目群"
              onChange={() => form.setFieldValue('project_id', undefined)}
            />
          </Form.Item>
          <Form.Item name="project_id" label="项目（可空）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              options={projectOptions}
              placeholder="选择项目"
            />
          </Form.Item>
        </div>
        <Form.Item
          name="job_profile_id"
          label={
            <>
              关联岗位条件（可空）
              <Text type="secondary" style={{ marginLeft: 8, fontWeight: 'normal' }}>
                关联后才能参与评分/匹配与缺口统计
              </Text>
            </>
          }
        >
          <Select allowClear showSearch optionFilterProp="label" options={jobs} placeholder="选择岗位条件" />
        </Form.Item>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 12 }}>
          <Form.Item name="expected_start_date" label="期望到岗日期（可空）">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          {editing && (
            <Form.Item name="status" label="状态（流转白名单由后端校验）">
              <Select
                allowClear
                options={DEMAND_STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] ?? s }))}
                placeholder={editing.status}
              />
            </Form.Item>
          )}
        </div>
        <Form.Item name="remark" label="说明（可空）">
          <TextArea rows={2} maxLength={500} />
        </Form.Item>
        {submitError && <Text type="danger">{submitError}</Text>}
      </Form>
    </Modal>
  );
}
