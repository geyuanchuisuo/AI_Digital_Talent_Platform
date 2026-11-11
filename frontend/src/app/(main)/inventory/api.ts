/** F10 人才盘点接口封装。 */

import { downloadFile, get, patch, post, request } from '@/lib/http';

import type { InventoryReview, InventorySummary, TalentPoolItem } from './types';

export function fetchReviews(params?: { employee_user_id?: number }) {
  return get<InventoryReview[]>('/inventory/reviews', { params });
}

export function getReview(id: number) {
  return get<InventoryReview>(`/inventory/reviews/${id}`);
}

export interface ReviewPayload {
  employee_user_id: number;
  talent_profile_id?: number | null;
  cycle_name: string;
  review_type?: 'annual' | 'ad_hoc';
  performance_score?: number | null;
  performance_source?: string;
  potential_manager_score?: number | null;
  potential_hr_score?: number | null;
  summary?: string | null;
}

export function createReview(payload: ReviewPayload) {
  return post<{ id: number }>('/inventory/reviews', payload);
}

/** C09 经理本人潜力初评（仅草稿、仅负责部门）；reviewer_manager_id 由后端记为初评经理本人 */
export function managerInitialReview(
  id: number,
  payload: { potential_manager_score: number; comment?: string | null },
) {
  return post<{ id: number; reviewer_manager_id: number | null; potential_manager_score: number }>(
    `/inventory/reviews/${id}/manager-assessment`,
    payload,
  );
}

export function updateReview(id: number, payload: Omit<ReviewPayload, 'employee_user_id' | 'cycle_name'>) {
  return patch<{ id: number; grid_cell: string | null }>(`/inventory/reviews/${id}`, payload);
}

export function submitReview(id: number) {
  return post<{ id: number; grid_cell: string | null }>(`/inventory/reviews/${id}/submit`);
}

export function confirmReview(id: number) {
  return post<{ id: number }>(`/inventory/reviews/${id}/confirm`);
}

export function fetchSummary() {
  return get<InventorySummary>('/inventory/summary');
}

/** 管理员导出匿名汇总 CSV（backend 写 summary_exported 审计）；cycle_name 可选过滤 */
export function downloadSummary(cycleName?: string) {
  const query = cycleName ? `?cycle_name=${encodeURIComponent(cycleName)}` : '';
  return downloadFile(`/inventory/summary/export${query}`, 'inventory-summary.csv');
}

export function fetchPools() {
  return get<TalentPoolItem[]>('/inventory/pools');
}

export function nominatePool(payload: { employee_user_id: number; pool_type: string; note?: string | null }) {
  return post<{ id: number }>('/inventory/pools', payload);
}

export function decidePool(id: number, payload: { status: 'approved' | 'rejected' | 'removed'; note?: string | null }) {
  return post<{ id: number; status: string }>(`/inventory/pools/${id}/decide`, payload);
}

export function createDispute(reviewId: number, content: string) {
  return post<{ id: number }>(`/inventory/reviews/${reviewId}/disputes`, { content });
}

export function respondDispute(disputeId: number, payload: { status: 'resolved' | 'rejected'; response: string }) {
  return post<{ id: number; status: string }>(`/inventory/disputes/${disputeId}/respond`, payload);
}

export function upsertIdp(
  reviewId: number,
  payload: { content_json: Record<string, unknown>; status?: 'draft' | 'confirmed' },
) {
  return request<{ id: number; status: string }>({
    method: 'PUT',
    url: `/inventory/reviews/${reviewId}/idp`,
    data: payload,
  });
}

/** 九宫格标准配置版本（F05a）：阈值须经 HR 批准后方可产出正式评级 */
export interface GridConfig {
  id: number;
  version: number;
  status: string;
  job_family: string | null;
  performance_bands: { hi_min: number; mid_min: number };
  potential_bands: { hi_min: number; mid_min: number };
  labels: Record<string, unknown>;
  note: string | null;
  approved_by: number | null;
  approved_at: string | null;
}

export function fetchGridConfigs() {
  return get<GridConfig[]>('/inventory/grid-configs');
}

export function createGridConfig(payload: {
  job_family?: string | null;
  performance_bands: { hi_min: number; mid_min: number };
  potential_bands: { hi_min: number; mid_min: number };
  labels?: Record<string, unknown> | null;
  note?: string | null;
}) {
  return post<{ id: number; version: number; status: string }>('/inventory/grid-configs', payload);
}

export function approveGridConfig(id: number) {
  return post<{ id: number; version: number; status: string }>(`/inventory/grid-configs/${id}/approve`);
}
