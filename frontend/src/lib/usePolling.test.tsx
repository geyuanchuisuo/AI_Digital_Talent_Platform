import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { POLL_INTERVAL_MS, isTerminalStatus, usePolling } from './usePolling';

type Snapshot = { status: string; progress?: number };

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('isTerminalStatus', () => {
  it('done / failed / cancelled 视为终态', () => {
    expect(isTerminalStatus({ status: 'done' })).toBe(true);
    expect(isTerminalStatus({ status: 'failed' })).toBe(true);
    expect(isTerminalStatus({ status: 'cancelled' })).toBe(true);
  });

  it('running / 非对象不算终态', () => {
    expect(isTerminalStatus({ status: 'running' })).toBe(false);
    expect(isTerminalStatus(null)).toBe(false);
    expect(isTerminalStatus('done')).toBe(false);
  });
});

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it(`默认间隔 ${POLL_INTERVAL_MS}ms 串行轮询`, async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'running' } as Snapshot);
    const { result } = renderHook(() => usePolling(fetcher));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual({ status: 'running' });
    expect(result.current.loading).toBe(false);

    await advance(POLL_INTERVAL_MS - 1);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await advance(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('status=done 后不再轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'done', progress: 100 } as Snapshot);
    const { result } = renderHook(() => usePolling(fetcher));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.stopped).toBe(true);

    await advance(POLL_INTERVAL_MS * 5);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('status=failed 后不再轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'failed' } as Snapshot);
    const { result } = renderHook(() => usePolling(fetcher));

    await flush();
    expect(result.current.stopped).toBe(true);

    await advance(POLL_INTERVAL_MS * 3);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('单次失败不停止轮询，并回调 onError', async () => {
    const boom = new Error('network');
    const onError = vi.fn();
    const fetcher = vi
      .fn<() => Promise<Snapshot>>()
      .mockRejectedValueOnce(boom)
      .mockResolvedValue({ status: 'running' });

    const { result } = renderHook(() => usePolling(fetcher, { onError }));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe(boom);
    expect(onError).toHaveBeenCalledWith(boom);
    expect(result.current.stopped).toBe(false);

    await advance(POLL_INTERVAL_MS);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBeNull();
  });

  it('上一请求未返回时不会并发发起下一次', async () => {
    let resolveFetch: ((value: Snapshot) => void) | undefined;
    const fetcher = vi.fn(
      () =>
        new Promise<Snapshot>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    renderHook(() => usePolling(fetcher));
    await advance(POLL_INTERVAL_MS * 3);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFetch?.({ status: 'running' });
      await Promise.resolve();
    });
    await advance(POLL_INTERVAL_MS);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('卸载后停止轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'running' } as Snapshot);
    const { unmount } = renderHook(() => usePolling(fetcher));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);

    unmount();
    await advance(POLL_INTERVAL_MS * 3);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('stop / refresh / start 可手动控制', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'running' } as Snapshot);
    const { result } = renderHook(() => usePolling(fetcher));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => result.current.stop());
    expect(result.current.stopped).toBe(true);
    await advance(POLL_INTERVAL_MS * 2);
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => result.current.refresh());
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);

    act(() => result.current.stop());
    act(() => result.current.start());
    await advance(0);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('enabled=false 时不发起请求', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'running' } as Snapshot);
    renderHook(() => usePolling(fetcher, { enabled: false }));

    await advance(POLL_INTERVAL_MS * 2);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
