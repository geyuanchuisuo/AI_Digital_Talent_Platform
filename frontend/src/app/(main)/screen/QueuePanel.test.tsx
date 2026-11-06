import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import QueuePanel from './QueuePanel';
import { cancelTask, fetchBatches, fetchQueue, requeueTask, retryTask, updateConcurrency } from './api';
import type { QueueListData, QueueItem } from './types';

vi.mock('./api', () => ({
  fetchQueue: vi.fn(),
  fetchBatches: vi.fn(),
  updateConcurrency: vi.fn(),
  retryTask: vi.fn(),
  requeueTask: vi.fn(),
  cancelTask: vi.fn(),
}));

const fetchQueueMock = vi.mocked(fetchQueue);
const fetchBatchesMock = vi.mocked(fetchBatches);
const updateConcurrencyMock = vi.mocked(updateConcurrency);
const retryTaskMock = vi.mocked(retryTask);
const requeueTaskMock = vi.mocked(requeueTask);
const cancelTaskMock = vi.mocked(cancelTask);

const BATCH = '01JABCDEFGHJKMNPQRSTVWXYZA';

function item(patch: Partial<QueueItem> & Pick<QueueItem, 'task_id' | 'filename' | 'status'>): QueueItem {
  return {
    resume_file_id: patch.task_id + 10,
    upload_batch_id: BATCH,
    stage: null,
    retries: 0,
    error: null,
    truncated: false,
    created_at: '2026-11-05T10:20:30',
    started_at: null,
    finished_at: null,
    profiled_at: null,
    talent_profile_id: null,
    can_retry: false,
    can_requeue: false,
    ...patch,
  };
}

const QUEUE: QueueListData = {
  summary: { pending: 1, running: 1, success: 1, failed: 1, duplicate_pending: 0, cancelled: 1, total: 5 },
  concurrency: 3,
  items: [
    item({
      task_id: 1,
      filename: '失败简历.pdf',
      status: 'failed',
      stage: 'vector',
      retries: 2,
      error: 'Milvus 连接失败: timeout',
      can_retry: true,
      can_requeue: true,
    }),
    item({ task_id: 2, filename: '已取消简历.pdf', status: 'cancelled', can_requeue: true }),
    item({ task_id: 3, filename: '解析中简历.pdf', status: 'running', stage: 'extract' }),
    item({ task_id: 4, filename: '成功简历.pdf', status: 'success', talent_profile_id: 8 }),
    item({ task_id: 5, filename: '待处理简历.pdf', status: 'pending' }),
  ],
  total: 5,
  page: 1,
  page_size: 10,
};

describe('QueuePanel', () => {
  beforeEach(() => {
    fetchQueueMock.mockReset().mockResolvedValue(QUEUE);
    fetchBatchesMock.mockReset().mockResolvedValue({
      items: [
        { upload_batch_id: BATCH, file_count: 8, success: 6, failed: 1, pending: 1, uploaded_at: '2026-11-05T10:00:00' },
      ],
      total: 1,
      page: 1,
      page_size: 100,
    });
    updateConcurrencyMock.mockReset().mockResolvedValue({ concurrency: 3, effective: 'next_scan' });
    retryTaskMock.mockReset().mockResolvedValue({ task_id: 1, status: 'pending', stage: 'vector', retries: 3 });
    requeueTaskMock.mockReset().mockResolvedValue({ task_id: 2, status: 'pending', stage: null, retries: 1 });
    cancelTaskMock.mockReset().mockResolvedValue({ task_id: 5, status: 'cancelled', stage: null, retries: 0 });
  });

  it('渲染汇总卡片、状态与阶段，按钮严格按 can_retry/can_requeue/可删状态显隐', async () => {
    render(<QueuePanel reloadSignal={0} />);

    expect(await screen.findByText('失败简历.pdf')).toBeTruthy();

    // 汇总卡片（含已取消）
    expect(screen.getByText('队列总数')).toBeTruthy();
    expect(screen.getByText('查重待确认')).toBeTruthy();
    expect(screen.getAllByText('已取消').length).toBeGreaterThan(0);

    // 状态 / 阶段 / 错误
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0);
    expect(screen.getByText('同步向量')).toBeTruthy();
    expect(screen.getByText('提取文本')).toBeTruthy();
    expect(screen.getByText('Milvus 连接失败: timeout')).toBeTruthy();

    // failed → 重试 + 重新入队；cancelled → 仅重新入队
    expect(screen.getAllByRole('button', { name: /重\s*试/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: '重新入队' })).toHaveLength(2);
    // running / cancelled 不可移除，其余 3 行可移除
    expect(screen.getAllByRole('button', { name: /移\s*除/ })).toHaveLength(3);

    // 点"移除"先弹二次确认，不直接请求
    fireEvent.click(screen.getAllByRole('button', { name: /移\s*除/ })[0]);
    expect(await screen.findByText('从队列移除该简历？')).toBeTruthy();
    expect(cancelTaskMock).not.toHaveBeenCalled();
  });

  it('并发值来自后端，保存走 PATCH 并提示下周期生效', async () => {
    render(<QueuePanel reloadSignal={0} />);

    const input = await screen.findByDisplayValue('3');
    fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(updateConcurrencyMock).toHaveBeenCalledWith(3));
    expect(input).toBeTruthy();
  });

  it('点击重试/重新入队调用对应接口并刷新队列', async () => {
    render(<QueuePanel reloadSignal={0} />);
    await screen.findByText('失败简历.pdf');

    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    await waitFor(() => expect(retryTaskMock).toHaveBeenCalledWith(1));

    const requeueButtons = screen.getAllByRole('button', { name: '重新入队' });
    fireEvent.click(requeueButtons[requeueButtons.length - 1]);
    await waitFor(() => expect(requeueTaskMock).toHaveBeenCalledWith(2));
  });

  it('批次下拉使用 GET /resumes/batches 的数据', async () => {
    render(<QueuePanel reloadSignal={0} />);
    await screen.findByText('失败简历.pdf');

    expect(fetchBatchesMock).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(screen.getByRole('combobox'));
    expect(await screen.findByText('01JABCDE…XYZA · 8 份')).toBeTruthy();
  });
});
