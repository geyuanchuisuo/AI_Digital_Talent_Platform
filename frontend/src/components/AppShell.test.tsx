import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AppShell from './AppShell';
import { clearAuth, getToken, setAuth } from '@/lib/auth';

const push = vi.fn();
const replace = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, prefetch: vi.fn() }),
  usePathname: () => '/screen',
}));

describe('AppShell', () => {
  beforeEach(() => {
    push.mockReset();
    replace.mockReset();
    clearAuth();
    setAuth('header.payload.signature', { id: 1, username: 'hr', display_name: 'HR 管理员', role: 'hr' });
  });

  it('渲染七个业务菜单与当前用户', () => {
    render(
      <AppShell>
        <div>页面内容</div>
      </AppShell>,
    );

    for (const label of [
      '简历筛选',
      '人才画像',
      '知识库',
      '岗位条件',
      '岗位匹配',
      '岗位测评',
      '人工面试',
      '培训成长',
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('HR 管理员')).toBeTruthy();
    expect(screen.getByText('页面内容')).toBeTruthy();
  });

  it('退出登录清除本地登录态并回登录页', async () => {
    render(<AppShell>内容</AppShell>);
    expect(getToken()).not.toBeNull();

    fireEvent.click(screen.getByText('HR 管理员'));
    fireEvent.click(await screen.findByText('退出登录'));

    await waitFor(() => expect(getToken()).toBeNull());
    expect(replace).toHaveBeenCalledWith('/login');
  });
});
