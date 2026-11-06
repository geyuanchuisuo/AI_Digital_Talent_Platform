import AuthGuard from '@/components/AuthGuard';
import AppShell from '@/components/AppShell';

/**
 * 业务页分组布局（T-06）：`(main)` 是路由组，不进 URL。
 * 所有需要登录的页面放这里，统一被 AuthGuard 保护 + AppShell 包裹。
 * 登录页在 `src/app/login`，不在本组内，因此不会被守卫拦截（否则死循环）。
 */
export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
