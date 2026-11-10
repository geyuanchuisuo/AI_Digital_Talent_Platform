/**
 * 个人工作台接口封装（T-D23）。走 `lib/http.ts`；后端契约见 docs/modules/D/工作台契约.md。
 * 权限：仅 personal 账号（后端 403，personal 侧请求由 http 统一提示）。
 */

import { get } from '@/lib/http';

export interface MeOverview {
  user_id: number;
  pending_assignments: number;
  pending_training: number;
  recent_results: number;
  caliber: Record<string, string>;
}

export function fetchMeOverview(): Promise<MeOverview> {
  return get<MeOverview>('/ai-platform/me-overview');
}
