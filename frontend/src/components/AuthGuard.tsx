'use client';

/**
 * 路由守卫（T-06 落地 / T-09 补服务端校验）。
 *
 * 两级校验，缺一不可：
 *   一级（本地）：token 是否存在、解出的 `exp` 是否过期 —— 零成本，拦掉明显失效的 token；
 *   二级（服务端）：`GET /auth/me` 确认签名有效、账号未被停用 —— 拦住"本地 exp 还新鲜，
 *     但 token 已被篡改 / 账号已被停用"的情况（这两种恰恰是 T-09 要求必须回登录页的）。
 *
 * 为什么必须补二级：本地 `exp` 只读 payload、不验签（验签是后端职责），所以
 * 篡改过 payload 之外的部分、或登录后被管理员停用，一级检查都会放行。一期页面
 * 大多不发起业务请求，光靠"请求回来 401 再踢"会出现"能进空壳、看不到提示"的洞。
 *
 * 三级降级：`/auth/me` 因网络异常 / 5xx 失败时**放行**（后端故障不该把用户锁在门外，
 * 真正的数据仍由后端逐请求鉴权）；只有 401 / 403 才停在校验态，由 `lib/http.ts`
 * 统一清态 + 提示 + `location.replace` 到登录页。
 *
 * 请求开销：同一 token 在一次页面生命周期内只校验一次（模块级缓存），
 * 站内路由切换不会重复请求；刷新 / 401 跳转后自然重置。
 */

import { Spin } from 'antd';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { clearAuth, getToken, isTokenExpired, setAuth, AuthUser } from '@/lib/auth';
import { ApiError, get } from '@/lib/http';

/** 已通过服务端校验的 token（同一页面生命周期内只验一次） */
let verifiedToken: string | null = null;
let inflight: { token: string; promise: Promise<boolean> } | null = null;

/** 仅供单测使用：清掉模块级缓存，避免用例间互相影响 */
export function __resetGuardCache(): void {
  verifiedToken = null;
  inflight = null;
}

function isAuthFailure(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  return err.code === 401 || err.code === 403 || err.status === 401 || err.status === 403;
}

/**
 * 服务端确认一次登录态。
 * @returns true = 可以渲染子内容；false = 401/403，保持 Spin 等 `lib/http.ts` 跳登录。
 */
async function verifySession(token: string): Promise<boolean> {
  if (verifiedToken === token) return true;
  if (!inflight || inflight.token !== token) {
    inflight = {
      token,
      promise: get<AuthUser>('/auth/me')
        .then((user) => {
          verifiedToken = token;
          // 顺手刷新本地用户信息（管理员改过显示名 / 角色时，页头不会一直显示旧值）
          if (user && typeof user === 'object') setAuth(token, user);
          return true;
        })
        .finally(() => {
          inflight = null;
        }),
    };
  }
  try {
    return await inflight.promise;
  } catch (err) {
    // 401/403 已由 lib/http.ts 清态 + 提示 + 跳转，这里只负责不渲染业务内容
    if (isAuthFailure(err)) return false;
    // 网络异常 / 5xx：降级放行
    return true;
  }
}

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token || isTokenExpired(token)) {
      const hadToken = Boolean(token);
      clearAuth();
      const redirect = encodeURIComponent(pathname || '/screen');
      // 首次访问（本来就没 token）静默跳转；"带着过期 token"才提示 —— 提示由登录页读
      // `reason=expired` 渲染成提示条，不用 message（整页跳转后 message 会一起消失）
      const reason = hadToken ? '&reason=expired' : '';
      router.replace(`/login?redirect=${redirect}${reason}`);
      return;
    }

    let alive = true;
    void verifySession(token).then((ok) => {
      if (alive && ok) setChecked(true);
    });
    return () => {
      alive = false;
    };
  }, [pathname, router]);

  if (!checked) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
        }}
      >
        <Spin size="large" />
        <span style={{ color: 'rgba(0,0,0,0.45)' }}>正在校验登录状态...</span>
      </div>
    );
  }

  return <>{children}</>;
}
