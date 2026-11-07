/** C 培训成长页接口封装。 */

import { get, patch, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  GrowthEvent,
  LearningEvidence,
  TrainingAttendance,
  TrainingCourse,
  TrainingEnrollment,
  TrainingFeedbackState,
  TrainingFeedbackSummary,
  TrainingLearningRecord,
  TrainingPlan,
  TrainingSession,
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

/** 期次与出勤（管理员） */
export function createSession(courseId: number, payload: { instructor_name?: string | null; starts_at: string; ends_at: string }) {
  return post<TrainingSession>(`/training/courses/${courseId}/sessions`, payload);
}

export function fetchSessions(courseId: number) {
  return get<{ items: TrainingSession[]; total: number }>(`/training/courses/${courseId}/sessions`);
}

export function markAttendance(sessionId: number, employeeUserId: number) {
  return post<TrainingAttendance>(`/training/sessions/${sessionId}/attendance`, {
    employee_user_id: employeeUserId,
  });
}

export function fetchAttendance(sessionId: number) {
  return get<{ items: TrainingAttendance[]; total: number }>(`/training/sessions/${sessionId}/attendance`);
}

/** 员工评价：本人状态、提交/修改、撤回；窗口由后端校验 */
export function fetchMyFeedback(courseId: number) {
  return get<TrainingFeedbackState>(`/training/courses/${courseId}/feedback/mine`);
}

export interface FeedbackPayload {
  content_score: number;
  instructor_score: number;
  comment?: string | null;
}

export function submitFeedback(courseId: number, payload: FeedbackPayload) {
  return post<TrainingFeedbackState>(`/training/courses/${courseId}/feedback`, payload);
}

export function updateFeedback(courseId: number, payload: FeedbackPayload) {
  return patch<TrainingFeedbackState>(`/training/courses/${courseId}/feedback`, payload);
}

export function withdrawFeedback(courseId: number) {
  return post<TrainingFeedbackState>(`/training/courses/${courseId}/feedback/withdraw`);
}

/** 匿名汇总与主题归纳（管理员，样本门槛由后端控制） */
export function fetchFeedbackSummary(courseId: number) {
  return get<TrainingFeedbackSummary>(`/training/courses/${courseId}/feedback/summary`);
}

export function refreshFeedbackThemes(courseId: number) {
  return post<{ generated: boolean; reason: string | null; comment_count: number; theme_count: number }>(
    `/training/courses/${courseId}/feedback-themes/refresh`,
  );
}

export function updateFeedbackNote(courseId: number, note: string) {
  return request<TrainingCourse>({
    method: 'PUT',
    url: `/training/courses/${courseId}/feedback-note`,
    data: { note },
  });
}

/** 报名审批/候补（24h 未审批自动释放由后端处理） */
export function applyEnrollment(payload: { course_id: number; priority: 'required' | 'elective' }) {
  return post<{ id: number; status: string; expires_at: string | null }>('/training/enrollments', payload);
}

export function decideEnrollment(
  enrollmentId: number,
  payload: { status: 'approved' | 'rejected' | 'cancelled'; note?: string | null },
) {
  return post<{ id: number; status: string }>(`/training/enrollments/${enrollmentId}/decide`, payload);
}

export function fetchEnrollments(params?: { course_id?: number }) {
  return get<TrainingEnrollment[]>('/training/enrollments', { params });
}

/** 有效学习证据与正式完成认定 */
export function submitLearningEvidence(payload: {
  learning_record_id: number;
  evidence_type: string;
  payload: Record<string, unknown>;
  score?: number;
}) {
  return post<{ id: number; score: number }>('/training/learning-evidence', payload);
}

export function confirmFormalCompletion(recordId: number) {
  return post<{ id: number; formal_completed: boolean }>(
    `/training/learning-records/${recordId}/confirm-completion`,
  );
}

export function fetchLearningEvidence(recordId: number) {
  return get<LearningEvidence[]>(`/training/learning-records/${recordId}/evidence`);
}
