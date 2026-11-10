/**
 * 通知中心接口封装（T-D24）。走 `lib/http.ts`；后端契约见 docs/modules/D/通知契约.md。
 * 权限：个人仅本人通知；管理侧（can_view_all_persons）可查全量/按收件人过滤（403 由 http 统一提示）。
 * 已读仅收件人本人；failed 的 failure_reason 仅 failed 状态回显。
 */

import { get, post } from '@/lib/http';
import type { PageData } from '@/lib/types';

export type NotificationStatus = 'pending' | 'sent' | 'failed' | 'read';

export interface NotificationItem {
  id: number;
  event_type: string;
  ref_type: string | null;
  ref_id: number | null;
  actor_user_id: number | null;
  recipient_user_id: number;
  payload: Record<string, unknown> | null;
  status: NotificationStatus;
  failure_reason: string | null;
  created_at: string | null;
  sent_at: string | null;
  read_at: string | null;
}

export function fetchNotifications(params: {
  page?: number;
  page_size?: number;
  status?: NotificationStatus | '';
  recipient_user_id?: number;
}): Promise<PageData<NotificationItem>> {
  const { status, ...rest } = params;
  return get<PageData<NotificationItem>>('/notifications', {
    params: { ...rest, status: status || undefined },
  });
}

export function fetchEventTypes(): Promise<string[]> {
  return get<{ event_types: string[]; note: string }>('/notifications/meta/event-types').then(
    (resp) => resp?.event_types ?? [],
  );
}

export function markNotificationRead(id: number): Promise<NotificationItem> {
  return post<NotificationItem>(`/notifications/${id}/read`, {});
}

export const STATUS_TAG: Record<NotificationStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '待投递' },
  sent: { color: 'green', text: '已投递' },
  failed: { color: 'red', text: '投递失败' },
  read: { color: 'blue', text: '已读' },
};

/** 事件类型中文标签（14 枚举封闭清单，契约 §1） */
export const EVENT_LABELS: Record<string, string> = {
  'recruitment.stage_changed': '招聘阶段变更',
  'interview.invited': '面试邀约',
  'interview.rescheduled': '面试改期',
  'interview.cancelled': '面试取消',
  'course.assigned': '课程指派',
  'course.enrollment_reviewed': '报名审批结果',
  'course.waitlist_promoted': '候补递补',
  'training.due_reminder': '必修课到期提醒',
  'training.overdue': '必修课逾期',
  'training.extension_decided': '必修课延期决定',
  'workflow.approval_pending': '审批待办',
  'workflow.run_failed': '流程运行失败',
  'workflow.needs_human': '流程需人工介入',
  'talent.gap_alert': '人才缺口预警',
};

export function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType;
}
