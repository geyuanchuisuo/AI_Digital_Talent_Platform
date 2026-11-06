import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ScreenBatchPanel from './ScreenBatchPanel';
import {
  createScreenBatch,
  fetchJobOptions,
  fetchScreenBatchDetail,
  fetchScreenBatches,
  fetchScreenResults,
  setScreenManualVerdict,
} from './api';
import type { ScreenBatchListItem, ScreenResultItem } from './types';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    createScreenBatch: vi.fn(),
    fetchJobOptions: vi.fn(),
    fetchScreenBatches: vi.fn(),
    fetchScreenBatchDetail: vi.fn(),
    fetchScreenResults: vi.fn(),
    retryScreenBatch: vi.fn(),
    setScreenManualVerdict: vi.fn(),
    fetchBatches: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 }),
    fetchQueue: vi.fn(),
  };
});

const fetchScreenBatchesMock = vi.mocked(fetchScreenBatches);
const fetchJobOptionsMock = vi.mocked(fetchJobOptions);
const fetchScreenResultsMock = vi.mocked(fetchScreenResults);
const fetchScreenBatchDetailMock = vi.mocked(fetchScreenBatchDetail);
const createMock = vi.mocked(createScreenBatch);
const verdictMock = vi.mocked(setScreenManualVerdict);

function batchItem(patch: Partial<ScreenBatchListItem>): ScreenBatchListItem {
  return {
    batch_id: 3,
    job_profile_id: 1,
    job_title: '高级后端工程师',
    job_profile_version: 1,
    status: 'done',
    progress: 5,
    total: 5,
    passed: 2,
    pending_count: 1,
    rejected: 2,
    retry_count: 0,
    created_at: '2026-11-06T10:00:00',
    finished_at: '2026-11-06T10:05:00',
    ...patch,
  };
}

function resultItem(patch: Partial<ScreenResultItem>): ScreenResultItem {
  return {
    result_id: 30,
    talent_profile_id: 5,
    name: '张三',
    deleted: false,
    current_title: '后端工程师',
    degree: '本科',
    work_years: 4,
    hard_fail: false,
    hard_fail_reason: null,
    score: 73.25,
    verdict: 'pending',
    manual_verdict: null,
    effective_verdict: 'pending',
    manual_verdict_by: null,
    manual_verdict_at: null,
    reason_summary: '命中 python',
    hits: ['python'],
    misses: ['milvus'],
    created_at: '2026-11-06T10:01:00',
    ...patch,
  };
}

describe('ScreenBatchPanel（T-27）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchJobOptionsMock.mockResolvedValue([{ id: 1, title: '高级后端工程师', version: 2, is_active: true }]);
    fetchScreenBatchesMock.mockResolvedValue({
      items: [batchItem({}), batchItem({ batch_id: 4, status: 'failed', retry_count: 1 })],
      total: 2,
      page: 1,
      page_size: 20,
    });
    fetchScreenBatchDetailMock.mockResolvedValue({
      batch_id: 3,
      job_profile_id: 1,
      job_profile_version: 1,
      config_snapshot: {},
      scope: { type: 'all', resolved_ids_count: 5 },
      status: 'done',
      progress: 5,
      total: 5,
      passed: 2,
      pending_count: 1,
      rejected: 2,
      processed: 5,
      skipped_deleted: 1,
      vector_degraded: false,
      error: null,
      retry_count: 0,
      created_at: '2026-11-06T10:00:00',
      finished_at: '2026-11-06T10:05:00',
    });
    fetchScreenResultsMock.mockResolvedValue({
      items: [resultItem({})],
      total: 1,
      page: 1,
      page_size: 10,
    });
    createMock.mockResolvedValue({ batch_id: 9, total: 5, status: 'pending', estimated_llm_calls: 4 });
    verdictMock.mockResolvedValue(resultItem({ manual_verdict: 'passed', effective_verdict: 'passed' }));
  });

  it('发起筛选（all 范围）→ 202 后刷新批次并选中详情', async () => {
    render(<ScreenBatchPanel />);
    await waitFor(() => expect(fetchScreenBatchesMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /发\s*起\s*筛\s*选/ }));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith(1, { type: 'all' }));
    await waitFor(() => expect(screen.getByText(/批次 #9 已创建/)).toBeTruthy());
  });

  it('批次详情 + 结果表（有效结论/硬淘汰说明）+ 改判调用', async () => {
    render(<ScreenBatchPanel />);
    await waitFor(() => expect(screen.getByText('#3')).toBeTruthy());

    fireEvent.click(screen.getByText('#3'));
    await waitFor(() => expect(fetchScreenResultsMock).toHaveBeenCalled());
    expect(await screen.findByText('张三', {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText('73.25')).toBeTruthy();
    expect(screen.getAllByText('待定').length).toBeGreaterThanOrEqual(2); // Tab 标签 + 行内 Tag
    // skipped_deleted 提示
    expect(screen.getByText(/1 人画像已删除被跳过/)).toBeTruthy();

    fireEvent.click(screen.getByTestId('judge-30'));
    const passButton = await screen.findByRole('button', { name: '改为通过' });
    fireEvent.click(passButton);
    await waitFor(() => expect(verdictMock).toHaveBeenCalledWith(30, 'passed'));
  });

  it('failed 批次显示重试入口', async () => {
    render(<ScreenBatchPanel />);
    await waitFor(() => expect(screen.getByText('#4')).toBeTruthy());
    expect(screen.getAllByText('重试').length).toBeGreaterThan(0);
  });
});
