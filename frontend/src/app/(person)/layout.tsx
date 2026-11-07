import AuthGuard from '@/components/AuthGuard';
import PersonalShell from '@/components/PersonalShell';

/**
 * 个人端页分组布局（2026-11-07，功能 4）：`(person)` 是路由组，不进 URL。
 * 个人侧页面（本人档案、学习记录、测评等）放这里，统一被 AuthGuard 保护 +
 * PersonalShell 包裹；登录页在 `src/app/login`，不会被本组守卫拦截。
 * B/C 的个人侧组件挂载在本组页面内，接口数据可见性以后端 AccountScope 为准。
 */
export default function PersonLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <PersonalShell>{children}</PersonalShell>
    </AuthGuard>
  );
}
