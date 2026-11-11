/**
 * 工作流接口封装（T-D15 受控生命周期）。走 `lib/http.ts`。
 * 权限口径：全部端点管理侧门禁（403 由 http 统一提示）；
 * 四眼原则（提交人不可自行审核发布）由后端强制，前端透传 403 文案。
 * 节点/连线编辑一期走 JSON 表单输入（服务端整图校验兜底），画布编辑器归 T-D16。
 */

// http.ts 无 put 封装（A 域文件不改），PUT 用底层 request 直调
import { get, post, request } from '@/lib/http';

export type WorkflowVersionStatus = 'draft' | 'pending_review' | 'published' | 'deprecated';

export interface WorkflowDefinition {
  id: number;
  code: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface WorkflowVersion {
  id: number;
  version: number;
  status: WorkflowVersionStatus;
  note: string | null;
  submitted_by: number | null;
  submitted_at: string | null;
  review_note: string | null;
  published_at: string | null;
}

export interface WorkflowNodeDetail {
  node_key: string;
  name: string;
  node_type: string;
  position: Record<string, unknown> | null;
  config: Record<string, unknown> | null;
  inputs: Array<Record<string, unknown>>;
  outputs: Array<Record<string, unknown>>;
}

export interface WorkflowEdgeDetail {
  source: string;
  target: string;
  branch: string | null;
  condition: Record<string, unknown> | null;
}

export interface VersionDetail {
  definition: { id: number; code: string; name: string };
  version: WorkflowVersion;
  nodes: WorkflowNodeDetail[];
  edges: WorkflowEdgeDetail[];
}

export function fetchDefinitions(): Promise<WorkflowDefinition[]> {
  return get<WorkflowDefinition[]>('/workflows/definitions').then((resp) => resp ?? []);
}

export function createDefinition(body: {
  code: string;
  name: string;
  description?: string;
}): Promise<{ id: number; code: string; name: string }> {
  return post<{ id: number; code: string; name: string }>('/workflows/definitions', body);
}

export function fetchVersions(definitionId: number): Promise<WorkflowVersion[]> {
  return get<WorkflowVersion[]>(`/workflows/definitions/${definitionId}/versions`).then(
    (resp) => resp ?? [],
  );
}

export function createDraftVersion(
  definitionId: number,
  body: { nodes: unknown[]; edges: unknown[]; note?: string },
): Promise<{ id: number; version: number; status: string }> {
  return post(`/workflows/definitions/${definitionId}/versions`, body);
}

export function fetchVersionDetail(versionId: number): Promise<VersionDetail> {
  return get<VersionDetail>(`/workflows/versions/${versionId}`);
}

export function updateDraftGraph(
  versionId: number,
  body: { nodes: unknown[]; edges: unknown[] },
): Promise<{ id: number; version: number; status: string }> {
  return request({ method: 'PUT', url: `/workflows/versions/${versionId}/graph`, data: body });
}

export function submitVersion(
  versionId: number,
): Promise<{ id: number; status: string; submitted_by: number | null }> {
  return post(`/workflows/versions/${versionId}/submit`, {});
}

export function reviewVersion(
  versionId: number,
  approve: boolean,
  note?: string,
): Promise<{ id: number; status: string; review_note: string | null }> {
  return post(`/workflows/versions/${versionId}/review`, {
    approve,
    note: note ?? null,
  });
}

export function deactivateVersion(
  versionId: number,
): Promise<{ id: number; status: string }> {
  return post(`/workflows/versions/${versionId}/deactivate`, {});
}

export function rollbackDefinition(
  definitionId: number,
  targetVersionId: number,
): Promise<{ id: number; version: number; status: string }> {
  return post(`/workflows/definitions/${definitionId}/rollback`, {
    target_version_id: targetVersionId,
  });
}

export interface WorkflowRunSummary {
  id: number;
  definition_id: number;
  version_id: number;
  thread_id: string;
  trigger_type: 'manual' | 'event' | 'schedule';
  status: 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled';
  error: string | null;
  created_by: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface WorkflowRunDetail {
  run: WorkflowRunSummary & { trigger_ref: string | null; params: Record<string, unknown> | null };
  steps: Array<{
    node_key: string;
    node_type: string;
    attempt: number;
    status: string;
    output: Record<string, unknown> | null;
    error: string | null;
    started_at: string | null;
    finished_at: string | null;
  }>;
  engine_state?: { next: string[] } | null;
}

export function fetchRuns(definitionId?: number): Promise<WorkflowRunSummary[]> {
  const query = definitionId == null ? '' : `?definition_id=${definitionId}`;
  return get<WorkflowRunSummary[]>(`/workflows/runs${query}`);
}

export function fetchRunDetail(threadId: string): Promise<WorkflowRunDetail> {
  return get<WorkflowRunDetail>(`/workflows/runs/${encodeURIComponent(threadId)}`);
}

export function startRun(definitionId: number, params: Record<string, unknown>) {
  return post<{ run_id: number; thread_id: string; status: string; interrupt: unknown }>(
    '/workflows/runs', { definition_id: definitionId, params },
  );
}

export function approveRun(threadId: string, approved: boolean, note: string) {
  return post(`/workflows/runs/${encodeURIComponent(threadId)}/approval`, { approved, note });
}

export function cancelRun(threadId: string) {
  return post(`/workflows/runs/${encodeURIComponent(threadId)}/cancel`, {});
}

export function cancelRunningRun(threadId: string) {
  return post(`/workflows/runs/${encodeURIComponent(threadId)}/cancel-running`, {});
}

export function retryRun(threadId: string) {
  return post<{ run_id: number; thread_id: string; status: string }>(
    `/workflows/runs/${encodeURIComponent(threadId)}/retry`, {},
  );
}

// ---- T-D16/T-D10：节点元数据 + Dify DSL 导出/显式导入/对账（D 域画布对接）----

export interface NodeTypeMeta {
  type: string;
  label: string;
}

export function fetchNodeTypes(): Promise<{
  node_types: NodeTypeMeta[];
  version_statuses: string[];
  trigger_types: string[];
}> {
  return get('/workflows/meta/node-types');
}

export interface DifyDslExport {
  version_id: number;
  dsl_version: string;
  app_name: string;
  node_count: number;
  edge_count: number;
  yaml: string;
}

export function exportDifyDsl(versionId: number): Promise<DifyDslExport> {
  return get<DifyDslExport>(`/workflows/versions/${versionId}/dify-dsl`);
}

export function importDifyDsl(
  definitionId: number,
  yaml: string,
): Promise<{ definition_id: number; version_id: number; version: number; status: string; node_count: number; edge_count: number }> {
  return post(`/workflows/definitions/${definitionId}/dify-import`, { yaml });
}

export interface DifyReconcileReport {
  version_id: number;
  identical: boolean;
  nodes: { added: string[]; removed: string[]; changed: Array<{ node_key: string; fields: string[] }> };
  edges: { added: Array<{ source: string; target: string; branch: string | null }>; removed: Array<{ source: string; target: string; branch: string | null }>; changed: Array<{ source: string; target: string; branch: string | null; fields: string[] }> };
}

export function reconcileDifyDsl(versionId: number, yaml: string): Promise<DifyReconcileReport> {
  return post<DifyReconcileReport>(`/workflows/versions/${versionId}/dify-reconcile`, { yaml });
}
