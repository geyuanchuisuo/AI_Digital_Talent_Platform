/** F10 人才盘点接口封装。 */

import { get, patch, post, request } from '@/lib/http';

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
  reviewer_manager_id?: number | null;
  reviewer_hr_id?: number | null;
  summary?: string | null;
}

export function createReview(payload: ReviewPayload) {
  return post<{ id: number }>('/inventory/reviews', payload);
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
