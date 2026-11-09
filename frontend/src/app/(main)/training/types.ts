/** C 培训成长页数据类型。 */

export interface TrainingCourse {
  id: number;
  title: string;
  description: string | null;
  material_url: string;
  dimension_names: string[];
  capacity: number | null;
  status: 'active' | 'inactive';
  version: number;
  feedback_note: string | null;
  feedback_note_at: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface TrainingSession {
  id: number;
  course_id: number;
  instructor_name: string | null;
  starts_at: string;
  ends_at: string;
  created_at: string;
}

export interface TrainingAttendance {
  id: number;
  session_id: number;
  employee_user_id: number;
  attended_at: string;
}

export interface TrainingFeedback {
  id: number;
  course_id: number;
  session_id: number;
  content_score: number;
  instructor_score: number;
  comment: string | null;
  status: 'submitted' | 'withdrawn';
  submitted_at: string;
  withdrawn_at: string | null;
  updated_at: string;
}

export interface TrainingFeedbackState {
  attended: boolean;
  eligible: boolean;
  reason: string | null;
  window_closes_at: string | null;
  feedback: TrainingFeedback | null;
  improvement_note: string | null;
  improvement_note_at: string | null;
}

export interface TrainingFeedbackSummary {
  course_id: number;
  feedback_count: number;
  enough_samples: boolean;
  content_avg: number | null;
  instructor_avg: number | null;
  themes: Array<{ theme: string; count: number }>;
  improvement_note: string | null;
  improvement_note_at: string | null;
}

export interface TrainingLearningRecord {
  plan_item_id: number;
  record_id?: number | null;
  status: 'not_started' | 'learning' | 'completed';
  note: string | null;
  reported_at: string | null;
  is_self_reported: boolean;
  formal_completed?: boolean;
}

export interface TrainingPlanItem {
  id: number;
  plan_id: number;
  plan_version: number;
  course_id: number;
  course_snapshot: {
    course_id: number;
    title: string;
    material_url: string;
    dimension_names?: string[];
  };
  dimension_name: string | null;
  reason: string | null;
  due_date: string | null;
  sort_order: number;
  learning: {
    record_id?: number | null;
    status: 'not_started' | 'learning' | 'completed';
    note: string | null;
    reported_at: string | null;
    is_self_reported: boolean;
    formal_completed?: boolean;
  };
  feedback?: TrainingFeedbackState | null;
}

export interface TrainingPlanVersion {
  version: number;
  status: string;
  snapshot: Record<string, unknown>;
  note: string | null;
  created_at: string;
}

export interface TrainingPlan {
  id: number;
  employee_user_id: number;
  talent_profile_id: number | null;
  job_id: number | null;
  target_job_title: string | null;
  source: 'ai' | 'manual';
  status: 'draft' | 'assigned';
  gap_summary: string | null;
  version: number;
  items?: TrainingPlanItem[];
  versions?: TrainingPlanVersion[];
}

export interface GrowthEvent {
  id: number;
  employee_user_id: number;
  talent_profile_id: number | null;
  event_type: string;
  summary: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TrainingEnrollment {
  id: number;
  course_id: number;
  employee_user_id: number;
  priority: 'required' | 'elective';
  status: 'pending' | 'approved' | 'rejected' | 'waitlisted' | 'cancelled' | 'expired';
  expires_at: string | null;
  note: string | null;
}

export interface LearningEvidence {
  id: number;
  evidence_type: string;
  score: number;
  verified_by: string | null;
  payload: Record<string, unknown>;
}
