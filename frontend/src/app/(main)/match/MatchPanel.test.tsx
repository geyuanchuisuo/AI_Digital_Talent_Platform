import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MatchPanel from './MatchPanel';
import {
  createMatchBatch,
  fetchMatchBatchDetail,
  fetchMatchBatches,
  fetchMatchResultDetail,
  fetchMatchResults,
} from './api';
import { fetchJobOptions } from '../screen/api';
import type { MatchBatchListItem, MatchResultItem } from './types';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    createMatchBatch: vi.fn(),
    fetchJobOptions: vi.fn(),
    fetchMatchBatches: vi.fn(),
    fetchMatchBatchDetail: vi.fn(),
    fetchMatchResults: vi.fn(),
    fetchMatchResultDetail: vi.fn(),
    retryMatchBatch: vi.fn(),
  };
});
vi.mock('../screen/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../screen/api')>();
  return {
    ...actual,
    fetchJobOptions: vi.fn().mockResolvedValue([{ id: 1, title: '高级后端工程师', version: 1, is_active: true }]),
    fetchBatches: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 }),
    fetchQueue: vi.fn(),
  };
});

const fetchMatchBatchesMock = vi.mocked(fetchMatchBatches);
const fetchMatchBatchDetailMock = vi.mocked(fetchMatchBatchDetail);
const fetchMatchResultsMock = vi.mocked(fetchMatchResults);
const fetchMatchResultDetailMock = vi.mocked(fetchMatchResultDetail);
const createMock = vi.mocked(createMatchBatch);
const fetchJobOptionsMock = vi.mocked(fetchJobOptions);

function batchItem(patch: Partial<MatchBatchListItem>): MatchBatchListItem {
  return {
    batch_id: 2,
    job_profile_id: 1,
    job_title: '高级后端工程师',
    job_profile_version: 1,
    status: 'done',
    progress: 25,
    total: 25,
    hard_failed: 3,
    retry_count: 0,
    vector_degraded: false,
    created_at: '2026-11-06T10:00:00',
    finished_at: '2026-11-06T10:10:00',
    ...patch,
  };
}

function resultItem(patch: Partial<MatchResultItem>): MatchResultItem {
  return {
    result_id: 8,
    rank: 1,
    talent_profile_id: 5,
    name: '张三',
    deleted: false,
    current_title: '后端工程师',
    score: 86.0,
    hard_fail: false,
    hard_fail_reason: null,
    hits_count: 6,
    misses_count: 1,
    ...patch,
  };
}

describe('MatchPanel（T-28）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchJobOptionsMock.mockResolvedValue([{ id: 1, title: '高级后端工程师', version: 1, is_active: true }]);
    fetchMatchBatchesMock.mockResolvedValue({
      items: [batchItem({}), batchItem({ batch_id: 3, vector_degraded: true })],
      total: 2,
      page: 1,
      page_size: 20,
    });
    fetchMatchBatchDetailMock.mockImplementation(async (batchId: number) => ({
      batch_id: batchId,
      job_profile_id: 1,
      job_profile_version: 1,
      config_snapshot: {},
      scope: { type: 'all', resolved_ids_count: 25 },
      status: 'done' as const,
      progress: 25,
      total: 25,
      hard_failed: 3,
      processed: 25,
      skipped_deleted: 0,
      vector_degraded: batchId === 3, // #3 = 降级批次
      error: null,
      retry_count: 0,
      created_at: '2026-11-06T10:00:00',
      finished_at: '2026-11-06T10:10:00',
    }));
    fetchMatchResultsMock.mockResolvedValue({
      items: [
        resultItem({}),
        resultItem({
          result_id: 9,
          rank: 2,
          name: '李四',
          score: 0,
          hard_fail: true,
          hard_fail_reason: '学历不满足：要求 本科，候选人 大专',
        }),
      ],
      total: 2,
      page: 1,
      page_size: 10,
    });
    fetchMatchResultDetailMock.mockResolvedValue({
      result_id: 8,
      rank: 1,
      talent_profile_id: 5,
      name: '张三',
      deleted: false,
      current_title: '后端工程师',
      score: 86.0,
      hard_fail: false,
      hard_fail_reason: null,
      dimension_scores: { skills: 90, experience: 80, education: 85, others: 70, vector_similarity: 0.83 },
      vector_similarity: 0.83,
      vector_degraded: false,
      hits: ['Python', 'FastAPI'],
      misses: ['Milvus'],
      reason: '候选人技能与岗位高度重合',
    });
    createMock.mockResolvedValue({ batch_id: 5, total: 25, status: 'pending', estimated_llm_calls: 25 });
  });

  it('发起匹配（filter 范围）→ 202 后刷新', async () => {
    render(<MatchPanel />);
    await waitFor(() => expect(fetchMatchBatchesMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /发\s*起\s*匹\s*配/ }));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith(1, { type: 'all' }));
    await waitFor(() => expect(screen.getByText(/批次 #5 已创建/)).toBeTruthy());
  });

  it('名次表渲染（硬淘汰 score=0 置底）+ 理由详情弹窗', async () => {
    render(<MatchPanel />);
    await waitFor(() => expect(screen.getByText('#2')).toBeTruthy());

    fireEvent.click(screen.getByText('#2'));
    await waitFor(() => expect(fetchMatchResultsMock).toHaveBeenCalled());
    // 硬淘汰置底：张三 rank1、李四 rank2（score=0）
    await screen.findByText('张三', {}, { timeout: 5000 });
    expect(screen.getByText('李四')).toBeTruthy();
    expect(screen.getByText('86')).toBeTruthy();

    fireEvent.click(screen.getAllByText('理由')[0]);
    const detail = await screen.findByText('候选人技能与岗位高度重合', {}, { timeout: 5000 });
    expect(detail).toBeTruthy();
    expect(screen.getByText(/向量相似度：83%/)).toBeTruthy();
    expect(screen.getByText('技能：90')).toBeTruthy();
  });

  it('vector_degraded 批次展示黄色告警', async () => {
    render(<MatchPanel />);
    await waitFor(() => expect(screen.getByText('#3')).toBeTruthy());
    fireEvent.click(screen.getByText('#3'));
    // usePolling 按周期取数（fetcher 变化不立即重取），等下一个 2s tick
    await screen.findByText(/向量依赖不可用/, {}, { timeout: 6000 });
  });
});
