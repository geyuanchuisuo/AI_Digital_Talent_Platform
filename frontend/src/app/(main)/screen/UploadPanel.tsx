/**
 * 批量上传面板（T-15，需求 F1-1）。
 *
 * 交互与口径：
 * 1. 拖拽/点选多文件，**先攒列表再一次性提交**（`beforeUpload` 返回 false），
 *    用户可删掉误选的文件；字段名固定 `files`。
 * 2. 响应是 202 + 三块：`accepted` / `duplicates` / `rejected`。重复**不是错误**——
 *    `resume_files` 行永不物理删除，重传必然命中哈希；后端按 `can_requeue` 告知
 *    哪些原任务可重新入队（`cancelled`/`failed`），这里据此渲染按钮。
 * 3. 重新入队走 T-14 的 `POST /resumes/queue/{id}/requeue`（清空 stage 从头重跑），
 *    成功后置按钮为"已重新入队"并通知父组件刷新队列。
 */

'use client';

import { InboxOutlined } from '@ant-design/icons';
import { Alert, Button, Card, List, Space, Tag, Typography, Upload, message } from 'antd';
import type { UploadFile } from 'antd';
import { useState } from 'react';

import { requeueTask, uploadBatch } from './api';
import type { BatchUploadData, DuplicateItem } from './types';
import { formatBatchId, statusMeta } from './utils';

const { Dragger } = Upload;

export interface UploadPanelProps {
  /** 上传成功 / 重新入队后通知父组件刷新队列与批次下拉 */
  onChanged: () => void;
}

export default function UploadPanel({ onChanged }: UploadPanelProps) {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<BatchUploadData | null>(null);
  const [requeuing, setRequeuing] = useState<number | null>(null);
  const [requeued, setRequeued] = useState<number[]>([]);

  const handleUpload = async () => {
    const files = fileList.flatMap((item) => (item.originFileObj ? [item.originFileObj] : []));
    if (files.length === 0) {
      message.warning('请先选择简历文件');
      return;
    }

    setUploading(true);
    try {
      const data = await uploadBatch(files);
      setResult(data);
      setRequeued([]);
      setFileList([]);
      message.success(`已受理 ${data.accepted} 份简历，当前待处理 ${data.queue_total} 个任务`);
      onChanged();
    } catch {
      // 统一错误提示由 lib/http 拦截器负责，避免重复弹 toast
    } finally {
      setUploading(false);
    }
  };

  const handleRequeue = async (item: DuplicateItem) => {
    if (!item.task_id) return;
    setRequeuing(item.task_id);
    try {
      await requeueTask(item.task_id);
      setRequeued((ids) => [...ids, item.task_id as number]);
      message.success(`「${item.filename}」已重新入队，将从头重跑`);
      onChanged();
    } catch {
      // 同上
    } finally {
      setRequeuing(null);
    }
  };

  return (
    <Card title="批量上传简历">
      <Dragger
        multiple
        accept=".pdf,.docx"
        fileList={fileList}
        beforeUpload={() => false}
        onChange={({ fileList: next }) => setFileList(next)}
        disabled={uploading}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽简历到此处</p>
        <p className="ant-upload-hint">
          支持 PDF / DOCX，单文件 ≤20MB；内容已存在过的文件会被自动识别，不会重复入库
        </p>
      </Dragger>

      <Space style={{ marginTop: 12 }} wrap>
        <Button type="primary" loading={uploading} onClick={handleUpload}>
          上传并解析
        </Button>
        <Typography.Text type="secondary">
          {fileList.length > 0 ? `已选 ${fileList.length} 个文件` : '上传后自动进入解析队列'}
        </Typography.Text>
      </Space>

      {result && (
        <Alert
          style={{ marginTop: 16 }}
          type="info"
          showIcon
          message={
            <Space size="middle" wrap>
              <span>本批次新入队 {result.accepted} 份</span>
              <Typography.Text
                copyable={{ text: result.upload_batch_id }}
                style={{ fontFamily: 'monospace' }}
              >
                批次 {formatBatchId(result.upload_batch_id)}
              </Typography.Text>
              {result.duplicates.length > 0 && (
                <Tag color="warning">重复 {result.duplicates.length}</Tag>
              )}
              {result.rejected.length > 0 && <Tag color="error">拒绝 {result.rejected.length}</Tag>}
            </Space>
          }
          description={
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {result.duplicates.length > 0 && (
                <div>
                  <Typography.Text type="secondary">
                    重复文件（内容已存在，未重复入库；可对失败/已取消的原任务重新入队）
                  </Typography.Text>
                  <List
                    size="small"
                    dataSource={result.duplicates}
                    renderItem={(item) => {
                      const taskId = item.task_id;
                      const done = taskId !== null && requeued.includes(taskId);
                      const canRequeue = Boolean(item.can_requeue && taskId);
                      return (
                        <List.Item
                          actions={
                            canRequeue
                              ? [
                                  <Button
                                    key="requeue"
                                    type="link"
                                    size="small"
                                    loading={taskId !== null && requeuing === taskId}
                                    disabled={done}
                                    onClick={() => handleRequeue(item)}
                                  >
                                    {done ? '已重新入队' : '重新入队'}
                                  </Button>,
                                ]
                              : undefined
                          }
                        >
                          <Space wrap>
                            <span>{item.filename}</span>
                            {item.task_status && (
                              <Tag>{statusMeta(item.task_status).label}</Tag>
                            )}
                          </Space>
                        </List.Item>
                      );
                    }}
                  />
                </div>
              )}

              {result.rejected.length > 0 && (
                <div>
                  <Typography.Text type="secondary">被拒绝的文件</Typography.Text>
                  <List
                    size="small"
                    dataSource={result.rejected}
                    renderItem={(item) => (
                      <List.Item>
                        <Space wrap>
                          <span>{item.filename}</span>
                          <Typography.Text type="danger">{item.reason}</Typography.Text>
                        </Space>
                      </List.Item>
                    )}
                  />
                </div>
              )}
            </Space>
          }
        />
      )}
    </Card>
  );
}
