import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MyApplicationsPage from './page';
import { clearAuth, setAuth } from '@/lib/auth';
import { get } from '@/lib/http';

vi.mock('@/lib/http', () => ({
  get: vi.fn(),
  ApiError: class MockApiError extends Error {},
}));

const getMock = vi.mocked(get);

describe('我的岗位申请（个人端）', () => {
  beforeEach(() => {
    getMock.mockReset();
    clearAuth();
  });

  it('已绑定账号渲染申请列表与对外阶段中文标签', async () => {
    setAuth('header.payload.signature', {
      id: 2, username: 'personal', display_name: '演示个人账号', role: 'personal', person_id: 398,
    });
    getMock.mockResolvedValue({
      items: [
        {
          id: 1, job_profile_id: 1, internal_stage: 'invited',
          external_stage: 'interviewing', result_category: null,
          result_published: false, stage_updated_at: '2026-11-07T15:00:00',
        },
        {
          id: 2, job_profile_id: 2, internal_stage: 'offer_accepted',
          external_stage: 'closed', result_category: 'hired',
          result_published: true, stage_updated_at: '2026-11-07T15:30:00',
        },
      ],
      total: 2,
    });

    render(<MyApplicationsPage />);
    await waitFor(() => expect(screen.getByText('待面试')).toBeTruthy());
    // antd Table 分行异步渲染：各断言独立等待
    await waitFor(() => expect(screen.getByText('已结束')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('录用')).toBeTruthy()); // result_published=true 才显示类别
    expect(screen.queryByText('待确认')).toBeNull(); // 无未确认行
  });

  it('未绑定档案不发请求并展示提示', () => {
    setAuth('header.payload.signature', {
      id: 3, username: 'personal', display_name: '无绑定', role: 'personal', person_id: null,
    });
    render(<MyApplicationsPage />);
    expect(screen.getByText(/尚未绑定人才档案/)).toBeTruthy();
    expect(getMock).not.toHaveBeenCalled();
  });

  it('内部阶段不外露（响应本身无 internal_stage 展示列）', async () => {
    setAuth('header.payload.signature', {
      id: 2, username: 'personal', display_name: '演示', role: 'personal', person_id: 398,
    });
    getMock.mockResolvedValue({
      items: [
        {
          id: 1, job_profile_id: 1, internal_stage: 'matched',
          external_stage: 'screening', result_category: null,
          result_published: false, stage_updated_at: null,
        },
      ],
      total: 1,
    });
    render(<MyApplicationsPage />);
    await waitFor(() => expect(screen.getByText('筛选中')).toBeTruthy());
    expect(screen.queryByText('matched')).toBeNull(); // 内部枚举值不出现在 DOM
  });
});
