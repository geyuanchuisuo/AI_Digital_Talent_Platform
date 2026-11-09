import { beforeEach, describe, expect, it, vi } from 'vitest';

import { get } from '@/lib/http';
import { fetchFeedback } from './api';

vi.mock('@/lib/http', () => ({ get: vi.fn() }));

const getMock = vi.mocked(get);

describe('analytics feedback API', () => {
  beforeEach(() => getMock.mockReset());

  it('reads items from the backend response envelope', async () => {
    const items = [{
      id: 7, snapshot_id: 2, comment: '口径存疑', created_by: 2,
      created_at: '2026-11-09T00:00:00', snapshot_metric_id: 'jobs.active',
    }];
    getMock.mockResolvedValue({ items });
    await expect(fetchFeedback()).resolves.toEqual(items);
    expect(getMock).toHaveBeenCalledWith('/analytics/feedback');
  });

  it('returns the empty items array without passing an object to the list', async () => {
    getMock.mockResolvedValue({ items: [] });
    await expect(fetchFeedback()).resolves.toEqual([]);
  });

  it('propagates authorization and network failures', async () => {
    getMock.mockRejectedValueOnce(new Error('403'));
    let failure: unknown;
    try {
      await fetchFeedback();
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe('403');
  });
});
