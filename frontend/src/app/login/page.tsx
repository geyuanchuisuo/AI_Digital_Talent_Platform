import LoginView from './LoginView';

/**
 * 登录页（T-08 / T-09）。
 *
 * 用 server component 读 `searchParams` 再传给客户端视图：
 * 比在客户端用 `useSearchParams` 少一层 Suspense 边界，也不会触发 CSR bailout。
 * 登录页**不在** `(main)` 路由组内，因此不会被 AuthGuard 拦截（否则死循环）。
 *
 * 两个查询参数：
 *  - `redirect`：登录成功后回跳的站内路径（缺省 `/screen`）；
 *  - `reason`：被动跳过来的原因（`expired` 过期 / `disabled` 停用），
 *    用于在页面上**持久**显示提示条 —— antd 的 message 在整页跳转后就没了，
 *    只在跳转途中闪一下等于没提示（T-09 集成自测发现）。
 */
export default function LoginPage({
  searchParams,
}: {
  searchParams: { redirect?: string | string[]; reason?: string | string[] };
}) {
  const redirectParam = pickFirst(searchParams?.redirect);
  const reasonParam = pickFirst(searchParams?.reason);
  return <LoginView redirectParam={redirectParam} reasonParam={reasonParam} />;
}

/** `searchParams` 的同名参数出现两次时是数组，统一取第一个 */
function pickFirst(raw: string | string[] | undefined): string | null {
  if (Array.isArray(raw)) return raw[0] ?? null;
  return raw ?? null;
}
