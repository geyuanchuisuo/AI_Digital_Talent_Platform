/**
 * 匹配页业务类型（T-28）。口径：接口 §8 + `backend/app/schemas/match.py` 逐字对齐。
 */

export type MatchScopeType = 'all' | 'filter' | 'upload_batch';

export interface MatchBatchListItem {
  batch_id: number;
  job_profile_id: number;
  job_title: string | null;
  job_profile_version: number;
  status: 'pending' | 'running' | 'done' | 'failed';
  progress: number;
  total: number;
  hard_failed: number;
  retry_count: number;
  vector_degraded: boolean;
  created_at: string;
  finished_at: string | null;
}

export interface MatchBatchDetail {
  batch_id: number;
  job_profile_id: number;
  job_profile_version: number;
  config_snapshot: Record<string, unknown>;
  scope: Record<string, unknown> & { resolved_ids_count?: number };
  status: 'pending' | 'running' | 'done' | 'failed';
  progress: number;
  total: number;
  hard_failed: number;
  processed: number;
  skipped_deleted: number;
  vector_degraded: boolean;
  error: string | null;
  retry_count: number;
  created_at: string;
  finished_at: string | null;
}

export interface MatchBatchCreatedData {
  batch_id: number;
  total: number;
  status: string;
  estimated_llm_calls: number;
}

/** `GET /match/batches/{id}/results` 的 items（rank 查询时计算） */
export interface MatchResultItem {
  result_id: number;
  rank: number;
  talent_profile_id: number;
  name: string;
  deleted: boolean;
  current_title: string | null;
  score: number;
  hard_fail: boolean;
  hard_fail_reason: string | null;
  hits_count: number;
  misses_count: number;
}

/** `GET /match/results/{id}` 理由详情 */
export interface MatchResultDetail {
  result_id: number;
  rank: number;
  talent_profile_id: number;
  name: string;
  deleted: boolean;
  current_title: string | null;
  score: number;
  hard_fail: boolean;
  hard_fail_reason: string | null;
  dimension_scores: Record<string, number | null>;
  vector_similarity: number | null;
  vector_degraded: boolean;
  hits: string[];
  misses: string[];
  reason: string | null;
}
