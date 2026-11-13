/**
 * 人才需求页业务类型（T-A21/T-A24）。与 backend/app/modules/recruitment/schemas.py
 * （ProjectDemandCreate/Update/Item、gap-summary 出参）逐字对齐。
 */

export const DEMAND_STATUSES = ['open', 'in_progress', 'fulfilled', 'closed'] as const;
export type DemandStatus = (typeof DEMAND_STATUSES)[number];

export const DEMAND_PRIORITIES = ['high', 'medium', 'low'] as const;
export type DemandPriority = (typeof DEMAND_PRIORITIES)[number];

export interface DemandItem {
  id: number;
  project_id: number | null;
  project_group_id: number | null;
  job_profile_id: number | null;
  position_name: string;
  headcount: number;
  expected_start_date: string | null;
  priority: DemandPriority;
  remark: string | null;
  status: DemandStatus;
  approval_status: string;
  approval_version: number;
  approved_version: number | null;
  approved_by: number | null;
  approved_at: string | null;
  matchable: boolean;
  created_at: string;
  updated_at: string;
}

/** gap-summary 行（T-A24 端点，service.demand_gap_summary 出参） */
export interface GapRow {
  demand_id: number;
  position_name: string;
  project_id: number | null;
  project_group_id: number | null;
  job_profile_id: number | null;
  demand_status: DemandStatus;
  headcount: number;
  advanced_count: number;
  hired_count: number;
  gap: number;
}

export interface ProjectGroupItem {
  id: number;
  name: string;
  status: string;
}

export interface ProjectItem {
  id: number;
  project_group_id: number | null;
  name: string;
  status: string;
}

export interface JobOption {
  id: number;
  title: string;
}

/** POST /demands 请求体（extra=forbid，只传写入字段） */
export interface DemandWritePayload {
  position_name: string;
  headcount: number;
  project_id?: number | null;
  project_group_id?: number | null;
  job_profile_id?: number | null;
  expected_start_date?: string | null;
  priority?: DemandPriority;
  remark?: string | null;
  /** 仅编辑（PATCH）允许；POST 由后端默认 open */
  status?: DemandStatus;
}
