/**
 * 查重待确认抽屉（T-20：角标的"入口"落地处，需求 F2-2）。
 *
 * 口径：
 * 1. 列表读 `GET /talents/duplicates?status=pending`；`matched_value` / 手机号
 *    均为后端脱敏值，直接展示。
 * 2. **merge 的 master 固定为画像 A**（接口 §5：传其他值 400），不提供选择；
 *    merge 是同步操作，响应带 `vector_status`（失败仍 200，半写提示交给 message）。
 * 3. keep 返回 202：新画像由 runner 后台补齐标签/向量，进度在解析队列页可见；
 *    提示文案明确"后台补齐中"。
 * 4. 处理完一条立即刷新列表与角标计数（onChanged 回调）。
 */

'use client';

import { Alert, Badge, Button, Drawer, Empty, Input, List, Modal, Space, Tag, Typography, message } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { fetchDuplicates, resolveDuplicate, undoMerge } from './api';
import type { DuplicateItem, KeepResolveData, MergeResolveData } from './types';
import { duplicateReasonMeta, formatTime } from './utils';

const { Text } = Typography;

export interface DuplicatesDrawerProps {
  open: boolean;
  onClose: () => void;
  /** 处理完一条后通知父组件刷新角标与列表 */
  onChanged: () => void;
}

export default function DuplicatesDrawer({ open, onClose, onChanged }: DuplicatesDrawerProps) {
  const [items, setItems] = useState<DuplicateItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<number | null>(null);
  const [view,setView]=useState<'pending'|'merged'>('pending');
  const [undoItem,setUndoItem]=useState<DuplicateItem|null>(null);
  const [reason,setReason]=useState('');
  const [error,setError]=useState('');
  const generation=useRef(0);

  const load = useCallback(async () => {
    if (!open) return;
    const current=++generation.current;
    setLoading(true);
    setError('');setItems([]);
    try {
      const data = await fetchDuplicates(1, 50, view);
      if(current===generation.current)setItems(data.items);
    } catch (error) {
      if(current===generation.current)setError(getErrorMessage(error));
    } finally {
      if(current===generation.current)setLoading(false);
    }
  }, [open,view]);

  useEffect(() => {
    void load();
    return ()=>{generation.current++;};
  }, [load]);

  useEffect(()=>{if(!open){setUndoItem(null);setReason('');}},[open]);

  const handleUndo=async()=>{
    if(!undoItem||!reason.trim())return;
    setActing(undoItem.id);setError('');
    try {
      const result=await undoMerge(undoItem.id,reason.trim());
      if(result.vector_status==='failed')message.warning('已撤销合并，向量同步失败，可在档案详情重建向量');
      else message.success('已撤销合并，简历回到待确认');
      setUndoItem(null);setReason('');onChanged();void load();
    } catch(e){setError(getErrorMessage(e));}
    finally{setActing(null);}
  };

  const handleResolve = useCallback(
    (item: DuplicateItem, action: 'merge' | 'keep') => {
      if (action === 'merge') {
        Modal.confirm({
          title: '合并到既有画像？',
          content: (
            <Space direction="vertical">
              <Text>
                新简历「{item.parsed_resume?.filename ?? `解析 #${item.parsed_resume?.id}`}」将并入画像 A
                （{item.profile_a?.name}），经历取并集、能力维度沿用画像 A 原值。
              </Text>
              <Text type="secondary">合并保留原件与历史。若合并后没有其他修改，可在已合并记录中申请撤销。</Text>
            </Space>
          ),
          okText: '确认合并',
          cancelText: '取消',
          okButtonProps: { danger: true },
          onOk: async () => {
            setActing(item.id);
            try {
              const result = (await resolveDuplicate(item.id, { action: 'merge' })) as MergeResolveData;
              if (result.vector_status === 'failed') {
                message.warning('已合并，但向量同步失败，可在画像详情页点击"重建向量"');
              } else {
                message.success('已合并到画像 A');
              }
              onChanged();
              void load();
            } catch (error) {
              message.error(getErrorMessage(error));
            } finally {
              setActing(null);
            }
          },
        });
        return;
      }

      Modal.confirm({
        title: '保留为独立画像？',
        content: (
          <Space direction="vertical">
            <Text>为新简历创建独立画像，标签/能力/向量由后台任务补齐（可在解析队列看进度）。</Text>
          </Space>
        ),
        okText: '确认保留',
        cancelText: '取消',
        onOk: async () => {
          setActing(item.id);
          try {
            const result = (await resolveDuplicate(item.id, { action: 'keep' })) as KeepResolveData;
            message.success(`已保留为独立画像 #${result.talent_profile_id}，后台补齐中`);
            onChanged();
            void load();
          } catch (error) {
            message.error(getErrorMessage(error));
          } finally {
            setActing(null);
          }
        },
      });
    },
    [load, onChanged],
  );

  return (
    <Drawer
      title={
        <Space>
          <span>查重待确认</span>
          <Badge count={items.length} color="#d48806" />
        </Space>
      }
      width={560}
      open={open}
      onClose={onClose}
    >
      <Space style={{marginBottom:16}}>
        <Button type={view==='pending'?'primary':'default'} disabled={acting!==null} onClick={()=>setView('pending')}>待确认记录</Button>
        <Button type={view==='merged'?'primary':'default'} disabled={acting!==null} onClick={()=>setView('merged')}>已合并记录</Button>
      </Space>
      {view==='merged'&&<Alert type="info" message="仅支持有完整日志且合并后未再修改的记录。旧记录需人工核查。"/>}
      {error&&<Alert type="warning" message={error} showIcon/>}
      <Modal title="撤销合并" open={undoItem!==null} okText="确认撤销" cancelText="取消"
        confirmLoading={acting!==null} okButtonProps={{danger:true,disabled:!reason.trim()||acting!==null}}
        onCancel={()=>{if(acting===null)setUndoItem(null);}} onOk={()=>void handleUndo()}>
        <Typography.Paragraph>撤销会恢复合并前档案并将简历放回待确认；后续修改不会被覆盖。</Typography.Paragraph>
        <label htmlFor="merge-undo-reason">撤销原因</label>
        <Input.TextArea id="merge-undo-reason" maxLength={500} value={reason} onChange={e=>setReason(e.target.value)}/>
      </Modal>
      <List
        loading={loading}
        locale={{ emptyText: <Empty description={view==='pending'?'暂无待确认的重复简历':'暂无已合并记录'} /> }}
        dataSource={items}
        renderItem={(item) => {
          const reason = duplicateReasonMeta(item.reason);
          return (
            <List.Item
              actions={view==='merged'?[
                <Button key="undo" danger disabled={acting!==null} onClick={()=>{setUndoItem(item);setReason('');setError('');}}>撤销合并</Button>,
              ]:[
                <Button
                  key="merge"
                  size="small"
                  type="primary"
                  danger
                  loading={acting === item.id}
                  onClick={() => handleResolve(item, 'merge')}
                >
                  合并
                </Button>,
                <Button
                  key="keep"
                  size="small"
                  loading={acting === item.id}
                  onClick={() => handleResolve(item, 'keep')}
                >
                  保留
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space wrap size={4}>
                    <Tag color={reason.color}>{reason.label}</Tag>
                    <Text strong>{item.parsed_resume?.filename ?? `解析 #${item.parsed_resume?.id}`}</Text>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={0}>
                    <Text type="secondary">
                      命中值：<Text code>{item.matched_value ?? '—'}</Text>（已脱敏）
                    </Text>
                    <Text type="secondary">
                      既有画像 A：{item.profile_a ? `#${item.profile_a.id} ${item.profile_a.name}` : '—'}
                      {item.profile_a?.current_title ? ` · ${item.profile_a.current_title}` : ''}
                    </Text>
                    <Text type="secondary">发现于 {formatTime(item.created_at)}</Text>
                  </Space>
                }
              />
            </List.Item>
          );
        }}
      />
    </Drawer>
  );
}
