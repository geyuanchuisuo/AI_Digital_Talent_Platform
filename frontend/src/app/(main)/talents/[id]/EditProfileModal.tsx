/**
 * 基本信息编辑弹窗（T-21，需求 F2-6 字段级保护）。
 *
 * 口径：
 * 1. 提交 PATCH `/talents/{id}`，**只传画像自有字段**（D-J：经历类一期只读，
 *    本弹窗不出现 educations/employments/projects/certificates 任何输入）。
 * 2. `manual_edited_fields` 里的字段在表单里打"已人工修正"标记（视觉保护提示）；
 *    后端只把**实际变化的字段**并入保护清单，前端不预判。
 * 3. phone/email 由后端归一化（D-N）；degree 只允许六档枚举；work_years 0–80。
 * 4. skills 用 Select tags 模式（输入技能名，提交数组，后端归一化去重排序）。
 */

'use client';

import { Form, Input, InputNumber, Modal, Select, Tag, Tooltip, message } from 'antd';
import { useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { patchTalent } from '../api';
import { DEGREE_OPTIONS } from '../utils';
import type { PatchTalentData, TalentProfileOut } from '../types';

export interface EditProfileModalProps {
  open: boolean;
  profile: TalentProfileOut;
  onClose: () => void;
  /** PATCH 成功后回传（含最新 manual_edited_fields / vector_status） */
  onPatched: (data: PatchTalentData) => void;
}

export default function EditProfileModal({ open, profile, onClose, onPatched }: EditProfileModalProps) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const protectedFields = profile.manual_edited_fields ?? [];

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        name: profile.name,
        phone: profile.phone,
        email: profile.email,
        city: profile.city,
        degree: profile.degree,
        work_years: profile.work_years === null ? undefined : Number(profile.work_years),
        current_title: profile.current_title,
        expected_title: profile.expected_title,
        skills: profile.skills ?? [],
      });
    }
  }, [open, profile, form]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const data = await patchTalent(profile.id, {
        name: values.name,
        phone: values.phone || null,
        email: values.email || null,
        city: values.city || null,
        degree: values.degree ?? null,
        work_years: values.work_years ?? null,
        current_title: values.current_title || null,
        expected_title: values.expected_title || null,
        skills: values.skills ?? [],
      });
      message.success(
        data.vector_status === 'failed' ? '已保存，但向量同步失败（可在本页稍后重建）' : '已保存',
      );
      onPatched(data);
      onClose();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  /** 字段标签带"已人工修正"标识（F2-6 视觉提示） */
  const label = (name: string, text: string) => (
    <span>
      {text}
      {protectedFields.includes(name) && (
        <Tooltip title="该字段已被人工修正，后续自动解析不会覆盖">
          <Tag color="blue" style={{ marginLeft: 6 }}>
            已人工修正
          </Tag>
        </Tooltip>
      )}
    </span>
  );

  return (
    <Modal
      title="编辑基本信息"
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      destroyOnClose
      width={520}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label={label('name', '姓名')} rules={[{ required: true, message: '姓名不能为空' }]}>
          <Input maxLength={50} />
        </Form.Item>
        <Form.Item name="phone" label={label('phone', '手机号')}>
          <Input maxLength={30} placeholder="支持 +86 / 分隔符，保存时自动归一化" />
        </Form.Item>
        <Form.Item name="email" label={label('email', '邮箱')}>
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="city" label={label('city', '城市')}>
          <Input maxLength={50} />
        </Form.Item>
        <Form.Item name="degree" label={label('degree', '学历')} tooltip="仅允许归一化六档字面值">
          <Select allowClear placeholder="选择学历" options={DEGREE_OPTIONS.map((v) => ({ value: v, label: v }))} />
        </Form.Item>
        <Form.Item name="work_years" label={label('work_years', '工作年限')}>
          <InputNumber min={0} max={80} step={0.1} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="current_title" label={label('current_title', '当前/最近职位')}>
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="expected_title" label={label('expected_title', '期望职位')}>
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="skills" label={label('skills', '技能')} tooltip="回车添加；保存时自动归一化（别名/大小写/去重排序）">
          <Select mode="tags" open={false} tokenSeparators={[',']} placeholder="输入技能后回车" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
