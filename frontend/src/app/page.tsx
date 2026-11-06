import { redirect } from 'next/navigation';

/** 根路径统一进入简历筛选（默认首页）；未登录由 (main) 分组的 AuthGuard 拦到 /login */
export default function HomePage() {
  redirect('/screen');
}
