import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import DuplicatesDrawer from './DuplicatesDrawer';
import { fetchDuplicates, resolveDuplicate, undoMerge } from './api';
import type { DuplicateItem } from './types';

vi.mock('./api', () => ({
  fetchDuplicates: vi.fn(),
  resolveDuplicate: vi.fn(),
  undoMerge: vi.fn(),
  restoreTalent: vi.fn(),
  fetchTalents: vi.fn(),
  searchTalents: vi.fn(),
  fetchPendingDuplicateCount: vi.fn(),
}));

const fetchDuplicatesMock = vi.mocked(fetchDuplicates);
const resolveDuplicateMock = vi.mocked(resolveDuplicate);

function dupItem(patch: Partial<DuplicateItem> & Pick<DuplicateItem, 'id' | 'reason'>): DuplicateItem {
  return {
    matched_value: '138****0000',
    status: 'pending',
    profile_a: { id: 5, name: '张三', current_title: '后端工程师', created_at: '2026-11-05T10:00:00' },
    profile_b: null,
    parsed_resume: { id: 12, filename: '张三-新版.pdf', name: '张三', phone: '138****0000' },
    created_at: '2026-11-06T09:00:00',
    ...patch,
  };
}

const PENDING: DuplicateItem[] = [
  dupItem({ id: 1, reason: 'name_phone_match' }),
  dupItem({ id: 2, reason: 'email_match', matched_value: 'a***e@example.com' }),
];

describe('DuplicatesDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchDuplicatesMock.mockResolvedValue({ items: PENDING, total: 2, page: 1, page_size: 50 });
    resolveDuplicateMock.mockResolvedValue({
      duplicate_id: 1,
      status: 'kept',
      talent_profile_id: 20,
      task_id: 12,
      task_status: 'pending',
      stage: 'profile',
    });
  });

  it('渲染待确认列表：reason 细分文案 + 脱敏命中值 + 既有画像 A', async () => {
    render(<DuplicatesDrawer open onClose={vi.fn()} onChanged={vi.fn()} />);

    await waitFor(() => expect(screen.getAllByText('张三-新版.pdf').length).toBeGreaterThan(0));
    expect(screen.getByText('姓名+手机号相同')).toBeTruthy();
    expect(screen.getByText('邮箱相同')).toBeTruthy();
    expect(screen.getByText('138****0000')).toBeTruthy();
    expect(screen.getAllByText(/画像 A：#5 张三/).length).toBe(2);
  });

  it('keep：确认后调 resolve({action:"keep"}) 并回调 onChanged、刷新列表', async () => {
    const onChanged = vi.fn();
    render(<DuplicatesDrawer open onClose={vi.fn()} onChanged={onChanged} />);
    await waitFor(() => expect(screen.getAllByText('张三-新版.pdf').length).toBeGreaterThan(0));

    const keepButtons = screen.getAllByRole('button', { name: /保\s*留/ });
    fireEvent.click(keepButtons[0]);
    fireEvent.click(await screen.findByRole('button', { name: '确认保留' }));

    await waitFor(() => expect(resolveDuplicateMock).toHaveBeenCalledWith(1, { action: 'keep' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(fetchDuplicatesMock).toHaveBeenCalledTimes(2)); // 打开 + 处理后刷新
  });

  it('merge：确认后调 resolve({action:"merge"})（master 固定 A，不传 master_profile_id）', async () => {
    resolveDuplicateMock.mockResolvedValue({
      duplicate_id: 1,
      status: 'merged',
      master_profile_id: 5,
      vector_status: 'synced',
      task_id: 12,
      task_status: 'success',
    });
    render(<DuplicatesDrawer open onClose={vi.fn()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByText('张三-新版.pdf').length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByRole('button', { name: /^合\s*并$/ })[0]);
    fireEvent.click(await screen.findByRole('button', { name: '确认合并' }));

    await waitFor(() => expect(resolveDuplicateMock).toHaveBeenCalledWith(1, { action: 'merge' }));
  });

  it('空列表显示空态', async () => {
    fetchDuplicatesMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    render(<DuplicatesDrawer open onClose={vi.fn()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('暂无待确认的重复简历')).toBeTruthy());
  });

  it('切到已合并记录，填写原因后撤销并刷新', async () => {
    const onChanged=vi.fn();
    fetchDuplicatesMock.mockImplementation(async (_page,_size,status)=>({
      items:status==='merged'?[dupItem({id:1,reason:'phone_match',status:'merged'})]:PENDING,
      total:1,page:1,page_size:50}));
    vi.mocked(undoMerge).mockResolvedValue({duplicate_id:1,status:'pending',master_profile_id:5,
      profile_version:3,vector_status:'synced'});
    render(<DuplicatesDrawer open onClose={vi.fn()} onChanged={onChanged}/>);
    fireEvent.click(screen.getByRole('button',{name:'已合并记录'}));
    fireEvent.click(await screen.findByRole('button',{name:'撤销合并'}));
    fireEvent.change(await screen.findByLabelText('撤销原因'),{target:{value:'误判为同一个人'}});
    fireEvent.click(screen.getByRole('button',{name:'确认撤销'}));
    await waitFor(()=>expect(undoMerge).toHaveBeenCalledWith(1,'误判为同一个人'));
    await waitFor(()=>expect(onChanged).toHaveBeenCalled());
  });
});
