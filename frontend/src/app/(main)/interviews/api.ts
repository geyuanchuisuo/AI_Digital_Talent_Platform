/** C 人工面试页接口封装。 */

import { get, patch, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type { InterviewFeedback, InterviewRound, MyRoundItem, RoundDetail } from './types';

export interface RoundQuery {
  status?: string;
  candidate_user_id?: number;
  page: number;
  page_size: number;
}

export function fetchRounds(query: RoundQuery): Promise<PageData<InterviewRound>> {
  return get<PageData<InterviewRound>>('/interviews/rounds', { params: query });
}

export function fetchMyRounds(): Promise<{ items: MyRoundItem[]; total: number }> {
  return get<{ items: MyRoundItem[]; total: number }>('/interviews/my/rounds');
}

export function getRound(id: number): Promise<RoundDetail> {
  return get<RoundDetail>(`/interviews/rounds/${id}`);
}

export interface RoundPayload {
  candidate_user_id?: number | null;
  talent_profile_id?: number | null;
  job_id?: number | null;
  job_title?: string;
  round_no: number;
  round_name: string;
  scheduled_at?: string | null;
  mode: 'onsite' | 'online' | 'phone';
  location_or_link?: string | null;
  interviewer_user_ids: number[];
  internal_note?: string | null;
}

export function createRound(payload: RoundPayload): Promise<InterviewRound> {
  return post<InterviewRound>('/interviews/rounds', payload);
}

export function updateRound(id: number, payload: Partial<RoundPayload> & { status?: 'pending' | 'in_progress' | 'completed' }): Promise<InterviewRound> {
  return patch<InterviewRound>(`/interviews/rounds/${id}`, payload);
}

export function cancelRound(id: number): Promise<InterviewRound> {
  return post<InterviewRound>(`/interviews/rounds/${id}/cancel`);
}

export interface FeedbackPayload {
  evaluation: string;
  evidence?: string | null;
  strengths?: string | null;
  weaknesses?: string | null;
  recommendation: 'advance' | 'hold' | 'reject';
}

export function saveFeedback(id: number, payload: FeedbackPayload): Promise<InterviewFeedback> {
  return request<InterviewFeedback>({
    method: 'PUT',
    url: `/interviews/rounds/${id}/feedback`,
    data: payload,
  });
}
