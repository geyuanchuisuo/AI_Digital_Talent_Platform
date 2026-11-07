/**
 * 登录态存储（T-06）。
 *
 * 口径：`docs/接口设计.md` v1.3 §2 —— 登录返回 `{token, expires_in, user}`，
 * 前端把 token 放 localStorage 并由 axios 统一注入 `Authorization: Bearer`。
 *
 * 为什么不存 cookie：一期单页后台、无 SSR 数据依赖，localStorage 足够；
 * 也避免与后端 CORS（`allow_credentials=false` 当 origins=*）打架。
 * ⚠️ 路由守卫只在客户端生效，因此所有业务页面都在 `(main)` 分组下，由 AuthGuard 包裹。
 */

export interface AuthUser {
  id: number;
  username: string;
  display_name?: string | null;
  role: string;
  /** 绑定的主档 id（个人账号唯一；管理侧为 null）——2026-11-07 贯通 */
  person_id?: number | null;
}

const TOKEN_KEY = 'ai_talent_token';
const USER_KEY = 'ai_talent_user';

const hasWindow = (): boolean => typeof window !== 'undefined';

export function getToken(): string | null {
  if (!hasWindow()) return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUser(): AuthUser | null {
  if (!hasWindow()) return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function setAuth(token: string, user: AuthUser): void {
  if (!hasWindow()) return;
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuth(): void {
  if (!hasWindow()) return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

/**
 * 本地判断 JWT 是否过期（只解 payload，不验签——验签是后端职责）。
 * 作用：守卫阶段就能拦掉明显过期的 token，避免先渲染页面再被 401 打回。
 */
export function isTokenExpired(token: string, skewSeconds = 30): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== 'number') return true;
  return payload.exp * 1000 <= Date.now() + skewSeconds * 1000;
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const binary = window.atob(padded);
    // 处理非 ASCII（payload 目前只有 ASCII，做兼容避免异常）
    const json = decodeURIComponent(
      Array.from(binary)
        .map((ch) => `%${ch.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    );
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}
