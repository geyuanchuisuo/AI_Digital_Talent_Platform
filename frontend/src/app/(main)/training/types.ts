/** C 培训成长页数据类型。 */

export interface TrainingCourse {
  id: number;
  title: string;
  description: string | null;
  material_url: string;
  dimension_names: string[];
  status: 'active' | 'inactive';
  version: number;
}

export interface TrainingLearningRecord {
  plan_item_id: number;
  status: 'not_started' | 'learning' | 'completed';
  note: string | null;
  reported_at: string | null;
  is_self_reported: boolean;
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
  sort_order: number;
  learning: {
    status: 'not_started' | 'learning' | 'completed';
    note: string | null;
    reported_at: string | null;
    is_self_reported: boolean;
  };
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
