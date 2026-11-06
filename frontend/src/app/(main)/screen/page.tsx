/**
 * 简历智能筛选页（T-15：上传与队列区）。
 *
 * 本页只负责编排：上传完成后通知队列面板立即刷新，避免等下一次 2s 轮询。
 * 筛选批次与结果区（T-27）在队列面板下方。
 */

'use client';

import { Space } from 'antd';
import { useState } from 'react';

import QueuePanel from './QueuePanel';
import ScreenBatchPanel from './ScreenBatchPanel';
import UploadPanel from './UploadPanel';

export default function ScreenPage() {
  const [reloadSignal, setReloadSignal] = useState(0);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <UploadPanel onChanged={() => setReloadSignal((value) => value + 1)} />
      <QueuePanel reloadSignal={reloadSignal} />
      <ScreenBatchPanel />
    </Space>
  );
}
