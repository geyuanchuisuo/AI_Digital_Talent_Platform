/**
 * 标签编辑弹窗（T-21，需求 F2-6）。
 *
 * 口径（与后端「tags ∪ skills」合并模型对齐，见开发日志 T-18 取舍③）：
 * 1. 提交 PATCH `{ tags: [{tag, tag_type}] }`——以本弹窗名单为准整体重建，
 *    既有行 type/source 保留、新增行 source='manual'（后端语义）。
 * 2. **技能行（tag_type='skill'）随 skills 字段管理**：在本弹窗里只读展示、
 *    不可删除（删除技能请编辑基本信息的 skills），避免"清了又回来"的困惑。
 * 3. 行内展示 tag_type（技能/行业/级别/通用）与 source（llm/manual）。
 */

'use client';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { Alert, Button, Input, Modal, Select, Space, Tag, Tooltip, Typography, message } from 'antd';
import { useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { patchTalent } from '../api';
import TagSuggestions from '@/features/talents/TagSuggestions';
import type {TagProofDraft} from '@/features/talents/tagTypes';
import type { PatchTalentData, TalentProfileOut } from '../types';

const TAG_TYPES = [
  { value: 'general', label: '通用' },
  { value: 'industry', label: '行业' },
  { value: 'level', label: '级别' },
  { value: 'skill', label: '技能' },
];

export interface TagEditModalProps {
  open: boolean;
  profile: TalentProfileOut;
  onClose: () => void;
  onPatched: (data: PatchTalentData) => void;
}

export default function TagEditModal({ open, profile, onClose, onPatched }: TagEditModalProps) {
  /** 草稿：tag → 显式 type（undefined = 沿用既有行） */
  const [rows, setRows] = useState<{ tag: string; tag_type?: string; existed: boolean; proof?:TagProofDraft }[]>([]);
  const [newTag, setNewTag] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setRows((profile.tags ?? []).map((row) => ({ tag: row.tag, tag_type: row.tag_type, existed: true })));
      setNewTag('');
    }
  }, [open, profile]);

  const derivedSkills = new Set((profile.skills??[]).map(skill=>skill.toLocaleLowerCase()));
  const isDerivedSkill = (row:{tag:string;tag_type?:string;existed:boolean}) =>
    row.tag_type==='skill'&&row.existed&&derivedSkills.has(row.tag.toLocaleLowerCase());
  const skillRows = rows.filter(isDerivedSkill);
  const editableRows = rows.filter(row=>!isDerivedSkill(row));

  const addTag = () => {
    const name = newTag.trim();
    if (!name) return;
    if (rows.some((row) => row.tag === name.toLowerCase() || row.tag === name)) {
      message.warning('标签已存在');
      return;
    }
    setRows((prev) => [...prev, { tag: name, tag_type: 'general', existed: false }]);
    setNewTag('');
  };

  const removeRow = (tag: string) => {
    setRows((prev) => prev.filter((row) => row.tag !== tag));
  };

  const submit = async () => {
    setSaving(true);
    try {
      const data = await patchTalent(profile.id, {
        tags: rows.map((row) => ({ tag: row.tag, tag_type: row.tag_type ?? 'general' })),
        ...(rows.some(row=>row.proof)?{tag_proofs:rows.flatMap(row=>row.proof?[row.proof]:[])}:{}),
      });
      message.success(
        data.vector_status === 'failed' ? '已保存，但向量同步失败（可在本页稍后重建）' : '标签已更新',
      );
      onPatched(data);
      onClose();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="编辑标签"
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      width={560}
      destroyOnClose
    >
      <Alert
        style={{ marginBottom: 12 }}
        type="info"
        showIcon
        message="由技能字段生成的标签在此只读；独立添加的人工标签可在此编辑，不会被自动解析覆盖。"
      />
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        {open&&<TagSuggestions personId={profile.id} onChoose={(tag,tag_type,proof)=>{
          setRows(previous=>previous.some(row=>row.tag.toLocaleLowerCase()===tag.toLocaleLowerCase())
            ?previous.map(row=>row.tag.toLocaleLowerCase()===tag.toLocaleLowerCase()?{...row,tag_type,proof}:row)
            :[...previous,{tag,tag_type,existed:false,proof}]);
        }}/>}
        {skillRows.length > 0 && (
          <Space wrap size={4}>
            <Text type="secondary">技能（随技能字段管理）：</Text>
            {skillRows.map((row) => (
              <Tooltip key={row.tag} title="技能标签由基本信息里的技能字段生成">
                <Tag color="blue">{row.tag}</Tag>
              </Tooltip>
            ))}
          </Space>
        )}
        {editableRows.map((row) => (
          <Space key={row.tag} style={{ display: 'flex' }}>
            <Tag>{row.tag}</Tag>
            <Select
              size="small"
              style={{ width: 100 }}
              value={row.tag_type ?? 'general'}
              options={TAG_TYPES}
              onChange={(value) =>
                setRows((prev) => prev.map((r) => (r.tag === row.tag ? { ...r, tag_type: value, proof:undefined } : r)))
              }
            />
            {row.existed && <Tag color={sourceColor(profile, row.tag)}>来源：{sourceLabel(profile, row.tag)}</Tag>}
            {row.proof&&<Tag color="blue">已附原文核对 · 版本 {row.proof.profile_version}，保存后生效</Tag>}
            <Button
              size="small"
              type="text"
              danger
              icon={<DeleteOutlined />}
              onClick={() => removeRow(row.tag)}
              aria-label={`删除标签 ${row.tag}`}
            />
          </Space>
        ))}
        {editableRows.length === 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            暂无自定义标签
          </Text>
        )}
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="新增标签"
            value={newTag}
            onChange={(event) => setNewTag(event.target.value)}
            onPressEnter={addTag}
            maxLength={100}
          />
          <Button icon={<PlusOutlined />} onClick={addTag}>
            添加
          </Button>
        </Space.Compact>
      </Space>
    </Modal>
  );
}

const { Text } = Typography;

function sourceLabel(profile: TalentProfileOut, tag: string): string {
  return profile.tags?.find((row) => row.tag === tag)?.source === 'manual' ? '人工' : 'AI';
}

function sourceColor(profile: TalentProfileOut, tag: string): string {
  return profile.tags?.find((row) => row.tag === tag)?.source === 'manual' ? 'geekblue' : 'green';
}
