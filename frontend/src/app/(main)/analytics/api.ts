/**
 * 问数接口封装（T-D01b 切片 + T-D10 多维/图表/解读）。走 `lib/http.ts`；
 * SSE 用 fetch 流式读取（EventSource 不支持自定义 Authorization 头）。
 */

import { get, post, API_PREFIX } from '@/lib/http';
import { getToken } from '@/lib/auth';

export interface ChartSpec {
  chart_type: 'bar' | 'number' | 'none';
  label?: string;
  value?: number | string | null;
  x_field?: string;
  y_field?: string;
  data?: Array<{ name: string; value: number | string | null }>;
  truncated_series?: boolean;
  reason?: string;
}

export interface InterpretationSegment {
  kind: 'fact' | 'inference';
  key: string;
  text: string;
  ref?: Record<string, unknown>;
  basis?: string[];
}

export interface Interpretation {
  segments: InterpretationSegment[];
  generator: 'rule' | 'llm';
}

export interface MetricData {
  metric_id: string;
  name: string;
  value: number | null;
  source: 'replica' | 'primary';
  cache_hit: boolean;
  data_time: string;
  snapshot_id: number | null;
  table: { columns: string[]; rows: Array<Record<string, unknown>> } | null;
  note: string | null;
  group_by: string | null;
  chart: ChartSpec | null;
  interpretation: Interpretation | null;
}

export type ActiveJobsData = MetricData;

export function fetchActiveJobs(groupBy?: 'department'): Promise<ActiveJobsData> {
  const qs = groupBy ? `?group_by=${groupBy}` : '';
  return get<ActiveJobsData>(`/analytics/metrics/jobs/active${qs}`);
}

export interface MetricFeedbackItem {
  id: number;
  snapshot_id: number;
  comment: string | null;
  created_by: number;
  created_at: string;
  snapshot_metric_id: string | null;
  snapshot?: {
    metric_id: string;
    params_json: Record<string, unknown> | null;
    data_time: string | null;
    result_json: Record<string, unknown> | null;
  };
}

export function createFeedback(
  snapshotId: number,
  comment?: string,
): Promise<MetricFeedbackItem> {
  return post<MetricFeedbackItem>('/analytics/feedback', {
    snapshot_id: snapshotId,
    comment: comment ?? null,
  });
}

export function fetchFeedback(): Promise<MetricFeedbackItem[]> {
  return get<{ items: MetricFeedbackItem[] }>('/analytics/feedback').then(
    (resp) => resp.items,
  );
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
