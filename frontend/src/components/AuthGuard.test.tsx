import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AuthGuard, { __resetGuardCache } from './AuthGuard';
import { setAuth } from '@/lib/auth';

const replace = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/talents',
}));

// 守卫二级校验会调 GET /auth/me，这里整体替身（ApiError 也要替，否则 instanceof 对不上）
vi.mock('@/lib/http', () => {
  class ApiError extends Error {
    readonly code: number;
    readonly status?: number;
    constructor(code: number, msg: string, extra?: { status?: number }) {
      super(msg);
      this.name = 'ApiError';
      this.code = code;
      this.status = extra?.status;
    }
  }
  return { ApiError, get: vi.fn(), API_BASE: 'http://test', API_PREFIX: 'http://test/api' };
});

import { ApiError, get } from '@/lib/http';

const mockGet = vi.mocked(get);

/** 造三段式 token（守卫只解 payload 的 exp，不验签）。注意必须保持正好 3 段，多加 `.` 会被判为无效 */
function makeToken(expSecondsFromNow: number): string {
  const payload = btoa(JSON.stringify({ sub: '1', exp: Math.floor(Date.now() / 1000) + expSecondsFromNow }));
  return `header.${payload}.signature`;
}

describe('AuthGuard', () => {
  beforeEach(() => {
    replace.mockReset();
    mockGet.mockReset();
    window.localStorage.clear();
    __resetGuardCache();
  });

  it('无 token 时跳登录页并带上原路径，且不发 /auth/me', () => {
    render(<AuthGuard>受保护内容</AuthGuard>);

    expect(replace).toHaveBeenCalledWith('/login?redirect=%2Ftalents');
    expect(screen.queryByText('受保护内容')).toBeNull();
    expect(screen.getByText('正在校验登录状态...')).toBeTruthy();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('token 本地已过期时跳登录页并清态，不发 /auth/me', () => {
    setAuth(makeToken(-60), { id: 1, username: 'hr', role: 'hr' });
    render(<AuthGuard>受保护内容</AuthGuard>);

    // 带过期 token 时要带 reason，登录页据此显示"登录已过期"提示条
    expect(replace).toHaveBeenCalledWith('/login?redirect=%2Ftalents&reason=expired');
    expect(window.localStorage.getItem('ai_talent_token')).toBeNull();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('token 有效且 /auth/me 通过时渲染子内容', async () => {
    setAuth(makeToken(3600), { id: 1, username: 'hr', role: 'hr' });
    mockGet.mockResolvedValue({ id: 1, username: 'hr', display_name: '招聘专员', role: 'hr' });

    render(<AuthGuard>受保护内容</AuthGuard>);

    expect(await screen.findByText('受保护内容')).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
    expect(mockGet).toHaveBeenCalledWith('/auth/me');
  });

  it('本地 exp 未过期但服务端 401（token 被篡改）→ 不渲染内容、保持校验态', async () => {
    setAuth(makeToken(3600), { id: 1, username: 'hr', role: 'hr' });
    mockGet.mockRejectedValue(new ApiError(401, '登录状态无效，请重新登录', { status: 401 }));

    render(<AuthGuard>受保护内容</AuthGuard>);

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('受保护内容')).toBeNull();
    expect(screen.getByText('正在校验登录状态...')).toBeTruthy();
  });

  it('服务端 403（账号停用）同样不渲染内容', async () => {
    setAuth(makeToken(3600), { id: 1, username: 'hr', role: 'hr' });
    mockGet.mockRejectedValue(new ApiError(403, '账号已被停用，请联系管理员', { status: 403 }));

    render(<AuthGuard>受保护内容</AuthGuard>);

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('受保护内容')).toBeNull();
  });

  it('/auth/me 网络异常时降级放行（后端故障不锁门）', async () => {
    setAuth(makeToken(3600), { id: 1, username: 'hr', role: 'hr' });
    mockGet.mockRejectedValue(new ApiError(0, '无法连接后端服务，请确认后端已启动'));

    render(<AuthGuard>受保护内容</AuthGuard>);

    expect(await screen.findByText('受保护内容')).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
  });

  it('同一 token 站内切页不重复调 /auth/me', async () => {
    const token = makeToken(3600);
    setAuth(token, { id: 1, username: 'hr', role: 'hr' });
    mockGet.mockResolvedValue({ id: 1, username: 'hr', role: 'hr' });

    const first = render(<AuthGuard>受保护内容</AuthGuard>);
    await screen.findByText('受保护内容');
    first.unmount();

    render(<AuthGuard>受保护内容</AuthGuard>);
    await screen.findByText('受保护内容');
    expect(mockGet).toHaveBeenCalledTimes(1);
  });
});
