import { afterEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import { get, getWithHeaders, http } from '@/lib/http';
import { getToken, setAuth } from '@/lib/auth';
vi.mock('antd', () => ({ message: {error: vi.fn()} }));

afterEach(() => { localStorage.clear(); vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('authorization errors', () => {
  it('clears a session when the shared AuthGuard probe is denied', async () => {
    vi.useFakeTimers();
    setAuth('test-token', {id: 1, username: 'personal', role: 'personal'});
    const adapter = http.defaults.adapter;
    http.defaults.adapter = async (config) => {
      expect(config.headers.get('X-Auth-Probe')).toBe('1');
      throw new AxiosError('Forbidden', 'ERR_BAD_REQUEST', config, undefined,
        {config, data: {code: 403, message: '会话校验被拒绝'}, headers: {}, status: 403, statusText: 'Forbidden'});
    };
    try {
      await expect(getWithHeaders('/auth/me', {'X-Auth-Probe':'1'})).rejects.toMatchObject({code:403});
      expect(getToken()).toBeNull();
    } finally { http.defaults.adapter = adapter; }
  });
  it('keeps a valid session after a resource permission denial', async () => {
    setAuth('test-token', {id: 1, username: 'personal', role: 'personal'});
    const adapter = http.defaults.adapter;
    http.defaults.adapter = async (config) => {
      throw new AxiosError('Forbidden', 'ERR_BAD_REQUEST', config, undefined,
        {config, data: {code: 403, message: '无权访问该资源'}, headers: {}, status: 403, statusText: 'Forbidden'});
    };
    try {
      await expect(get('/talents/999')).rejects.toMatchObject({code: 403});
      expect(getToken()).toBe('test-token');
    } finally { http.defaults.adapter = adapter; }
  });
  it.each([
    ['/auth/me',403,'账号已停用'],
    ['/talents/129',403,'账号已停用'],
    ['/talents/129',401,'登录已过期'],
  ])('clears an invalid session for %s / %s', async (url,status,msg) => {
    vi.useFakeTimers();
    setAuth('test-token',{id:1,username:'personal',role:'personal'});
    const adapter=http.defaults.adapter;
    http.defaults.adapter=async config => {throw new AxiosError('Denied','ERR_BAD_REQUEST',config,undefined,
      {config,data:{code:status,message:msg},headers:{},status,statusText:'Denied'});};
    try {
      await expect(get(url)).rejects.toMatchObject({code:status});
      expect(getToken()).toBeNull();
    } finally {http.defaults.adapter=adapter;}
  });
});
