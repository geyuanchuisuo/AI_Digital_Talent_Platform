/**
 * 匹配页接口封装（T-28，接口 §8）。
 */

import { get, post } from '@/lib/http';
import type { PageData } from '@/lib/types';

import type {
  MatchBatchCreatedData,
  MatchBatchDetail,
  MatchBatchListItem,
  MatchResultDetail,
  MatchResultItem,
} from './types';

export interface MatchScope {
  type: 'all' | 'filter' | 'upload_batch';
  upload_batch_id?: string;
  degree?: string;
  min_years?: number;
  tags?: string[];
}

export function createMatchBatch(jobProfileId: number, scope: MatchScope): Promise<MatchBatchCreatedData> {
  return post<MatchBatchCreatedData>('/match/batches', { job_profile_id: jobProfileId, scope });
}

export function fetchMatchBatches(
  jobProfileId?: number,
  page = 1,
  pageSize = 10,
): Promise<PageData<MatchBatchListItem>> {
  return get<PageData<MatchBatchListItem>>('/match/batches', {
    params: { job_profile_id: jobProfileId, page, page_size: pageSize },
  });
}

export function fetchMatchBatchDetail(batchId: number): Promise<MatchBatchDetail> {
  return get<MatchBatchDetail>(`/match/batches/${batchId}`);
}

export function retryMatchBatch(batchId: number): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(`/match/batches/${batchId}/retry`);
}

export function fetchMatchResults(
  batchId: number,
  page = 1,
  pageSize = 10,
): Promise<PageData<MatchResultItem>> {
  return get<PageData<MatchResultItem>>(`/match/batches/${batchId}/results`, {
    params: { page, page_size: pageSize },
  });
}

export function fetchMatchResultDetail(resultId: number): Promise<MatchResultDetail> {
  return get<MatchResultDetail>(`/match/results/${resultId}`);
}

