import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PageData } from '@/lib/types';

import NotificationsPage from './page';
import { fetchEventTypes, fetchNotifications, markNotificationRead } from './api';
import type { NotificationItem } from './api';

vi.mock('./api', () => ({
  fetchNotifications: vi.fn(),
  fetchEventTypes: vi.fn(),
  markNotificationRead: vi.fn(),
  STATUS_TAG: {
    pending: { color: 'default', text: '待投递' },
    sent: { color: 'green', text: '已投递' },
    failed: { color: 'red', text: '投递失败' },
    read: { color: 'blue', text: '已读' },
  },
  EVENT_LABELS: {},
  eventLabel: (t: string) => t,
}));

const fetchNotificationsMock = vi.mocked(fetchNotifications);
const fetchEventTypesMock = vi.mocked(fetchEventTypes);
const markReadMock = vi.mocked(markNotificationRead);

function item(partial: Partial<NotificationItem>): NotificationItem {
  return {
    id: 1,
    event_type: 'workflow.run_failed',
    ref_type: 'workflow_run',
    ref_id: 9,
    actor_user_id: null,
    recipient_user_id: 1,
    payload: null,
    status: 'sent',
    failure_reason: null,
    created_at: '2026-11-09T12:00:00',
    sent_at: '2026-11-09T12:00:01',
    read_at: null,
    ...partial,
  };
}

function pageData(items: NotificationItem[]): PageData<NotificationItem> {
  return { items, total: items.length, page: 1, page_size: 10 };
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchEventTypesMock.mockResolvedValue([
    'workflow.approval_pending',
    'workflow.run_failed',
  ]);
  fetchNotificationsMock.mockResolvedValue(pageData([]));
});

describe('NotificationsPage', () => {
  it('渲染通知列表（事件标签/状态/关联对象）', async () => {
    fetchNotificationsMock.mockResolvedValue(
      pageData([
        item({ id: 1, status: 'sent' }),
        item({ id: 2, event_type: 'talent.gap_alert', ref_type: 'project_demand', ref_id: 3, status: 'failed', failure_reason: '外发通道不可用' }),
        item({ id: 3, status: 'read', read_at: '2026-11-09T13:00:00' }),
      ]),
    );
    render(<NotificationsPage />);
    await waitFor(() => expect(fetchNotificationsMock).toHaveBeenCalled());
    // 事件列：两行同为 run_failed 时用行内查询避免多元素歧义
    const rows = await screen.findAllByText('workflow.run_failed');
    expect(rows.length >= 1).toBeTruthy();
    expect(screen.getByText('project_demand#3')).toBeTruthy();
    // 失败原因列表不展示（详情抽屉展示），列表只有状态 Tag
    expect(screen.queryByText('外发通道不可用')).not.toBeTruthy();
    // 已读行不再显示"标记已读"
    expect(screen.queryByTestId('read-3')).not.toBeTruthy();
    expect(screen.getByTestId('read-1')).toBeTruthy();
  });

  it('标记已读调用接口并刷新列表', async () => {
    fetchNotificationsMock.mockResolvedValue(pageData([item({ id: 7, status: 'sent' })]));
    markReadMock.mockResolvedValue(item({ id: 7, status: 'read' }));
    render(<NotificationsPage />);
    const btn = await screen.findByTestId('read-7');
    fireEvent.click(btn);
    await waitFor(() => expect(markReadMock).toHaveBeenCalledWith(7));
    await waitFor(() =>
      expect(fetchNotificationsMock).toHaveBeenCalledTimes(2),
    );
  });

  it('状态过滤变更触发重新加载', async () => {
    render(<NotificationsPage />);
    await screen.findByTestId('status-filter');
    fireEvent.mouseDown(screen.getByTestId('status-filter').querySelector('.ant-select-selector')!);
    const option = await screen.findByText('投递失败');
    fireEvent.click(option);
    await waitFor(() =>
      expect(fetchNotificationsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: 'failed' }),
      ),
    );
  });

  it('收件人过滤：非正整数拒绝，合法值传入接口', async () => {
    render(<NotificationsPage />);
    await screen.findByTestId('status-filter');
    const input = screen.getByPlaceholderText('收件人 ID（管理侧）');
    const btn = screen.getByTestId('recipient-filter-btn');
    fireEvent.change(input, { target: { value: 'abc' } });
    fireEvent.click(btn);
    expect(fetchNotificationsMock).toHaveBeenCalledTimes(1); // 未追加调用
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(fetchNotificationsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ recipient_user_id: 5, page: 1 }),
      ),
    );
  });

  it('详情抽屉展示 payload 与失败原因', async () => {
    fetchNotificationsMock.mockResolvedValue(
      pageData([
        item({
          id: 9,
          status: 'failed',
          failure_reason: 'webhook 500',
          payload: { run_id: 42, reason: '节点越权' },
        }),
      ]),
    );
    render(<NotificationsPage />);
    fireEvent.click(await screen.findByTestId('detail-9'));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText(/webhook 500/)).toBeTruthy();
    expect(within(drawer).getByText(/节点越权/)).toBeTruthy();
  });
});
