import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LoginView from './LoginView';
import { clearAuth, getToken, setAuth } from '@/lib/auth';
import { ApiError, post } from '@/lib/http';

const replace = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/login',
}));

vi.mock('@/lib/http', () => {
  class MockApiError extends Error {
    readonly code: number;
    constructor(code: number, msg: string) {
      super(msg);
      this.name = 'ApiError';
      this.code = code;
    }
  }
  return { post: vi.fn(), ApiError: MockApiError };
});

const postMock = vi.mocked(post);

function makeToken(expSecondsFromNow: number): string {
  const payload = btoa(
    JSON.stringify({ sub: '1', username: 'hr', role: 'hr', exp: Math.floor(Date.now() / 1000) + expSecondsFromNow }),
  );
  return `header.${payload}.signature`;
}

async function fillAndSubmit(username = 'hr', password = 'hr123456'): Promise<void> {
  fireEvent.change(screen.getByPlaceholderText('请输入账号'), { target: { value: username } });
  fireEvent.change(screen.getByPlaceholderText('请输入密码'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));
}

describe('LoginView', () => {
  beforeEach(() => {
    replace.mockReset();
    postMock.mockReset();
    clearAuth();
  });

  it('登录成功写入 token 并跳转默认页', async () => {
    postMock.mockResolvedValue({
      token: 'jwt-token',
      expires_in: 86400,
      user: { id: 1, username: 'hr', display_name: 'HR 管理员', role: 'hr' },
    });

    render(<LoginView redirectParam={null} />);
    await fillAndSubmit(' hr ', 'hr123456');

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/screen'));
    expect(postMock).toHaveBeenCalledWith('/auth/login', { username: 'hr', password: 'hr123456' });
    expect(getToken()).toBe('jwt-token');
  });

  it('登录后回到 redirect 指定的站内页面', async () => {
    postMock.mockResolvedValue({
      token: 'jwt-token',
      expires_in: 86400,
      user: { id: 1, username: 'hr', role: 'hr' },
    });

    render(<LoginView redirectParam="/talents?tag=python" />);
    await fillAndSubmit();

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/talents?tag=python'));
  });

  it.each(['https://evil.example.com', '//evil.example.com'])(
    '非法 redirect（%s）回落到 /screen',
    async (bad) => {
      postMock.mockResolvedValue({
        token: 'jwt-token',
        expires_in: 86400,
        user: { id: 1, username: 'hr', role: 'hr' },
      });

      render(<LoginView redirectParam={bad} />);
      await fillAndSubmit();

      await waitFor(() => expect(replace).toHaveBeenCalledWith('/screen'));
    },
  );

  it('401 时把错误挂到密码框且不写入 token', async () => {
    postMock.mockRejectedValue(new ApiError(401, '用户名或密码错误'));

    render(<LoginView redirectParam={null} />);
    await fillAndSubmit('hr', 'wrong-password');

    expect(await screen.findByText('用户名或密码错误')).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it('已有有效 token 时直接跳转，不再让用户登录', async () => {
    setAuth(makeToken(3600), { id: 1, username: 'hr', role: 'hr' });

    render(<LoginView redirectParam={null} />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/screen'));
    expect(postMock).not.toHaveBeenCalled();
  });
});
