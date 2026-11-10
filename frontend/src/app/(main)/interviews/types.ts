/** C 人工面试页数据类型。 */

export interface InterviewRound {
  id: number;
  candidate_user_id: number | null;
  talent_profile_id: number | null;
  job_id: number | null;
  job_title: string | null;
  round_no: number;
  round_name: string;
  scheduled_at: string | null;
  mode: 'onsite' | 'online' | 'phone';
  location_or_link: string | null;
  interviewer_user_ids: number[];
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  internal_note?: string | null;
  version: number;
}

export interface MyRoundItem extends InterviewRound {
  view: 'candidate' | 'interviewer';
}

export interface InterviewFeedback {
  round_id: number;
  interviewer_user_id: number;
  evaluation: string;
  evidence: string | null;
  strengths: string | null;
  weaknesses: string | null;
  recommendation: 'advance' | 'hold' | 'reject';
  version: number;
  updated_at: string;
}

export interface RoundEvent {
  event_type: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  operator_id: number | null;
  note: string | null;
  created_at: string;
}

export interface RoundDetail extends InterviewRound {
  events?: RoundEvent[];
  feedback?: InterviewFeedback | null;
}
