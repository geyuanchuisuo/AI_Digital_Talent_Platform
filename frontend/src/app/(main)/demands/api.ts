/**
 * 人才需求页接口封装（T-A21/T-A24）。走 lib/http.ts；POST/PATCH 请求体只含
 * 写入字段（后端 ProjectDemandCreate/Update extra=forbid，回传完整响应体会 422）。
 */

import { get, patch, post } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type { DemandItem, DemandWritePayload, GapRow, JobOption, ProjectGroupItem, ProjectItem } from './types';

export function fetchGapSummary(params: { status?: string; project_id?: number }): Promise<PageData<GapRow>> {
  return get<PageData<GapRow>>('/recruitment/demands/gap-summary', { params });
}

export function fetchDemands(params: { page: number; page_size: number }): Promise<PageData<DemandItem>> {
  return get<PageData<DemandItem>>('/recruitment/demands', { params });
}

/** 编辑前拉取完整明细（gap-summary 只有统计字段；approval/matchable 等在详情） */
export function fetchDemand(id: number): Promise<DemandItem> {
  return get<DemandItem>(`/recruitment/demands/${id}`);
}

export function createDemand(payload: DemandWritePayload): Promise<DemandItem> {
  return post<DemandItem>('/recruitment/demands', payload);
}

/** lib/http 未提供 patch 导出则走 request 组合（避免为业务改公共层） */
export function updateDemand(id: number, payload: DemandWritePayload): Promise<DemandItem> {
  return patch<DemandItem>(`/recruitment/demands/${id}`, payload);
}

export function fetchProjectGroups(): Promise<PageData<ProjectGroupItem>> {
  return get<PageData<ProjectGroupItem>>('/recruitment/project-groups', { params: { page_size: 200 } });
}

export function fetchProjects(): Promise<PageData<ProjectItem>> {
  return get<PageData<ProjectItem>>('/recruitment/projects', { params: { page_size: 200 } });
}

export function fetchJobOptions(): Promise<PageData<JobOption>> {
  return get<PageData<JobOption>>('/job-profiles', { params: { page: 1, page_size: 100 } });
}
