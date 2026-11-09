/**
 * 岗位条件表单弹窗（T-26，需求 F3-1 / 接口 §6）。
 *
 * 口径：
 * 1. 新建（POST）与编辑（PUT 全量写入字段）共用；非法配置**前端先拦截**
 *    （权重总和 100、pass>pending、min_degree 六档字面值），后端 400 仍会兜底展示。
 * 2. 权重用四维滑块（步长 5），实时显示总和；≠100 时保存按钮禁用。
 * 3. 技能输入 Select tags 模式（提示会归一化为规范名，如 `Python 3.11`→`python`）。
 */

'use client';

import { Form, Input, InputNumber, Modal, Select, Slider, Switch, Typography, message } from 'antd';
import { useEffect, useMemo, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { createJobProfile, putJobProfile } from './api';
import { DEGREE_OPTIONS } from './types';
import type { JobProfileOut, JobProfileWritePayload, Weights } from './types';

const { Text } = Typography;

export interface JobProfileFormModalProps {
  open: boolean;
  /** 编辑对象（null = 新建） */
  editing: JobProfileOut | null;
  onClose: () => void;
  onSaved: () => void;
}

const WEIGHT_DIMS: { key: keyof Weights; label: string }[] = [
  { key: 'skills', label: '技能' },
  { key: 'experience', label: '经验' },
  { key: 'education', label: '学历' },
  { key: 'others', label: '其他' },
];

export default function JobProfileFormModal({ open, editing, onClose, onSaved }: JobProfileFormModalProps) {
  const [form] = Form.useForm();
  const [weights, setWeights] = useState<Weights>({ skills: 40, experience: 30, education: 15, others: 15 });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editing) {
      const switches = editing.hard_rule_switches ?? { degree: true, work_years: true, must_skills: true };
      form.setFieldsValue({
        title: editing.title,
        department: editing.department,
        jd_text: editing.jd_text,
        requirements_text: editing.requirements_text,
        min_degree: editing.min_degree,
        min_work_years: editing.min_work_years === null ? undefined : Number(editing.min_work_years),
        must_skills: editing.must_skills,
        bonus_skills: editing.bonus_skills,
        pass_threshold: Number(editing.pass_threshold),
        pending_threshold: Number(editing.pending_threshold),
        hard_rule_switches: switches,
      });
      setWeights(editing.weights);
    } else {
      form.resetFields();
      form.setFieldsValue({
        pass_threshold: 75,
        pending_threshold: 60,
        hard_rule_switches: { degree: true, work_years: true, must_skills: true },
      });
      setWeights({ skills: 40, experience: 30, education: 15, others: 15 });
    }
  }, [open, editing, form]);

  const weightTotal = useMemo(
    () => Math.round(weights.skills + weights.experience + weights.education + weights.others),
    [weights],
  );
  const weightsOk = weightTotal === 100;

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload: JobProfileWritePayload = {
        title: values.title,
        department: values.department || null,
        jd_text: values.jd_text,
        requirements_text: values.requirements_text,
        min_degree: values.min_degree ?? null,
        min_work_years: values.min_work_years ?? null,
        must_skills: values.must_skills ?? [],
        bonus_skills: values.bonus_skills ?? [],
        hard_rule_switches: values.hard_rule_switches ?? { degree: true, work_years: true, must_skills: true },
        pass_threshold: values.pass_threshold,
        pending_threshold: values.pending_threshold,
        weights,
      };
      if (editing) {
        await putJobProfile(editing.id, payload);
        message.success(`已保存（version+1，历史批次不受影响）`);
      } else {
        await createJobProfile(payload);
        message.success('岗位已创建');
      }
      onSaved();
      onClose();
    } catch (error) {
      // Form validation already renders field-level errors. Consume its rejection
      // without treating an incomplete form as a server failure.
      if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        message.error(getErrorMessage(error));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={editing ? `编辑岗位：${editing.title}（v${editing.version}）` : '新建岗位条件'}
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      width={680}
      destroyOnClose
      okButtonProps={{ disabled: !weightsOk }}
    >
      <Form form={form} layout="vertical">
        <Space_row>
          <Form.Item
            name="title"
            label="岗位名称"
            style={{ flex: 1 }}
            rules={[{ required: true, message: '岗位名称不能为空' }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="department" label="部门" style={{ width: 180 }}>
            <Input maxLength={100} />
          </Form.Item>
        </Space_row>
        <Form.Item
          name="jd_text"
          label="职责描述（JD）"
          rules={[{ required: true, message: '职责描述不能为空' }]}
        >
          <Input.TextArea rows={3} maxLength={5000} showCount />
        </Form.Item>
        <Form.Item
          name="requirements_text"
          label="任职要求"
          rules={[{ required: true, message: '任职要求不能为空' }]}
        >
          <Input.TextArea rows={3} maxLength={5000} showCount />
        </Form.Item>
        <Space_row>
          <Form.Item name="min_degree" label="最低学历" style={{ width: 160 }}>
            <Select
              allowClear
              placeholder="不限"
              options={DEGREE_OPTIONS.map((value) => ({ value, label: value }))}
            />
          </Form.Item>
          <Form.Item name="min_work_years" label="最低年限" style={{ width: 140 }}>
            <InputNumber min={0} max={80} step={0.5} style={{ width: '100%' }} placeholder="不限" />
          </Form.Item>
          <Form.Item
            name={['hard_rule_switches', 'degree']}
            label="学历硬门槛"
            valuePropName="checked"
            initialValue
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name={['hard_rule_switches', 'work_years']}
            label="年限硬门槛"
            valuePropName="checked"
            initialValue
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name={['hard_rule_switches', 'must_skills']}
            label="技能硬门槛"
            valuePropName="checked"
            initialValue
          >
            <Switch />
          </Form.Item>
        </Space_row>
        <Form.Item
          name="must_skills"
          label="必备技能（任一缺失即硬淘汰）"
          tooltip="保存时自动归一化：小写、别名映射（golang→go）、去版本号"
        >
          <Select mode="tags" open={false} tokenSeparators={[',']} placeholder="输入技能后回车" />
        </Form.Item>
        <Form.Item name="bonus_skills" label="加分技能（不参与硬淘汰）">
          <Select mode="tags" open={false} tokenSeparators={[',']} placeholder="输入技能后回车" />
        </Form.Item>
        <Space_row>
          <Form.Item
            name="pass_threshold"
            label="通过阈值"
            rules={[{ required: true }]}
            style={{ width: 140 }}
          >
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="pending_threshold"
            label="待定阈值"
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (value === undefined || getFieldValue('pass_threshold') > value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('通过阈值必须大于待定阈值'));
                },
              }),
            ]}
            style={{ width: 140 }}
          >
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
        </Space_row>
        <div style={{ marginBottom: 8 }}>
          <Text strong>四维权重</Text>{' '}
          <Text type={weightsOk ? 'secondary' : 'danger'}>
            总和 {weightTotal} / 100{weightsOk ? '' : '（必须等于 100 才能保存）'}
          </Text>
        </div>
        {WEIGHT_DIMS.map((dim) => (
          <div key={dim.key} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 40 }}>{dim.label}</span>
            <Slider
              style={{ flex: 1 }}
              min={0}
              max={100}
              step={5}
              value={weights[dim.key]}
              onChange={(value) => setWeights((prev) => ({ ...prev, [dim.key]: value }))}
            />
            <span style={{ width: 40, textAlign: 'right' }}>{weights[dim.key]}</span>
          </div>
        ))}
      </Form>
    </Modal>
  );
}

/** 简易横向布局容器（避免额外引入 Row/Col 的样板） */
function Space_row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>{children}</div>;
}
