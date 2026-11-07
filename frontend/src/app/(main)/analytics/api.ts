/**
 * 问数切片接口封装（T-D01b）。走 `lib/http.ts`；
 * SSE 用 fetch 流式读取（EventSource 不支持自定义 Authorization 头）。
 */

import { get, API_PREFIX } from '@/lib/http';
import { getToken } from '@/lib/auth';

export interface ActiveJobsData {
  metric_id: string;
  name: string;
  value: number;
  source: 'replica' | 'primary';
  cache_hit: boolean;
  data_time: string;
  snapshot_id: number | null;
}

export function fetchActiveJobs(): Promise<ActiveJobsData> {
  return get<ActiveJobsData>('/analytics/metrics/jobs/active');
}

export type SseStage = 'started' | 'computed' | 'done';

export async function streamActiveJobsEvents(
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const resp = await fetch(`${API_PREFIX}/analytics/metrics/jobs/active/events`, {
    headers: { Authorization: `Bearer ${getToken() ?? ''}` },
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const event = /event: (.+)/.exec(block)?.[1] ?? 'message';
      const dataLine = /data: (.+)/.exec(block)?.[1];
      if (dataLine) onEvent(event, JSON.parse(dataLine));
    }
  }
}
