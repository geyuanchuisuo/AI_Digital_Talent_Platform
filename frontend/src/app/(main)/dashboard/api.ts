/**
 * 管理员工作台接口封装（T-D22）。走 `lib/http.ts`；后端契约见 docs/modules/D/工作台契约.md。
 * 权限：管理侧专用（personal 账号 403 由 http 统一提示）；六类计数为实时查询。
 */

import { get } from '@/lib/http';

export interface CardInfo {
  count: number;
  label: string;
  caliber: string;
  entry: string;
  breakdown?: Record<string, unknown>;
}

export interface AdminOverview {
  role: string;
  cards: Record<string, CardInfo>;
  data_source: string;
}

/** 卡片固定展示顺序（与需求 F09 列举一致） */
export const CARD_ORDER = ['jobs', 'profiles', 'reviews', 'scoring', 'training', 'gaps'] as const;

export function fetchAdminOverview(): Promise<AdminOverview> {
  return get<AdminOverview>('/ai-platform/admin-overview');
}
