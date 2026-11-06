/** C 测评页数据类型（与后端 `/api/assessments` 对齐）。 */

export interface PaginatedItems<T> {
  items: T[];
  total: number;
}

export interface AssessmentQuestion {
  id: number;
  dimension_name: string;
  dimension_type: string;
  difficulty: string;
  question_type: 'single_choice' | 'short_answer';
  stem: string;
  options: string[];
  correct_answer: string | null;
  reference_points: string | null;
  scoring_guideline: string | null;
  status: 'pending' | 'approved' | 'disabled';
  job_id: number | null;
  job_title: string | null;
}

export interface QuestionPayload {
  job_id?: number | null;
  job_title?: string | null;
  dimension_name: string;
  dimension_type: string;
  difficulty: string;
  question_type: 'single_choice' | 'short_answer';
  stem: string;
  options?: string[];
  correct_answer?: string | null;
  reference_points?: string | null;
  scoring_guideline?: string | null;
}

export interface AssessmentPlan {
  id: number;
  purpose: 'recruitment' | 'employee';
  job_id: number;
  job_title: string;
  dimensions: { name: string; type: string; weight: number }[];
  total_question_count: number;
  pass_score: number;
  status: string;
  version: number;
  paper?: { id: number; question_count: number };
}

export interface AssessmentAssignmentItem {
  id: number;
  plan_id: number;
  purpose: 'recruitment' | 'employee';
  user_id: number;
  job_title: string | null;
  status: 'assigned' | 'in_progress' | 'grading' | 'completed' | 'cancelled';
  answers_open: boolean;
  published_result_version: number | null;
  attempt_no: number;
}

export interface AssignmentQuestion {
  question_id: number;
  dimension_name: string;
  question_type: 'single_choice' | 'short_answer';
  stem: string;
  options: string[];
  correct_answer?: string | null;
  reference_points?: string | null;
  scoring_guideline?: string | null;
  review_completed?: boolean;
  answer: {
    selected_option: string | null;
    answer_text: string | null;
    grading_status: string;
    score: number | null;
  } | null;
}

export interface AssessmentDetail {
  assignment: AssessmentAssignmentItem;
  paper: { id: number; question_count: number; pass_score: number };
  questions: AssignmentQuestion[];
  result: AssessmentResultOut | null;
  answers_visible: boolean;
}

export interface DimensionScore {
  name: string;
  type: string;
  weight: number;
  question_count: number;
  score: number;
}

export interface AssessmentResultOut {
  version: number;
  dimension_scores: DimensionScore[];
  total_score: number;
  pass_score: number;
  passed: boolean;
  published: boolean;
  answers_open: boolean;
}

export interface AnswerScoreRow {
  question_id: number;
  selected_option: string | null;
  answer_text: string | null;
  grading_status: string;
  score: number | null;
  is_correct: number | null;
}
