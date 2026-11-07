import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PersonalShell from './PersonalShell';
import { clearAuth, setAuth } from '@/lib/auth';

const replace = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/home',
}));

describe('PersonalShell', () => {
  beforeEach(() => {
    replace.mockReset();
    clearAuth();
  });

  it('渲染个人端品牌条与当前用户', () => {
    setAuth('header.payload.signature', {
      id: 2,
      username: 'personal',
      display_name: '演示个人账号',
      role: 'personal',
      person_id: 398,
    });
    render(
      <PersonalShell>
        <div>个人内容</div>
      </PersonalShell>,
    );

    expect(screen.getByText('个人端')).toBeTruthy();
    expect(screen.getByText('演示个人账号')).toBeTruthy();
    expect(screen.getByText('个人内容')).toBeTruthy();
    // 已绑定档案（person_id=398）不出现未绑定提示
    expect(screen.queryByText(/尚未绑定人才档案/)).toBeNull();
  });

  it('未绑定档案的 personal 账号展示提示', () => {
    setAuth('header.payload.signature', {
      id: 3,
      username: 'personal',
      display_name: '无绑定账号',
      role: 'personal',
      person_id: null,
    });
    render(
      <PersonalShell>
        <div>内容</div>
      </PersonalShell>,
    );
    expect(screen.getByText(/尚未绑定人才档案/)).toBeTruthy();
  });

  it('退出登录清除本地登录态并回登录页', () => {
    setAuth('header.payload.signature', {
      id: 2,
      username: 'personal',
      display_name: '演示个人账号',
      role: 'personal',
      person_id: 398,
    });
    render(
      <PersonalShell>
        <div>内容</div>
      </PersonalShell>,
    );
    fireEvent.click(screen.getByTestId('personal-logout'));
    expect(replace).toHaveBeenCalledWith('/login');
  });
});
