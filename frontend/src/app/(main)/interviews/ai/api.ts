/** AI 面试间接口封装（prefix `/api/ai-interviews`）。 */

import { get, post } from '@/lib/http';

export interface AiSessionItem {
  id: number;
  candidate_user_id: number | null;
  job_id: number | null;
  application_id: number | null;
  status: 'pending' | 'in_progress' | 'completed';
  overall_score: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AiQuestionItem {
  id: number;
  sequence_no: number;
  question_text: string;
  question_type: string;
  status: 'pending' | 'graded' | 'failed';
  answer_text: string | null;
  ai_score: number | null;
  ai_feedback: { score?: number; feedback?: string } | null;
}

export function fetchSessions(params?: { candidate_user_id?: number }) {
  return get<AiSessionItem[]>('/ai-interviews/sessions', { params });
}

export function createSession(payload: {
  candidate_user_id?: number | null;
  talent_profile_id?: number | null;
  job_id?: number | null;
  application_id?: number | null;
}) {
  return post<{ id: number }>('/ai-interviews/sessions', payload);
}

export function startSession(id: number) {
  return post<{ id: number; status: string }>(`/ai-interviews/sessions/${id}/start`);
}

export function finishSession(id: number) {
  return post<{ id: number; overall_score: number | null }>(`/ai-interviews/sessions/${id}/finish`);
}

export function fetchQuestions(sessionId: number) {
  return get<AiQuestionItem[]>(`/ai-interviews/sessions/${sessionId}/questions`);
}

export function addQuestion(sessionId: number, payload: { question_text: string; question_type?: string }) {
  return post<{ id: number; sequence_no: number }>(`/ai-interviews/sessions/${sessionId}/questions`, payload);
}

export function answerQuestion(questionId: number, answerText: string) {
  return post<{ id: number; status: string; ai_score: number | null }>(
    `/ai-interviews/questions/${questionId}/answer`,
    { answer_text: answerText },
  );
}

export function retryQuestion(questionId: number) {
  return post<{ id: number; status: string; ai_score: number | null }>(
    `/ai-interviews/questions/${questionId}/retry`,
  );
}
