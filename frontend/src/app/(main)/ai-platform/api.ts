/**
 * AI 中台管理面接口封装（T-D20）。走 `lib/http.ts`；后端契约见 docs/modules/D/工作台契约.md。
 * 权限：管理侧专用（personal 账号 403 由 http 统一提示）；供应商密钥仅后端落库不回显（验收①）。
 */

import { get, post, patch } from '@/lib/http';

// ---- 供应商 ----

export interface ProviderInfo {
  id: number;
  name: string;
  provider_type: string;
  base_url: string;
  model: string;
  enabled: boolean;
  is_default: boolean;
  timeout_seconds: number;
  note: string | null;
  updated_by: number | null;
  has_api_key: boolean;
}

export function fetchProviders(): Promise<ProviderInfo[]> {
  return get<ProviderInfo[]>('/ai-platform/providers');
}

export function createProvider(payload: {
  name: string;
  provider_type: string;
  base_url: string;
  model: string;
  api_key: string;
  timeout_seconds: number;
  note?: string;
}): Promise<ProviderInfo> {
  return post<ProviderInfo>('/ai-platform/providers', payload);
}

export function updateProvider(providerId: number, payload: Partial<Omit<ProviderInfo, 'id'>>): Promise<ProviderInfo> {
  return patch<ProviderInfo>(`/ai-platform/providers/${providerId}`, payload);
}

export function setProviderEnabled(providerId: number, enabled: boolean): Promise<ProviderInfo> {
  return post<ProviderInfo>(`/ai-platform/providers/${providerId}/enabled`, { enabled });
}

export function setDefaultProvider(providerId: number): Promise<ProviderInfo> {
  return post<ProviderInfo>(`/ai-platform/providers/${providerId}/default`);
}

// ---- Prompt 版本库 ----

export interface PromptVersionInfo {
  id: number;
  name: string;
  version: number;
  status: 'draft' | 'published' | 'deprecated';
  content: string;
  note: string | null;
  created_by: number | null;
  published_by: number | null;
  published_at: string | null;
}

export function fetchPrompts(name?: string): Promise<PromptVersionInfo[]> {
  return get<PromptVersionInfo[]>('/ai-platform/prompts', { params: { name } });
}

export function createPrompt(payload: { name: string; content: string; note?: string }): Promise<PromptVersionInfo> {
  return post<PromptVersionInfo>('/ai-platform/prompts', payload);
}

export function updatePrompt(versionId: number, payload: { content: string }): Promise<PromptVersionInfo> {
  return patch<PromptVersionInfo>(`/ai-platform/prompts/${versionId}`, payload);
}

export function publishPrompt(versionId: number): Promise<PromptVersionInfo> {
  return post<PromptVersionInfo>(`/ai-platform/prompts/${versionId}/publish`);
}

// ---- 调用日志与看板 ----

export interface UsageSummary {
  days: number;
  total_calls: number;
  total_errors: number;
  error_rate: number;
  avg_latency_ms: number;
  by_purpose: Array<{
    purpose: string;
    ok: number;
    error: number;
    total_latency_ms: number;
    prompt_tokens: number;
    completion_tokens: number;
    calls: number;
    error_rate: number;
    avg_latency_ms: number;
  }>;
}

export function fetchUsageSummary(days = 7): Promise<UsageSummary> {
  return get<UsageSummary>('/ai-platform/usage', { params: { days } });
}

export interface CallLogItem {
  id: number;
  provider_name: string;
  model: string;
  purpose: string;
  result: 'ok' | 'error';
  latency_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  error: string | null;
  actor_user_id: number | null;
  created_at: string | null;
}

export function fetchCallLogs(params: {
  days?: number;
  purpose?: string;
  result?: 'ok' | 'error';
  limit?: number;
}): Promise<CallLogItem[]> {
  return get<CallLogItem[]>('/ai-platform/call-logs', { params });
}

export interface AgentRunStat {
  definition_id: number;
  name: string;
  code: string;
  by_trigger: Record<string, number>;
  by_status: Record<string, number>;
  total: number;
}

export function fetchAgentRuns(days = 7): Promise<{ days: number; agents: AgentRunStat[] }> {
  return get<{ days: number; agents: AgentRunStat[] }>('/ai-platform/agents/runs', { params: { days } });
}
