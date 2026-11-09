import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MeDashboardCard from './me-dashboard';

const fetchMeOverview = vi.fn();

vi.mock('./me-api', () => ({
  fetchMeOverview: (...args: unknown[]) => fetchMeOverview(...args),
}));

vi.mock('@/lib/http', () => ({
  getErrorMessage: (e: unknown) => String(e),
}));

const OVERVIEW = {
  user_id: 3,
  pending_assignments: 2,
  pending_training: 1,
  recent_results: 3,
  caliber: {
    pending_assignments: '本人未交卷测评',
    pending_training: '本人培训已正式指派',
    recent_results: '本人已公布结果',
  },
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('个人工作台卡片（T-D23）', () => {
  it('渲染三要素计数与口径说明', async () => {
    fetchMeOverview.mockResolvedValue(OVERVIEW);
    render(<MeDashboardCard />);
    await waitFor(() => expect(screen.getByText('2')).toBeTruthy());
    expect(screen.getByText('1')).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
    expect(screen.getByText('我的工作台')).toBeTruthy();
  });

  it('加载失败提示', async () => {
    fetchMeOverview.mockRejectedValue(new Error('403'));
    render(<MeDashboardCard />);
    await waitFor(() => expect(screen.getByText(/403/)).toBeTruthy());
    expect(screen.queryByText('2')).toBeNull();
  });
});
