/**
 * 简历筛选页接口封装（T-15）。
 *
 * 全部走 `lib/http.ts` 的 `get/post/patch/del`，不另起 axios 实例——
 * 鉴权注入、401/403 跳转、统一错误提示都在那一层，页面只处理成功路径。
 *
 * 唯一例外是 `uploadBatch`：需要 multipart，`lib/http` 没有 form 提交封装，
 * 这里直接用导出的 `http` 实例（仍继承全部拦截器），不为一次性需求改公共代码。
 */

import { del, get, http, patch, post } from '@/lib/http';
import type { ApiResponse, PageData } from '@/lib/types';

import type {
  BatchSummaryItem,
  BatchUploadData,
  JobOption,
  QueueListData,
  QueueSettingsData,
  QueueStatus,
  ScreenBatchCreatedData,
  ScreenBatchDetail,
  ScreenBatchListItem,
  ScreenResultItem,
  TaskActionData,
  Verdict,
} from './types';

export interface QueueQuery {
  status?: QueueStatus;
  upload_batch_id?: string;
  page: number;
  page_size: number;
}

export interface UploadOptions {
  allowed_ext: string[];
  max_mb: number;
  ocr_enabled: boolean;
  max_pages?: number;
}

export function fetchUploadOptions(): Promise<UploadOptions> {
  return get<UploadOptions>('/resumes/upload-options');
}

export function fetchQueue(query: QueueQuery): Promise<QueueListData> {
  return get<QueueListData>('/resumes/queue', { params: query });
}

export function updateConcurrency(concurrency: number): Promise<QueueSettingsData> {
  return patch<QueueSettingsData>('/resumes/queue/settings', { concurrency });
}

export function retryTask(taskId: number): Promise<TaskActionData> {
  return post<TaskActionData>(`/resumes/queue/${taskId}/retry`);
}

export function requeueTask(taskId: number): Promise<TaskActionData> {
  return post<TaskActionData>(`/resumes/queue/${taskId}/requeue`);
}

/** 软取消（不是物理删除）：文件与已产出画像保留 */
export function cancelTask(taskId: number): Promise<TaskActionData> {
  return del<TaskActionData>(`/resumes/queue/${taskId}`);
}

/** 批次下拉数据；`total` 是批次数，一期最多取 100 条 */
export function fetchBatches(page = 1, pageSize = 100): Promise<PageData<BatchSummaryItem>> {
  return get<PageData<BatchSummaryItem>>('/resumes/batches', {
    params: { page, page_size: pageSize },
  });
}

/**
 * 批量上传（字段名固定为 `files`，接口 §4）。
 *
 * 浏览器会自动补 multipart boundary，**这里绝不能手写 Content-Type**（手写的
 * `multipart/form-data` 缺 boundary 会让后端解析失败）。实例默认的
 * `application/json` 由 `lib/http.ts` 的请求拦截器在检测到 FormData 时删除：
 * axios 1.21 会先把 FormData 按 JSON 头序列化成 `{"files":{}}`，等到 adapter
 * 再删头已经来不及（详见 http.ts 注释 / 开发日志 H-16）。
 * 20 份大文件可能超过默认 30s，单独放宽到 120s。
 */
export async function uploadBatch(files: File[]): Promise<BatchUploadData> {
  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);
  const response = await http.post<ApiResponse<BatchUploadData>>('/resumes/batch', form, {
    timeout: 120_000,
  });
  return response.data.data;
}

// =========================================================================== #
// T-27 筛选批次（接口 §7）
// =========================================================================== #

export interface ScreenScope {
  type: 'all_unscreened' | 'upload_batch' | 'resume_file_ids' | 'all';
  upload_batch_id?: string;
  ids?: number[];
}

export function createScreenBatch(
  jobProfileId: number,
  scope: ScreenScope,
): Promise<ScreenBatchCreatedData> {
  return post<ScreenBatchCreatedData>('/screen/batches', { job_profile_id: jobProfileId, scope });
}

export function fetchScreenBatches(
  jobProfileId?: number,
  page = 1,
  pageSize = 10,
): Promise<PageData<ScreenBatchListItem>> {
  return get<PageData<ScreenBatchListItem>>('/screen/batches', {
    params: { job_profile_id: jobProfileId, page, page_size: pageSize },
  });
}

export function fetchScreenBatchDetail(batchId: number): Promise<ScreenBatchDetail> {
  return get<ScreenBatchDetail>(`/screen/batches/${batchId}`);
}

export function retryScreenBatch(batchId: number): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(`/screen/batches/${batchId}/retry`);
}

export interface ScreenResultsQuery {
  verdict?: Verdict;
  keyword?: string;
  page: number;
  page_size: number;
}

export function fetchScreenResults(
  batchId: number,
  query: ScreenResultsQuery,
): Promise<PageData<ScreenResultItem>> {
  return get<PageData<ScreenResultItem>>(`/screen/batches/${batchId}/results`, { params: query });
}

/** 改判（manual_verdict=null 撤销）；后端会同步重算批次三档计数 */
export function setScreenManualVerdict(
  resultId: number,
  manualVerdict: Verdict | null,
): Promise<ScreenResultItem> {
  return patch<ScreenResultItem>(`/screen/results/${resultId}`, { manual_verdict: manualVerdict });
}

/** 岗位下拉（复用岗位列表接口，只取启用岗位，最多 100） */
export function fetchJobOptions(): Promise<JobOption[]> {
  return get<PageData<JobOption>>('/job-profiles', {
    params: { is_active: true, page: 1, page_size: 100 },
  }).then((data) => data.items);
}
