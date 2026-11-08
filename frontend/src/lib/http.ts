/**
 * axios 统一封装（T-06）。
 *
 * 口径：`docs/接口设计.md` v1.3 §1、`docs/统一错误码与鉴权.md` v1.2 §一/§二。
 * 职责固定为四件事，业务页面不得绕过它直接 `axios.get`：
 *   1. 注入 `Authorization: Bearer <token>`；
 *   2. `code !== 0` → 弹出后端中文 message 并 reject（前端按 code 分支，按 message 展示）；
 *   3. 401 → 清登录态 + 跳 `/login?redirect=...`；403 → 仅当「认证探针」
 *      （X-Auth-Probe，来自 AuthGuard 的会话校验）或响应为账号停用语义时才踢登录页；
 *      业务接口的授权 403（如「仅员工可查看本人计划」）只 reject 给页面处理，不杀会话；
 *   4. blob 下载封装（先判"其实是 JSON 错误体"再落盘）。
 *
 * 注意：`GET /api/health` 不套统一返回体，因此只处理"长得像信封"的响应，
 * 其余原样返回，避免把健康检查/文件流误判为业务失败。
 */

import axios, { AxiosError, AxiosRequestConfig, AxiosResponse } from 'axios';
import { message } from 'antd';

import { clearAuth, getToken } from './auth';
import type { ApiResponse } from './types';

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000').replace(/\/+$/, '');
/** 后端基址统一带 `/api`（接口设计 §1），业务代码只写 `/talents` 这样的相对路径 */
export const API_PREFIX = `${API_BASE}/api`;

export class ApiError extends Error {
  readonly code: number;
  readonly status?: number;
  readonly requestId?: string;

  constructor(code: number, msg: string, extra?: { status?: number; requestId?: string }) {
    super(msg);
    this.name = 'ApiError';
    this.code = code;
    this.status = extra?.status;
    this.requestId = extra?.requestId;
  }
}

export const http = axios.create({
  baseURL: API_PREFIX,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

function isApiEnvelope(value: unknown): value is ApiResponse<unknown> {
  return typeof value === 'object' && value !== null && typeof (value as { code?: unknown }).code === 'number';
}

function isLoginPage(): boolean {
  return typeof window !== 'undefined' && window.location.pathname.startsWith('/login');
}

let redirecting = false;

/** 401/403 → 登录页提示条的 `reason` 值（登录页按它选文案，不透传后端原文，避免把文本塞进 URL） */
function reasonOf(code: number): 'disabled' | 'expired' {
  return code === 403 ? 'disabled' : 'expired';
}

/**
 * 业务 403 与认证 403 的区分（2026-11-08，验收缺陷修复）：
 * - 后端对 403 只有一个码（H-A08 白名单），语义靠 message 区分——「账号已停用」
 *   是账号级停用（踢登录），其余 403 是业务授权拒绝（页面内处理）。
 * - AuthGuard 的会话探针带 X-Auth-Probe 头，其 403 视为会话失效（踢登录）。
 */
const ACCOUNT_DISABLED_MESSAGE = '账号已停用';

function shouldKickSession(code: number, message: string, isAuthProbe: boolean): boolean {
  if (code === 401) return true;
  if (code !== 403) return false;
  return isAuthProbe || message === ACCOUNT_DISABLED_MESSAGE;
}

/**
 * 401/403 的统一处置：清态 + 回登录页（带 redirect，登录后能回到原页）。
 *
 * 提示为什么落在登录页而不是这里的 message：跳转是整页 `location.replace`，
 * 旧文档上的 antd message 会随页面销毁，用户只看到闪一下（T-09 集成自测发现）。
 * 因此 message 只作为"离开前"的即时反馈，真正的提示由登录页读 `reason` 渲染。
 */
function redirectToLogin(code: number, reason: string): void {
  if (typeof window === 'undefined') return;
  clearAuth();
  if (isLoginPage() || redirecting) {
    message.error(reason);
    return;
  }
  redirecting = true;
  message.error(reason);
  const redirect = encodeURIComponent(window.location.pathname + window.location.search);
  const reasonKey = reasonOf(code);
  // 留 300ms 让 message 可见；用 location.replace 避免用户点返回又回到失效页
  window.setTimeout(
    () => window.location.replace(`/login?redirect=${redirect}&reason=${reasonKey}`),
    300,
  );
}

function notifyBizError(code: number, msg: string, isAuthProbe = false): void {
  const text = msg || '请求失败';
  if (shouldKickSession(code, text, isAuthProbe)) {
    if (isLoginPage()) message.error(text);
    else redirectToLogin(code, text);
    return;
  }
  // 业务授权 403：页面内提示，不清登录态、不踢登录页
  message.error(text);
}

http.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  if (config.headers.get('X-Auth-Probe')) config.headers.set('X-Auth-Probe', '1');

  // multipart 上传必须先删掉实例默认的 JSON 头。
  // axios 1.21 的 transformRequest 在 Content-Type 含 application/json 时，会把 FormData
  // 转成 JSON（formDataToJSON），File 丢成 {} → 实际发出 {"files":{}}，后端 422「请求参数结构不合法」。
  // 删掉后 transformRequest 原样保留 FormData，浏览器自动补 multipart/form-data; boundary=...。
  // 请求拦截器早于 transformRequest 执行，因此这里是唯一有效的修正点。
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    config.headers.delete('Content-Type');
  }
  return config;
});

http.interceptors.response.use(
  (response: AxiosResponse) => {
    const body: unknown = response.data;
    if (!isApiEnvelope(body)) return response; // health / blob 等非统一体
    if (body.code === 0) return response;
    const isAuthProbe = response.config.headers.get('X-Auth-Probe') === '1';
    notifyBizError(body.code, body.message, isAuthProbe);
    throw new ApiError(body.code, body.message, { status: response.status, requestId: body.request_id });
  },
  (error: AxiosError<ApiResponse<null>>) => {
    const status = error.response?.status;
    const body = error.response?.data;
    const code = typeof body?.code === 'number' ? body.code : (status ?? 0);
    const text = body?.message || fallbackMessage(status ?? 0, error);
    const isAuthProbe = error.config?.headers?.get?.('X-Auth-Probe') === '1';
    notifyBizError(code, text, isAuthProbe);
    return Promise.reject(new ApiError(code, text, { status, requestId: body?.request_id }));
  },
);

function fallbackMessage(status: number, error: AxiosError): string {
  if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试';
  if (!error.response) return '无法连接后端服务，请确认后端已启动';
  return status >= 500 ? '服务器内部错误' : `请求失败（${status}）`;
}

/** 唯一请求入口：返回 `data` 字段，业务代码不接触 code/message 信封 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<ApiResponse<T>>(config);
  return response.data.data;
}

export const get = <T>(url: string, config?: AxiosRequestConfig) => request<T>({ ...config, method: 'GET', url });

/** 带 implcit 请求头的 GET（AuthGuard 会话探针用：403 才踢登录，业务 403 不踢） */
export const getWithHeaders = <T>(url: string, headers: Record<string, string>, config?: AxiosRequestConfig) =>
  request<T>({ ...config, method: 'GET', url, headers });
export const post = <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
  request<T>({ ...config, method: 'POST', url, data });
export const patch = <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
  request<T>({ ...config, method: 'PATCH', url, data });
export const del = <T>(url: string, config?: AxiosRequestConfig) => request<T>({ ...config, method: 'DELETE', url });

/**
 * 文件下载（接口 §5 resume-file）：后端返回 blob，出错时返回统一错误体。
 * axios 在 responseType=blob 下会把 JSON 错误也包成 Blob，因此这里手动识别。
 */
export async function downloadFile(url: string, fallbackName = 'download'): Promise<void> {
  const response = await http.get<Blob>(url, { responseType: 'blob' });
  const blob = response.data;
  if (blob.type.includes('application/json')) {
    const text = await blob.text();
    let code = 500;
    let msg = '下载失败';
    try {
      const body = JSON.parse(text) as ApiResponse<null>;
      code = body.code;
      msg = body.message || msg;
    } catch {
      /* 非 JSON 内容，按通用失败处理 */
    }
    notifyBizError(code, msg);
    throw new ApiError(code, msg, { status: response.status });
  }

  const filename = parseContentDisposition(response.headers['content-disposition']) ?? fallbackName;
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function parseContentDisposition(header: unknown): string | null {
  if (typeof header !== 'string') return null;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ''));
    } catch {
      return null;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

/** 统一取用户可读错误文案（页面 catch 后直接 message.error(getErrorMessage(e))） */
export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '请求失败，请稍后重试';
}
