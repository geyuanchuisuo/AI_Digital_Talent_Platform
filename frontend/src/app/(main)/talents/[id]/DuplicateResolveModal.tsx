/**
 * 查重确认弹窗（T-21，需求 F2-2 / 接口 §5）。
 *
 * 入口：详情页顶部"该画像有待确认的重复简历"告警条（本画像作为既有画像 A
 * 出现在 pending 查重记录里）。弹窗**并排对比**解析结果与画像 A：
 * - **master 固定为画像 A**（接口 §5：传其他值 400），不提供选择；
 * - merge → 200，展示返回的 `vector_status`（failed 时提示去重建向量）；
 * - keep → 202，展示 `task_id` 并提示后台补齐进度在解析队列可见。
 */

'use client';

import { Button, Descriptions, Divider, Modal, Space, Tag, Typography, message } from 'antd';

import { getErrorMessage } from '@/lib/http';

import { resolveDuplicate } from '../api';
import type { DuplicateItem } from '../types';
import { duplicateReasonMeta, formatTime } from '../utils';

const { Text, Paragraph } = Typography;

export interface DuplicateResolveModalProps {
  open: boolean;
  item: DuplicateItem | null;
  onClose: () => void;
  /** 处理成功后回调（刷新详情与角标） */
  onResolved: () => void;
}

export default function DuplicateResolveModal({ open, item, onClose, onResolved }: DuplicateResolveModalProps) {
  if (!item) return null;
  const reason = duplicateReasonMeta(item.reason);
  const profileA = item.profile_a;

  const run = async (action: 'merge' | 'keep') => {
    if (!item) return;
    try {
      if (action === 'merge') {
        const result = await resolveDuplicate(item.id, { action: 'merge' });
        if (result.status === 'merged') {
          const data = result as { vector_status?: string };
          if (data.vector_status === 'failed') {
            message.warning('已合并，但向量同步失败，可在本页点击"重建向量"');
          } else {
            message.success('已合并到画像 A');
          }
        }
      } else {
        const result = await resolveDuplicate(item.id, { action: 'keep' });
        if (result.status === 'kept') {
          const data = result as { talent_profile_id?: number; task_id?: number | null };
          message.success(
            `已保留为独立画像 #${data.talent_profile_id}，后台补齐中（解析队列任务 #${data.task_id ?? '—'}）`,
          );
        }
      }
      onResolved();
      onClose();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  return (
    <Modal
      title={
        <Space wrap>
          <span>确认查重：{item.parsed_resume?.filename ?? `解析 #${item.parsed_resume?.id}`}</span>
          <Tag color={reason.color}>{reason.label}</Tag>
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={640}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button onClick={() => void run('keep')}>保留为独立画像</Button>
          <Button type="primary" danger onClick={() => void run('merge')}>
            合并到画像 A
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text type="secondary">
          命中证据：<Text code>{item.matched_value ?? '—'}</Text>（已脱敏）· 发现于 {formatTime(item.created_at)}
        </Text>
        <Divider style={{ margin: '8px 0' }} />
        <Space align="start" style={{ width: '100%' }} size={24}>
          <div style={{ flex: 1 }}>
            <Paragraph strong style={{ marginBottom: 4 }}>
              新解析结果
            </Paragraph>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="姓名">{item.parsed_resume?.name ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="手机号">{item.parsed_resume?.phone ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="文件">{item.parsed_resume?.filename ?? '—'}</Descriptions.Item>
            </Descriptions>
          </div>
          <div style={{ flex: 1 }}>
            <Paragraph strong style={{ marginBottom: 4 }}>
              既有画像 A（合并目标）
            </Paragraph>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="编号">{profileA ? `#${profileA.id}` : '—'}</Descriptions.Item>
              <Descriptions.Item label="姓名">{profileA?.name ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="职位">{profileA?.current_title ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="建档时间">{profileA ? formatTime(profileA.created_at) : '—'}</Descriptions.Item>
            </Descriptions>
          </div>
        </Space>
        <Divider style={{ margin: '8px 0' }} />
        <Text type="secondary">
          合并：经历并集、标签并集、能力维度沿用画像 A，新解析结果改指画像 A（不可撤销）。
          保留：新建独立画像，标签/能力/向量由后台任务补齐。
        </Text>
      </Space>
    </Modal>
  );
}
