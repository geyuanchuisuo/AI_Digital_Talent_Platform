/**
 * 简历筛选页展示口径（T-15 纯函数，便于单测）。
 *
 * 状态/阶段文案与 `backend/app/service/queue_service.py`、`parse_worker.py` 对齐：
 * - 六状态：pending / running / success / failed / duplicate_pending / cancelled
 * - 三阶段：extract（提取文本）→ profile（生成画像）→ vector（同步向量）
 *
 * 按钮显隐**不在这里重算**：后端已在每个 item 上返回 `can_retry` / `can_requeue`，
 * 前端直接读，避免两套规则漂移。只有"移除"没有对应字段，按接口 §4 的可删状态判断
 * （running 400、已 cancelled 再删 404）。
 */

import dayjs from 'dayjs';

import type { QueueStatus, QueueSummary } from './types';

export const STATUS_META: Record<QueueStatus, { label: string; color: string }> = {
  pending: { label: '待处理', color: 'default' },
  running: { label: '解析中', color: 'processing' },
  success: { label: '成功', color: 'success' },
  failed: { label: '失败', color: 'error' },
  duplicate_pending: { label: '查重待确认', color: 'warning' },
  cancelled: { label: '已取消', color: 'default' },
};

/** 状态筛选 Tab；`all` 不是接口参数，请求时省略 status */
export const STATUS_TABS: { key: QueueStatus | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'running', label: '解析中' },
  { key: 'success', label: '成功' },
  { key: 'failed', label: '失败' },
  { key: 'duplicate_pending', label: '查重待确认' },
  { key: 'cancelled', label: '已取消' },
];

const STAGE_LABELS: Record<string, string> = {
  extract: '提取文本',
  profile: '生成画像',
  vector: '同步向量',
};

export function statusMeta(status: string): { label: string; color: string } {
  return STATUS_META[status as QueueStatus] ?? { label: status, color: 'default' };
}

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '—';
  return STAGE_LABELS[stage] ?? stage;
}

/** 接口 §4：running 拒绝、已 cancelled 视为不在活动队列；其余可移除（软取消） */
export function canDeleteStatus(status: string): boolean {
  return status !== 'running' && status !== 'cancelled';
}

/** summary 计数：`all` 取 total，其余按状态字段（summary 只按批次收窄，不受 status 影响） */
export function summaryCount(
  summary: QueueSummary | undefined,
  key: keyof QueueSummary | 'all',
): number {
  if (!summary) return 0;
  return key === 'all' ? summary.total : summary[key];
}

/** 26 位 ULID 缩略展示，避免把表格/下拉撑开 */
export function formatBatchId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.length <= 12 ? id : `${id.slice(0, 8)}…${id.slice(-4)}`;
}

/** 后端返回无时区的 ISO 字符串，dayjs 按本地时区解析即可 */
export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : '—';
}
