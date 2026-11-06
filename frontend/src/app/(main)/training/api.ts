/** C 培训成长页接口封装。 */

import { get, patch, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  GrowthEvent,
  TrainingCourse,
  TrainingLearningRecord,
  TrainingPlan,
} from './types';

export function fetchCourses(params: { keyword?: string; status?: string; page: number; page_size: number }) {
  return get<PageData<TrainingCourse>>('/training/courses', { params });
}

export function createCourse(payload: {
  title: string;
  description?: string | null;
  material_url: string;
  dimension_names?: string[];
  status?: string;
}) {
  return post<TrainingCourse>('/training/courses', payload);
}

export function updateCourse(
  id: number,
  payload: Partial<{ title: string; description: string | null; material_url: string; dimension_names: string[]; status: string }>,
) {
  return patch<TrainingCourse>(`/training/courses/${id}`, payload);
}

export interface PlanItemPayload {
  course_id: number;
  dimension_name?: string | null;
  reason?: string | null;
}

export function fetchPlans(params: { employee_user_id?: number; status?: string; page: number; page_size: number }) {
  return get<PageData<TrainingPlan>>('/training/plans', { params });
}

export function createPlan(payload: {
  employee_user_id: number;
  talent_profile_id?: number | null;
  job_id?: number | null;
  target_job_title?: string | null;
  items: PlanItemPayload[];
  gap_summary?: string | null;
}) {
  return post<TrainingPlan>('/training/plans', payload);
}

export function getPlan(id: number) {
  return get<TrainingPlan>(`/training/plans/${id}`);
}

export function updatePlan(
  id: number,
  payload: { target_job_title?: string | null; items?: PlanItemPayload[]; gap_summary?: string | null },
) {
  return patch<TrainingPlan>(`/training/plans/${id}`, payload);
}

export function assignPlan(id: number) {
  return post<TrainingPlan>(`/training/plans/${id}/assign`);
}

export function revisePlan(id: number) {
  return post<TrainingPlan>(`/training/plans/${id}/revise`);
}

export function draftPlan(payload: {
  employee_user_id: number;
  talent_profile_id?: number | null;
  job_id?: number | null;
  target_job_title?: string | null;
  dimension_gaps?: string[];
  assessment_assignment_id?: number | null;
  profile_summary?: string | null;
}) {
  return post<{
    generated: boolean;
    reason: string | null;
    gap_message: string | null;
    plan: TrainingPlan | null;
  }>('/training/plans/draft', payload);
}

export function draftFromAssessment(assignmentId: number) {
  return post<{
    generated: boolean;
    reason: string | null;
    gap_message: string | null;
    plan: TrainingPlan | null;
  }>(`/training/plans/draft-from-assessment/${assignmentId}`);
}

export function fetchMyPlans() {
  return get<{ items: TrainingPlan[]; total: number }>('/training/my/plans');
}

export function updateProgress(itemId: number, payload: { status: string; note?: string | null }) {
  return request<TrainingLearningRecord>({
    method: 'PUT',
    url: `/training/plan-items/${itemId}/progress`,
    data: payload,
  });
}

export function fetchGrowth(params: { employee_user_id?: number; page: number; page_size: number }) {
  return get<PageData<GrowthEvent>>('/training/growth', { params });
}
