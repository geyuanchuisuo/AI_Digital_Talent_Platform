/**
 * 人才画像页展示口径（T-20 纯函数，便于单测）。
 *
 * 状态文案与 `backend/app/schemas/talent.py` / 数据库 §5.1 对齐；
 * 学历六档来自决策 D-G 的归一化字面值。
 */

import dayjs from 'dayjs';

import type { VectorStatus } from './types';

export const DEGREE_OPTIONS = ['博士', '硕士', '本科', '大专', '高中中专', '其他'] as const;

export const TAG_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'skill', label: '技能' },
  { value: 'industry', label: '行业' },
  { value: 'level', label: '级别' },
  { value: 'general', label: '通用' },
];

export const VECTOR_STATUS_META: Record<VectorStatus, { label: string; color: string }> = {
  pending: { label: '向量待同步', color: 'default' },
  synced: { label: '向量已同步', color: 'success' },
  failed: { label: '向量同步失败', color: 'error' },
};

export function vectorStatusMeta(status: string): { label: string; color: string } {
  return VECTOR_STATUS_META[status as VectorStatus] ?? { label: status, color: 'default' };
}

/** 年限展示：null → "—"；整数不带小数点 */
export function formatYears(years: number | null | undefined): string {
  if (years === null || years === undefined) return '—';
  return Number.isInteger(years) ? `${years} 年` : `${years} 年`;
}

/** 相似度展示为百分数（语义检索列） */
export function formatSimilarity(similarity: number): string {
  return `${Math.round(similarity * 100)}%`;
}

/** 查重 reason 文案（D-O：按命中字段细分，matched_value 已脱敏） */
export function duplicateReasonMeta(reason: string): { label: string; color: string } {
  switch (reason) {
    case 'name_phone_match':
      return { label: '姓名+手机号相同', color: 'orange' };
    case 'phone_match':
      return { label: '手机号相同', color: 'gold' };
    case 'email_match':
      return { label: '邮箱相同', color: 'purple' };
    default:
      return { label: reason, color: 'default' };
  }
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  return dayjs(value).format('YYYY-MM-DD HH:mm');
}
