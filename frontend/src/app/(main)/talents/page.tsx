/**
 * 人才画像页（T-20：列表 + 回收站 + 查重抽屉）。
 *
 * 本页只负责编排：列表动作（恢复/查重处理）后同步刷新角标。
 * 详情页 `/talents/[id]` 由 T-21 提供。
 */

'use client';

import { Space } from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { fetchPendingDuplicateCount } from './api';
import DuplicatesDrawer from './DuplicatesDrawer';
import TalentListPanel from './TalentListPanel';

export default function TalentsPage() {
  const [duplicateCount, setDuplicateCount] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [reloadSignal, setReloadSignal] = useState(0);

  const loadCount = useCallback(async () => {
    try {
      setDuplicateCount(await fetchPendingDuplicateCount());
    } catch {
      // 错误提示由 lib/http 统一弹；角标维持当前值
    }
  }, []);

  useEffect(() => {
    void loadCount();
  }, [loadCount, reloadSignal]);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <TalentListPanel
        duplicateCount={duplicateCount}
        onOpenDuplicates={() => setDrawerOpen(true)}
        onChanged={() => setReloadSignal((value) => value + 1)}
      />
      <DuplicatesDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onChanged={() => setReloadSignal((value) => value + 1)}
      />
    </Space>
  );
}
