import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

import AiPlatformPage from './page';
import {
  fetchAgentRuns,
  fetchCallLogs,
  fetchPrompts,
  fetchProviders,
  fetchUsageSummary,
  publishPrompt,
  setDefaultProvider,
  setProviderEnabled,
  type ProviderInfo,
  type PromptVersionInfo,
} from './api';

vi.mock('./api', () => ({
  fetchProviders: vi.fn(),
  createProvider: vi.fn(),
  setProviderEnabled: vi.fn(),
  setDefaultProvider: vi.fn(),
  fetchPrompts: vi.fn(),
  publishPrompt: vi.fn(),
  fetchUsageSummary: vi.fn(),
  fetchCallLogs: vi.fn(),
  fetchAgentRuns: vi.fn(),
}));

const fetchProvidersMock = vi.mocked(fetchProviders);
const setEnabledMock = vi.mocked(setProviderEnabled);
const setDefaultMock = vi.mocked(setDefaultProvider);
const fetchPromptsMock = vi.mocked(fetchPrompts);
const publishPromptMock = vi.mocked(publishPrompt);
const fetchUsageMock = vi.mocked(fetchUsageSummary);
const fetchCallLogsMock = vi.mocked(fetchCallLogs);

function provider(partial: Partial<ProviderInfo>): ProviderInfo {
  return {
    id: 1,
    name: 'spark',
    provider_type: 'openai_compatible',
    base_url: 'http://127.0.0.1:9/v1',
    model: 'spark-x2.6',
    enabled: true,
    is_default: false,
    timeout_seconds: 120,
    note: null,
    updated_by: null,
    has_api_key: true,
    ...partial,
  };
}

function prompt(partial: Partial<PromptVersionInfo>): PromptVersionInfo {
  return {
    id: 1,
    name: 'parse',
    version: 1,
    status: 'draft',
    content: '解析提示词',
    note: null,
    created_by: null,
    published_by: null,
    published_at: null,
    ...partial,
  };
}

async function openTab(label: string) {
  fireEvent.click(screen.getByText(label));
  await waitFor(() => expect(screen.queryByRole('tab', { selected: true }))?.toBeTruthy());
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchProvidersMock.mockResolvedValue([provider({ id: 1, is_default: true })]);
  fetchPromptsMock.mockResolvedValue([prompt({})]);
  fetchUsageMock.mockResolvedValue({
    days: 7,
    total_calls: 10,
    total_errors: 2,
    error_rate: 0.2,
    avg_latency_ms: 150,
    by_purpose: [],
  });
  fetchCallLogsMock.mockResolvedValue([
    {
      id: 1,
      provider_name: 'spark',
      model: 'spark-x2.6',
      purpose: 'parse',
      result: 'ok',
      latency_ms: 100,
      prompt_tokens: 10,
      completion_tokens: 5,
      error: null,
      actor_user_id: null,
      created_at: '2026-11-09T12:00:00',
    },
  ]);
});

afterEach(() => {
  cleanup();
});

describe('AiPlatformPage', () => {
  it('渲染供应商列表（密钥仅布尔、默认标记）', async () => {
    render(<AiPlatformPage />);
    await openTab('供应商');
    await waitFor(() => expect(fetchProvidersMock).toHaveBeenCalled());
    expect(screen.getByText('spark')).toBeTruthy();
    expect(screen.getByText('已配置')).toBeTruthy();
    expect(screen.getAllByText('默认').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/sk-/)).not.toBeTruthy();
  });

  it('停用/启用供应商调用接口并刷新', async () => {
    fetchProvidersMock.mockResolvedValue([provider({ id: 2, enabled: false, is_default: false })]);
    render(<AiPlatformPage />);
    await openTab('供应商');
    await waitFor(() => expect(fetchProvidersMock).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(setEnabledMock).toHaveBeenCalledWith(2, true));
  });

  it('设默认执行面调用接口', async () => {
    fetchProvidersMock.mockResolvedValue([provider({ id: 3, enabled: true, is_default: false })]);
    render(<AiPlatformPage />);
    await openTab('供应商');
    await waitFor(() => expect(fetchProvidersMock).toHaveBeenCalled());
    fireEvent.click(screen.getByText('设为默认'));
    await waitFor(() => expect(setDefaultMock).toHaveBeenCalledWith(3));
  });

  it('Prompt 版本库：草稿可发布、已发布显示已锁定', async () => {
    fetchPromptsMock.mockResolvedValue([
      prompt({ id: 1, status: 'draft', name: 'match_prompt' }),
      prompt({ id: 2, status: 'published', name: 'parse_v2' }),
    ]);
    render(<AiPlatformPage />);
    fireEvent.click(screen.getByText('Prompt 库'));
    await waitFor(() => expect(fetchPromptsMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('match_prompt')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('已发布')).toBeTruthy());
    // antd Button 会把两字中文拆成两个 span（'发 布'），用锚定正则匹配
    fireEvent.click(screen.getByRole('button', { name: /^发\s*布$/ }));
    await waitFor(() => expect(publishPromptMock).toHaveBeenCalledWith(1));
  });

  it('用量看板：聚合指标与调用日志明细', async () => {
    render(<AiPlatformPage />);
    fireEvent.click(screen.getByText('用量看板'));
    await waitFor(() => expect(fetchUsageMock).toHaveBeenCalled(), { timeout: 3000 });
    expect(screen.getByText('总调用')).toBeTruthy();
    expect(screen.getByText('20.0%')).toBeTruthy();
    expect(screen.getByText('parse')).toBeTruthy();
  });
});
