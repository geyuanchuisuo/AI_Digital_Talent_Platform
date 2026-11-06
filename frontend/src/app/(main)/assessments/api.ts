/**
 * C 测评页接口封装。统一走 `lib/http.ts`，路径不含 `/api` 前缀。
 * 契约见 `docs/modules/C/C模块接口与数据契约.md` §4。
 */

import { get, patch, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  AssessmentAssignmentItem,
  AssessmentDetail,
  AssessmentPlan,
  AssessmentQuestion,
  AssessmentResultOut,
  AnswerScoreRow,
  PaginatedItems,
  QuestionPayload,
} from './types';

export interface QuestionQuery {
  status?: string;
  question_type?: string;
  keyword?: string;
  page: number;
  page_size: number;
}

export function fetchQuestions(query: QuestionQuery): Promise<PageData<AssessmentQuestion>> {
  return get<PageData<AssessmentQuestion>>('/assessments/questions', { params: query });
}

export function createQuestion(payload: QuestionPayload): Promise<AssessmentQuestion> {
  return post<AssessmentQuestion>('/assessments/questions', payload);
}

export function approveQuestion(id: number): Promise<AssessmentQuestion> {
  return post<AssessmentQuestion>(`/assessments/questions/${id}/approve`);
}

export interface PlanPayload {
  purpose: 'recruitment' | 'employee';
  job_id: number;
  job_title: string;
  total_question_count: number;
  pass_score: number;
  dimensions: { name: string; type: string; weight: number }[];
}

export function fetchPlans(params: {
  purpose?: string;
  status?: string;
  page: number;
  page_size: number;
}): Promise<PageData<AssessmentPlan>> {
  return get<PageData<AssessmentPlan>>('/assessments/plans', { params });
}

export function createPlan(payload: PlanPayload): Promise<AssessmentPlan> {
  return post<AssessmentPlan>('/assessments/plans', payload);
}

export function publishPlan(id: number): Promise<AssessmentPlan> {
  return post<AssessmentPlan>(`/assessments/plans/${id}/publish`);
}

export function fetchAssignments(params: {
  purpose?: string;
  status?: string;
  page: number;
  page_size: number;
}): Promise<PageData<AssessmentAssignmentItem>> {
  return get<PageData<AssessmentAssignmentItem>>('/assessments/assignments', { params });
}

export function assignAssessment(payload: {
  plan_id: number;
  user_id: number;
  idempotency_key: string;
}): Promise<AssessmentAssignmentItem> {
  return post<AssessmentAssignmentItem>('/assessments/assignments', payload);
}

export function fetchMyAssignments(): Promise<PaginatedItems<AssessmentAssignmentItem>> {
  return get<PaginatedItems<AssessmentAssignmentItem>>('/assessments/my/assignments');
}

export function getAssignment(id: number): Promise<AssessmentDetail> {
  return get<AssessmentDetail>(`/assessments/assignments/${id}`);
}

export function startAssignment(id: number): Promise<AssessmentAssignmentItem> {
  return post<AssessmentAssignmentItem>(`/assessments/assignments/${id}/start`);
}

export function saveAnswer(
  assignmentId: number,
  questionId: number,
  payload: { selected_option?: string; answer_text?: string },
): Promise<unknown> {
  return request({
    method: 'PUT',
    url: `/assessments/assignments/${assignmentId}/answers/${questionId}`,
    data: payload,
  });
}

export function submitAssignment(id: number): Promise<{
  assignment_id: number;
  status: string;
  failed_count: number;
  result: AssessmentResultOut | null;
}> {
  return post(`/assessments/assignments/${id}/submit`);
}

export function retryGrading(id: number): Promise<{
  assignment_id: number;
  status: string;
  retried: number;
  failed_count: number;
  result: AssessmentResultOut | null;
}> {
  return post(`/assessments/assignments/${id}/retry-grading`);
}

export function getResult(id: number): Promise<AssessmentResultOut & {
  assignment_id: number;
  purpose: string;
  review_items?: {
    question_id: number;
    is_completed: boolean;
    question_snapshot: { stem: string; correct_answer?: string; reference_points?: string };
  }[];
}> {
  return get(`/assessments/assignments/${id}/result`);
}

export function completeReviewItem(assignmentId: number, questionId: number): Promise<unknown> {
  return post(`/assessments/assignments/${assignmentId}/review-items/${questionId}/complete`);
}

export function publishResult(id: number): Promise<unknown> {
  return post(`/assessments/assignments/${id}/publish`);
}

export function openAnswers(id: number): Promise<unknown> {
  return post(`/assessments/assignments/${id}/open-answers`);
}

export function fetchAnswerScores(id: number): Promise<PaginatedItems<AnswerScoreRow>> {
  return get<PaginatedItems<AnswerScoreRow>>(`/assessments/assignments/${id}/answers`);
}

export function manualScore(
  assignmentId: number,
  questionId: number,
  payload: { score: number; reason: string },
): Promise<{ assignment_id: number; review_id: number; result: AssessmentResultOut | null }> {
  return patch(`/assessments/assignments/${assignmentId}/answers/${questionId}/score`, payload);
}
