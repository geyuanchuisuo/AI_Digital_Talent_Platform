/**
 * 简历筛选页业务类型（T-15）。
 *
 * 口径：`docs/接口设计.md` v1.3 §4 + `backend/app/schemas/resume.py`（T-10 / T-14 已落地）。
 * `lib/types.ts` 只放统一信封，本文件是首个"按真实接口手写"的页面级类型集，
 * 字段名与后端逐字一致，不做重命名以免前端再维护一份映射表。
 */

/** 解析任务六状态（`parse_tasks.status`） */
export type QueueStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'duplicate_pending'
  | 'cancelled';

/** 对外任务状态契约（`task_status`），旧 `status` 仍保留兼容 */
export type ContractTaskStatus = 'waiting' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface QueueSummary {
  pending: number;
  running: number;
  success: number;
  failed: number;
  duplicate_pending: number;
  cancelled: number;
  total: number;
}

/** `GET /resumes/queue` 的 items 项 */
export interface QueueItem {
  task_id: number;
  resume_file_id: number;
  filename: string;
  upload_batch_id: string;
  status: QueueStatus;
  task_status: ContractTaskStatus;
  duplicate_pending: boolean;
  stage: string | null;
  retries: number;
  error: string | null;
  truncated: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  profiled_at: string | null;
  talent_profile_id: number | null;
  can_retry: boolean;
  can_requeue: boolean;
}

/** `GET /resumes/queue` 的 data：比通用分页体多 summary / concurrency */
export interface QueueListData {
  summary: QueueSummary;
  concurrency: number;
  items: QueueItem[];
  total: number;
  page: number;
  page_size: number;
}

/** `PATCH /resumes/queue/settings` 的 data */
export interface QueueSettingsData {
  concurrency: number;
  /** 固定 `next_scan`：下一个扫描周期（约 2s）生效 */
  effective: string;
}

/** retry / requeue / delete 的返回：动作后的任务快照 */
export interface TaskActionData {
  task_id: number;
  status: QueueStatus;
  task_status: ContractTaskStatus;
  stage: string | null;
  retries: number;
}

/** 上传响应里的重复项（`can_requeue` 控制"重新入队"按钮） */
export interface DuplicateItem {
  filename: string;
  reason: string;
  resume_file_id: number;
  task_id: number | null;
  task_status: string | null;
  can_requeue: boolean;
}

/** 上传响应里被拒的文件（类型/大小/内容校验不通过） */
export interface RejectedItem {
  filename: string;
  reason: string;
}

/** `POST /resumes/batch` 的 data（HTTP 202） */
export interface BatchUploadData {
  upload_batch_id: string;
  accepted: number;
  duplicates: DuplicateItem[];
  rejected: RejectedItem[];
  queue_total: number;
}

/** `GET /resumes/batches` 的 items 项（按 upload_batch_id 实时聚合，无批次表） */
export interface BatchSummaryItem {
  upload_batch_id: string;
  file_count: number;
  success: number;
  failed: number;
  /** 尚未出结果的数量（含 running / duplicate_pending / cancelled） */
  pending: number;
  uploaded_at: string;
}

// =========================================================================== #
// T-27 筛选批次与结果（接口 §7 + backend/app/schemas/screen.py）
// =========================================================================== #

export type ScreenScopeType = 'all_unscreened' | 'upload_batch' | 'resume_file_ids' | 'all';

/** 批次状态四态（数据库 §6.2） */
export type ScreenBatchStatus = 'pending' | 'running' | 'done' | 'failed';

/** `GET /screen/batches` 的 items 项 */
export interface ScreenBatchListItem {
  batch_id: number;
  job_profile_id: number;
  job_title: string | null;
  job_profile_version: number;
  status: ScreenBatchStatus;
  progress: number;
  total: number;
  passed: number;
  pending_count: number;
  rejected: number;
  retry_count: number;
  created_at: string;
  finished_at: string | null;
}

/** `GET /screen/batches/{id}`（轮询详情） */
export interface ScreenBatchDetail {
  batch_id: number;
  job_profile_id: number;
  job_profile_version: number;
  config_snapshot: Record<string, unknown>;
  scope: Record<string, unknown> & { resolved_ids_count?: number };
  status: ScreenBatchStatus;
  progress: number;
  total: number;
  passed: number;
  pending_count: number;
  rejected: number;
  processed: number;
  skipped_deleted: number;
  vector_degraded: boolean;
  error: string | null;
  retry_count: number;
  created_at: string;
  finished_at: string | null;
}

/** POST /screen/batches 的 202 data */
export interface ScreenBatchCreatedData {
  batch_id: number;
  total: number;
  status: string;
  estimated_llm_calls: number;
}

export type Verdict = 'passed' | 'pending' | 'rejected';

/** `GET /screen/batches/{id}/results` 的 items 项 */
export interface ScreenResultItem {
  result_id: number;
  talent_profile_id: number;
  name: string;
  deleted: boolean;
  current_title: string | null;
  degree: string | null;
  work_years: number | null;
  hard_fail: boolean;
  hard_fail_reason: string | null;
  score: number | null;
  verdict: Verdict;
  manual_verdict: Verdict | null;
  effective_verdict: Verdict;
  manual_verdict_by: number | null;
  manual_verdict_at: string | null;
  reason_summary: string | null;
  hits: string[];
  misses: string[];
  created_at: string;
}

/** `POST /job-profiles` 简要列表项（岗位下拉用；复用 /job-profiles 列表接口） */
export interface JobOption {
  id: number;
  title: string;
  version: number;
  is_active: boolean;
}
