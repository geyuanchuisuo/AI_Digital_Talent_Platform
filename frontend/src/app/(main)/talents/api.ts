/**
 * 人才画像页接口封装（T-20）。全部走 `lib/http.ts`，模式与 `screen/api.ts` 一致。
 */

import { get, post } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  DuplicateItem,
  KeepResolveData,
  MergeResolveData,
  RestoreTalentData,
  TalentListItem,
  TalentSearchItem,
} from './types';

export interface TalentQuery {
  keyword?: string;
  tag?: string;
  tag_type?: string;
  degree?: string;
  min_years?: number;
  max_years?: number;
  /** true = 回收站视角（只看已软删画像） */
  deleted?: boolean;
  page: number;
  page_size: number;
}

export function fetchTalents(query: TalentQuery): Promise<PageData<TalentListItem>> {
  return get<PageData<TalentListItem>>('/talents', { params: query });
}

/**
 * 语义检索（T-19）。
 *
 * ⚠️ 无业务写入的接口在 Embedding/Milvus 不可用时返回 503（不是空列表），
 * 调用方应把 ApiError 提示为"向量服务不可用"，不要按空态渲染。
 */
export async function searchTalents(query: string, topK = 20): Promise<TalentSearchItem[]> {
  const response = await get<{ items: TalentSearchItem[] }>('/talents/search', {
    params: { query, top_k: topK },
  });
  return response.items;
}

export function fetchDuplicates(
  page = 1,
  pageSize = 1,
  status: 'pending' | 'merged' | 'kept' = 'pending',
): Promise<PageData<DuplicateItem>> {
  return get<PageData<DuplicateItem>>('/talents/duplicates', { params: { status, page, page_size: pageSize } });
}

/** 查重待确认数量（角标用，只取 total） */
export async function fetchPendingDuplicateCount(): Promise<number> {
  const data = await fetchDuplicates(1, 1, 'pending');
  return data.total;
}

export interface ResolvePayload {
  action: 'merge' | 'keep';
  /** 可省略；一期只接受等于 profile_a_id（传其他值 400） */
  master_profile_id?: number;
  resolution_note?: string;
}

/** merge → 200，keep → 202；`lib/http` 对两者都按成功信封解包 */
export function resolveDuplicate(
  duplicateId: number,
  payload: ResolvePayload,
): Promise<MergeResolveData | KeepResolveData> {
  return post<MergeResolveData | KeepResolveData>(`/talents/duplicates/${duplicateId}/resolve`, payload);
}

/** 撤销软删除（回收站用）；向量随 restore 强制重算，失败仍是 200 + failed */
export function restoreTalent(talentId: number): Promise<RestoreTalentData> {
  return post<RestoreTalentData>(`/talents/${talentId}/restore`);
}

// =========================================================================== #
// T-21 画像详情页
// =========================================================================== #
import { del, downloadFile, patch as patchMethod } from '@/lib/http';
import type { PatchTalentData, RevectorTalentData, TalentDetailData, TalentPatchPayload } from './types';

export function getTalent(talentId: number): Promise<TalentDetailData> {
  return get<TalentDetailData>(`/talents/${talentId}`);
}

/** PATCH 画像：向量按 hash 触发重算，失败仍 200（data.vector_status=failed） */
export function patchTalent(talentId: number, payload: TalentPatchPayload): Promise<PatchTalentData> {
  return patchMethod<PatchTalentData>(`/talents/${talentId}`, payload);
}

/**
 * 强制重建向量（修 vector_status=failed）。
 * ⚠️ 无业务写入的接口：Embedding/Milvus 不可用时 503，调用方提示"稍后重试"。
 */
export function revectorTalent(talentId: number): Promise<RevectorTalentData> {
  return post<RevectorTalentData>(`/talents/${talentId}/revector`);
}

/** 软删除画像（F2-7）：原始文件与历史结果保留，可从回收站恢复 */
export function deleteTalent(talentId: number): Promise<{ deleted: boolean; vector_deleted: boolean }> {
  return del(`/talents/${talentId}`);
}

/** blob 下载原始简历（必须带 Bearer，`<a>` 直链会 401；文件名由 Content-Disposition 解析） */
export function downloadResumeFile(talentId: number, resumeFileId: number, fallbackName?: string): Promise<void> {
  return downloadFile(`/talents/${talentId}/resume-file?resume_file_id=${resumeFileId}`, fallbackName);
}
