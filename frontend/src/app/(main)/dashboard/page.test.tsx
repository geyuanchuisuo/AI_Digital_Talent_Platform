import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import DashboardPage from './page';

const fetchAdminOverview = vi.fn();
const push = vi.fn();

vi.mock('./api', () => ({
  CARD_ORDER: ['jobs', 'profiles', 'reviews', 'scoring', 'training', 'gaps'],
  fetchAdminOverview: (...args: unknown[]) => fetchAdminOverview(...args),
}));

vi.mock('@/lib/http', () => ({
  getErrorMessage: (e: unknown) => String(e),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}));

const OVERVIEW = {
  role: 'admin',
  data_source: 'primary',
  cards: {
    jobs: { count: 3, label: '活跃岗位', caliber: 'A 口径', entry: '/job-profiles' },
    profiles: { count: 12, label: '人才档案', caliber: 'B 契约', entry: '/talents' },
    reviews: {
      count: 6,
      label: '待复核',
      caliber: 'F03/F10',
      entry: '/screen',
      breakdown: { screen_pending: 2, match_unmarked: 2, inventory_pending: 2 },
    },
    scoring: { count: 1, label: '待评分', caliber: 'C 契约', entry: '/assessments' },
    training: { count: 4, label: '培训计划', caliber: 'C 契约 §43', entry: '/training' },
    gaps: {
      count: 2,
      label: '缺口预警',
      caliber: 'A 口径 §7',
      entry: '/analytics',
      breakdown: { demand_count: 2, total_gap: 5, note: '明细回查走问数' },
    },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('管理员工作台页（T-D22）', () => {
  it('渲染六类卡片计数与口径', async () => {
    fetchAdminOverview.mockResolvedValue(OVERVIEW);
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('3')).toBeTruthy());
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('6')).toBeTruthy();
    expect(screen.getByText('1')).toBeTruthy();
    expect(screen.getByText('4')).toBeTruthy();
    expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('A 口径')).toBeTruthy();
    expect(screen.getByText('F03/F10')).toBeTruthy();
  });

  it('点击卡片跳转对应业务入口', async () => {
    fetchAdminOverview.mockResolvedValue(OVERVIEW);
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('12')).toBeTruthy());
    fireEvent.click(screen.getByText('人才档案').closest('.ant-card')!);
    expect(push).toHaveBeenCalledWith('/talents');
  });

  it('待复核与缺口分项渲染', async () => {
    fetchAdminOverview.mockResolvedValue(OVERVIEW);
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('待复核分项')).toBeTruthy());
    expect(screen.getByText('缺口预警分项')).toBeTruthy();
    expect(screen.getByText('screen_pending')).toBeTruthy();
    expect(screen.getByText('demand_count')).toBeTruthy();
  });

  it('加载失败提示且不渲染数字', async () => {
    fetchAdminOverview.mockRejectedValue(new Error('403'));
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText(/403/)).toBeTruthy());
    expect(screen.queryByText('12')).toBeNull();
  });
});
