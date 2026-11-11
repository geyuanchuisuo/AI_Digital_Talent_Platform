/** C 培训成长页接口封装。 */

import { get, patch, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  GrowthEvent,
  GrowthTrajectory,
  LearningEvidence,
  TrainingAttendance,
  TrainingCourse,
  TrainingEnrollment,
  TrainingFeedbackState,
  TrainingFeedbackSummary,
  TrainingLearningRecord,
  TrainingPlan,
  PrejobPlan,
  CapabilityRating,
  CapabilityRatingChange,
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
  due_date?: string | null;
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

/** 跨域成长轨迹聚合（C13）：测评/面试/培训/正式评级四域时间线 + 维度状态（formal/assessed/未评估） */
export function fetchGrowthTrajectory(params: { employee_user_id?: number; talent_profile_id?: number }) {
  return get<GrowthTrajectory>('/training/growth/trajectory', { params });
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

/** 员工确认候补名额（24h 内），确认后进入正常审批 */
export function confirmEnrollment(enrollmentId: number) {
  return post<{ id: number; status: string; expires_at: string | null }>(
    `/training/enrollments/${enrollmentId}/confirm`,
  );
}

export function fetchEnrollments(params?: { course_id?: number }) {
  return get<TrainingEnrollment[]>('/training/enrollments', { params });
}

/** 释放超时未决定的报名占位并递补候补（管理员） */
export function releaseExpiredEnrollments() {
  return post<{ scanned: number; released: number; promoted: number }>('/training/enrollments/release-expired');
}

/** 岗前培养计划（F05，待上岗人员分阶段：目标、阶段、课程、导师，版本化） */
export interface PrejobPhaseIn {
  name: string;
  goal?: string | null;
  mentor_user_id?: number | null;
  courses: { course_id: number; note?: string | null }[];
}

export interface PrejobPlanPayload {
  employee_user_id: number;
  talent_profile_id?: number | null;
  target_job_title?: string | null;
  goal?: string | null;
  phases: PrejobPhaseIn[];
}

export function fetchPrejobPlans() {
  return get<PrejobPlan[]>('/training/prejob-plans');
}

export function createPrejobPlan(payload: PrejobPlanPayload) {
  return post<PrejobPlan>('/training/prejob-plans', payload);
}

export function assignPrejobPlan(id: number) {
  return post<PrejobPlan>(`/training/prejob-plans/${id}/assign`);
}

export function revisePrejobPlan(id: number) {
  return post<PrejobPlan>(`/training/prejob-plans/${id}/revise`);
}

/** 正式能力评级变更链（C14）：经理提交实践成果证据+经理评价 → HR 审核生成新评级版本 */
export interface CapabilityEvidenceIn {
  source_type: string;
  description: string;
  ref?: string | null;
}

export interface CapabilityRatingChangePayload {
  employee_user_id: number;
  talent_profile_id?: number | null;
  dimension_name: string;
  evidence: CapabilityEvidenceIn[];
  manager_level: number;
  manager_comment: string;
}

export function fetchCapabilityRatingChanges(params?: { status?: string }) {
  return get<CapabilityRatingChange[]>('/training/capability-rating-changes', { params });
}

export function createCapabilityRatingChange(payload: CapabilityRatingChangePayload) {
  return post<CapabilityRatingChange>('/training/capability-rating-changes', payload);
}

export function reviewCapabilityRatingChange(
  id: number,
  payload: { approve: boolean; final_level?: number | null; comment?: string | null },
) {
  return post<CapabilityRatingChange>(`/training/capability-rating-changes/${id}/review`, payload);
}

export function fetchCapabilityRatings(employeeUserId?: number) {
  return get<CapabilityRating[]>('/training/capability-ratings', {
    params: employeeUserId ? { employee_user_id: employeeUserId } : undefined,
  });
}

/** 有效学习证据与正式完成认定：分数与验证人由服务端按可信来源计算，前端不可传 */
export type LearningEvidenceType = 'attendance' | 'assessment' | 'study_session';

export function submitLearningEvidence(payload: {
  learning_record_id: number;
  evidence_type: LearningEvidenceType;
  payload?: Record<string, unknown>;
}) {
  return post<{ id: number; evidence_type: string; score: number; verified_by: string | null }>(
    '/training/learning-evidence',
    payload,
  );
}

/** 员工领取服务端签名的一次性学习会话凭证（study_session 证据前置） */
export function startLearningSession(recordId: number) {
  return post<{ learning_record_id: number; session_token: string; expires_at: string }>(
    `/training/learning-records/${recordId}/study-sessions`,
  );
}

export function confirmFormalCompletion(recordId: number) {
  return post<{ id: number; formal_completed: boolean }>(
    `/training/learning-records/${recordId}/confirm-completion`,
  );
}

export function fetchLearningEvidence(recordId: number) {
  return get<LearningEvidence[]>(`/training/learning-records/${recordId}/evidence`);
}

export function extendPlanItem(
  itemId: number,
  payload: { new_due_date: string; reason: string; manager_user_id?: number | null },
) {
  return post<{ id: number; due_date: string | null }>(`/training/plan-items/${itemId}/extension`, payload);
}

/** 管理员手动触发必修到期/逾期扫描（每日定时由 D 的调度器负责） */
export function scanTrainingReminders(payload: { today?: string; days_ahead?: number } = {}) {
  return post<{
    today: string;
    due: { scanned: number; emitted: number };
    overdue: { scanned: number; emitted: number };
  }>('/training/reminders/scan', payload);
}
