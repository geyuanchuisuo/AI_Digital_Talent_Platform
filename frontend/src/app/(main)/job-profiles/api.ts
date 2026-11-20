/**
 * 岗位条件页接口封装（T-26）。走 `lib/http.ts`；PUT 请求体只含写入字段
 * （后端 `JobProfileWriteRequest` extra=forbid，回传完整响应体会 422）。
 */

import { del, get, post, request } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  JdDraftData,
  JdDraftPayload,
  JobProfileDeleteData,
  JobProfileListItem,
  JobProfileOut,
  JobProfileWritePayload,
} from './types';

export interface JobProfileQuery {
  keyword?: string;
  is_active?: boolean;
  page: number;
  page_size: number;
}

export function fetchJobProfiles(query: JobProfileQuery): Promise<PageData<JobProfileListItem>> {
  return get<PageData<JobProfileListItem>>('/job-profiles', { params: query });
}

export function getJobProfile(id: number): Promise<JobProfileOut> {
  return get<JobProfileOut>(`/job-profiles/${id}`);
}

export function createJobProfile(payload: JobProfileWritePayload): Promise<JobProfileOut> {
  return post<JobProfileOut>('/job-profiles', payload);
}

/** PUT 走已导出的 `request` 组合（lib/http 未提供 put 封装，避免为此改公共层） */
export function putJobProfile(id: number, payload: JobProfileWritePayload): Promise<JobProfileOut> {
  return request<JobProfileOut>({ method: 'PUT', url: `/job-profiles/${id}`, data: payload });
}

/** 有批次引用 → 软删（soft_deleted:true）；无引用 → 物理删除；重复删除 404 */
export function deleteJobProfile(id: number): Promise<JobProfileDeleteData> {
  return del<JobProfileDeleteData>(`/job-profiles/${id}`);
}

/** T-A22：AI 生成 JD 草稿（真实模型；失败 503 明确报错，前端不返回样例）。
 * 真实生成耗时 10–60s 级，单请求放宽到 120s（覆盖 lib/http 公共默认超时，不改公共层）。 */
export function generateJdDraft(payload: JdDraftPayload): Promise<JdDraftData> {
  return post<JdDraftData>('/job-profiles/jd-draft', payload, { timeout: 120_000 });
}
