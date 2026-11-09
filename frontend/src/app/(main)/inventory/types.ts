/** F10 人才盘点页数据类型。 */

export type ReviewStatus = 'draft' | 'submitted' | 'confirmed' | 'cancelled';
export type PoolStatus = 'nominated' | 'approved' | 'rejected' | 'removed';

export interface InventoryReview {
  id: number;
  employee_user_id: number;
  talent_profile_id: number | null;
  cycle_name: string;
  review_type: 'annual' | 'ad_hoc';
  status: ReviewStatus;
  /** 以下四项仅管理员可见，员工视角后端不返回 */
  performance_score?: number | null;
  performance_source?: string | null;
  potential_manager_score?: number | null;
  potential_hr_score?: number | null;
  grid_cell?: string | null;
  reviewer_manager_id: number | null;
  reviewer_hr_id: number | null;
  confirmed_at: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface TalentPoolItem {
  id: number;
  employee_user_id: number;
  pool_type: string;
  status: PoolStatus;
  note: string | null;
}

export interface InventorySummary {
  total: number;
  cells: Record<string, number>;
  labels: Record<string, string>;
}

export interface InventoryEvent {
  id: number;
  review_id: number | null;
  event_type: string;
  operator_id: number | null;
  note: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface InventoryDispute {
  id: number;
  review_id: number;
  employee_user_id: number;
  status: 'open' | 'resolved' | 'rejected';
  content: string;
  response: string | null;
  created_at: string;
}
