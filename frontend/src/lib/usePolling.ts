'use client';

/**
 * 长任务轮询 hook（T-06）。
 *
 * 口径：架构 §5.7 / 接口 §1.3 —— 前端 **2s** 轮询批次或解析队列，`done` / `failed`
 * 停止；`cancelled`（软取消）同样视为终态，继续轮询没有意义。
 *
 * 三个刻意的设计：
 *   1. **串行**：上一次请求返回后才排下一次（`setTimeout` 链而非 `setInterval`），
 *      后端慢时不会堆积请求；
 *   2. **出错不停**：单次失败只记 `error` 并继续轮询（后端重启/网络抖动能自愈），
 *      由页面的 `onError` 决定是否提示用户；
 *   3. **卸载即停**：清理定时器 + `cancelled` 标记，避免卸载后 setState 警告。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

function readIntervalFromEnv(): number {
  const raw = Number(process.env.NEXT_PUBLIC_POLL_INTERVAL_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : 2000;
}

export const POLL_INTERVAL_MS = readIntervalFromEnv();

/** 终态：`done`/`failed` 见接口 §1.3；`cancelled` 是软取消后的终态 */
export const TERMINAL_STATUSES = ['done', 'failed', 'cancelled'] as const;

export function isTerminalStatus(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  const status = (data as { status?: unknown }).status;
  return typeof status === 'string' && (TERMINAL_STATUSES as readonly string[]).includes(status);
}

export interface UsePollingOptions<T> {
  /** 轮询间隔，默认取 NEXT_PUBLIC_POLL_INTERVAL_MS（回退 2000ms） */
  intervalMs?: number;
  /** false 时不启动（例如弹窗未打开、批次 id 还没拿到） */
  enabled?: boolean;
  /** 是否挂载即拉一次，默认 true */
  immediate?: boolean;
  /** 停止条件，默认 `isTerminalStatus` */
  shouldStop?: (data: T) => boolean;
  onData?: (data: T) => void;
  onError?: (error: unknown) => void;
}

export interface UsePollingResult<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  stopped: boolean;
  /** 立即拉一次并恢复轮询（用于手动刷新） */
  refresh: () => void;
  /** 手动停止 */
  stop: () => void;
  /** 停止后手动恢复 */
  start: () => void;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  options: UsePollingOptions<T> = {},
): UsePollingResult<T> {
  const {
    intervalMs = POLL_INTERVAL_MS,
    enabled = true,
    immediate = true,
    shouldStop = isTerminalStatus,
    onData,
    onError,
  } = options;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [stopped, setStopped] = useState(false);

  // 用 ref 持有最新回调，避免调用方每次渲染新建函数导致轮询重启
  const fetcherRef = useRef(fetcher);
  const shouldStopRef = useRef(shouldStop);
  const onDataRef = useRef(onData);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    fetcherRef.current = fetcher;
    shouldStopRef.current = shouldStop;
    onDataRef.current = onData;
    onErrorRef.current = onError;
  }, [fetcher, shouldStop, onData, onError]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const stoppedRef = useRef(false);
  const runningRef = useRef(false);
  const tickRef = useRef<() => Promise<void>>(async () => undefined);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback(
    (delay: number) => {
      clearTimer();
      if (cancelledRef.current || stoppedRef.current) return;
      timerRef.current = setTimeout(() => {
        void tickRef.current();
      }, delay);
    },
    [clearTimer],
  );

  const tick = useCallback(async () => {
    if (cancelledRef.current || runningRef.current) return;
    runningRef.current = true;
    setLoading(true);
    try {
      const result = await fetcherRef.current();
      if (cancelledRef.current) return;
      const stop = shouldStopRef.current(result);
      stoppedRef.current = stop;
      setData(result);
      setError(null);
      setStopped(stop);
      onDataRef.current?.(result);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err);
      onErrorRef.current?.(err);
    } finally {
      runningRef.current = false;
      if (!cancelledRef.current) setLoading(false);
    }
    schedule(intervalMs);
  }, [intervalMs, schedule]);

  // 先把 tick 挂到 ref 再启动轮询：effect 按声明顺序执行，保证首次 immediate 调用拿到最新闭包
  useEffect(() => {
    tickRef.current = tick;
  }, [tick]);

  useEffect(() => {
    if (!enabled) return;
    cancelledRef.current = false;
    stoppedRef.current = false;
    setStopped(false);
    if (immediate) void tickRef.current();
    else schedule(intervalMs);
    return () => {
      cancelledRef.current = true;
      clearTimer();
    };
  }, [enabled, immediate, intervalMs, schedule, clearTimer]);

  const refresh = useCallback(() => {
    stoppedRef.current = false;
    setStopped(false);
    void tickRef.current();
  }, []);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    setStopped(true);
    clearTimer();
  }, [clearTimer]);

  const start = useCallback(() => {
    stoppedRef.current = false;
    setStopped(false);
    schedule(0);
  }, [schedule]);

  return { data, error, loading, stopped, refresh, stop, start };
}
