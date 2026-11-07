import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AnalyticsPage from './page';
import { fetchActiveJobs, streamActiveJobsEvents } from './api';
import type { ActiveJobsData } from './api';

vi.mock('./api', () => ({
  fetchActiveJobs: vi.fn(),
  streamActiveJobsEvents: vi.fn(),
}));

const fetchMock = vi.mocked(fetchActiveJobs);
const streamMock = vi.mocked(streamActiveJobsEvents);

function data(patch: Partial<ActiveJobsData> = {}): ActiveJobsData {
  return {
    metric_id: 'jobs.active',
    name: '活跃岗位数',
    value: 3,
    source: 'replica',
    cache_hit: false,
    data_time: '2026-11-08T00:00:00',
    snapshot_id: 1,
    ...patch,
  };
}

describe('AnalyticsPage（T-D01b 切片）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockResolvedValue(data());
  });

  it('渲染指标值与来源/缓存标记', async () => {
    const { container } = render(<AnalyticsPage />);
    await waitFor(() =>
      expect(container.querySelector('.ant-statistic-content-value')?.textContent).toBe('3'),
    );
    expect(screen.getByText('来源：PgSQL 副本')).toBeTruthy();
    expect(screen.getByText('缓存未命中')).toBeTruthy();
  });

  it('缓存命中时显示命中标记', async () => {
    fetchMock.mockResolvedValue(data({ cache_hit: true, source: 'primary' }));
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText('缓存命中')).toBeTruthy());
    expect(screen.getByText('来源：主库直查')).toBeTruthy();
  });

  it('SSE 按钮消费流并显示最终阶段', async () => {
    streamMock.mockImplementation(async (onEvent) => {
      onEvent('progress', { stage: 'started' });
      onEvent('progress', { stage: 'computed', value: 3 });
      onEvent('done', { value: 3 });
    });
    render(<AnalyticsPage />);
    const sseButton = await waitFor(() => {
      const btn = screen.getByText('计算进度（SSE）').closest('button') as HTMLButtonElement;
      expect(btn.disabled).toBeFalsy();
      return btn;
    });
    fireEvent.click(sseButton);
    await waitFor(() => expect(screen.getByText('done')).toBeTruthy());
    expect(streamMock).toHaveBeenCalledTimes(1);
  });
});
