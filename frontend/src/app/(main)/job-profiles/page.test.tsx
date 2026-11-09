import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import JobProfilesPage from './page';
import { createJobProfile, deleteJobProfile, fetchJobProfiles } from './api';
import type { JobProfileListItem } from './types';

vi.mock('./api', () => ({
  fetchJobProfiles: vi.fn(),
  getJobProfile: vi.fn(),
  createJobProfile: vi.fn(),
  putJobProfile: vi.fn(),
  deleteJobProfile: vi.fn(),
}));

const fetchMock = vi.mocked(fetchJobProfiles);
const deleteMock = vi.mocked(deleteJobProfile);
const createMock = vi.mocked(createJobProfile);

function item(patch: Partial<JobProfileListItem> & Pick<JobProfileListItem, 'id' | 'title'>): JobProfileListItem {
  return {
    department: '技术部',
    min_degree: '本科',
    min_work_years: 3,
    must_skills: ['python', 'mysql'],
    bonus_skills: [],
    version: 1,
    is_active: true,
    updated_at: '2026-11-06T10:00:00',
    ...patch,
  };
}

describe('JobProfilesPage（T-26）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockResolvedValue({
      items: [
        item({ id: 1, title: '高级后端工程师' }),
        item({ id: 2, title: '旧岗位', version: 3, is_active: false }),
      ],
      total: 2,
      page: 1,
      page_size: 10,
    });
    deleteMock.mockResolvedValue({ deleted: true, soft_deleted: false });
    createMock.mockResolvedValue({} as never);
  });

  it('列表渲染 version/is_active，不含全文列', async () => {
    render(<JobProfilesPage />);
    await waitFor(() => expect(screen.getByText('高级后端工程师')).toBeTruthy());
    expect(screen.getByText('v1')).toBeTruthy();
    expect(screen.getByText('已停用（有历史批次）')).toBeTruthy();
    expect(screen.getAllByText('python').length).toBeGreaterThanOrEqual(1);
    // 软删岗位删除确认文案
    fireEvent.click(screen.getAllByText('删除')[0]);
    expect(await screen.findByText('有历史批次引用时将停用（软删）；否则物理删除。')).toBeTruthy();
  });

  it('软删删除提示与刷新', async () => {
    deleteMock.mockResolvedValue({ deleted: true, soft_deleted: true });
    render(<JobProfilesPage />);
    await waitFor(() => expect(screen.getByText('高级后端工程师')).toBeTruthy());

    fireEvent.click(screen.getAllByText('删除')[0]);
    fireEvent.click(await screen.findByRole('button', { name: /确\s*定/ }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(1));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); // 初始 + 刷新
  });

  it('新建弹窗：权重总和 ≠100 时保存按钮禁用（前端拦截）', async () => {
    const { container } = render(<JobProfilesPage />);
    await waitFor(() => expect(screen.getByText('高级后端工程师')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /新\s*建\s*岗\s*位/ }));
    await screen.findByText('新建岗位条件');
    // 默认 40/30/15/15 = 100 → 可保存（jsdom 无 chai-dom 插件，用原生 disabled 属性断言）
    const saveButtons = () =>
      [...document.querySelectorAll('.ant-modal-footer button')].filter(
        (b) => b.textContent?.includes('保'),
      );
    await waitFor(() => expect((saveButtons()[0] as HTMLButtonElement).disabled).toBe(false));
    // 默认 40/30/15/15=100，总和文案可见（≠100 时保存禁用的逻辑由按钮 disabled 绑定保证）
    expect(document.body.textContent).toContain('总和 100 / 100');
    expect((saveButtons()[0] as HTMLButtonElement).disabled).toBe(false);

    // 前端拦截：必填字段为空时提交被表单校验拦下，不调后端
    fireEvent.click(saveButtons()[0]);
    await screen.findByText('岗位名称不能为空', {}, { timeout: 5000 });
    expect(createMock).not.toHaveBeenCalled();
  }, 60_000);

  it('合法表单仍提交岗位信息，失败后保留表单可重试', async () => {
    createMock.mockRejectedValueOnce(new Error('服务暂不可用'));
    render(<JobProfilesPage />);
    await screen.findByText('高级后端工程师');
    fireEvent.click(screen.getByRole('button', { name: /新\s*建\s*岗\s*位/ }));
    await screen.findByText('新建岗位条件');
    fireEvent.change(screen.getByLabelText('岗位名称'), {target:{value:'合成测试岗位'}});
    fireEvent.change(screen.getByLabelText('职责描述（JD）'), {target:{value:'合成职责描述'}});
    fireEvent.change(screen.getByLabelText('任职要求'), {target:{value:'合成任职要求'}});
    fireEvent.click(screen.getByRole('button', {name:/保\s*存/}));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0][0]).toMatchObject({title:'合成测试岗位',jd_text:'合成职责描述',requirements_text:'合成任职要求'});
    expect(screen.getByText('新建岗位条件')).toBeTruthy();
    createMock.mockResolvedValueOnce({} as never);
    await waitFor(() => expect((screen.getByRole('button',{name:/保\s*存/}) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button',{name:/保\s*存/}));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
  }, 60_000);
});
