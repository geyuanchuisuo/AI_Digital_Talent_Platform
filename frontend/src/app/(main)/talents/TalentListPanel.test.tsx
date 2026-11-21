import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TalentListPanel from './TalentListPanel';
import { fetchTalents, restoreTalent, searchTalents } from './api';
import type { TalentListItem } from './types';

vi.mock('./api', () => ({
  fetchTalents: vi.fn(),
  searchTalents: vi.fn(),
  restoreTalent: vi.fn(),
  fetchDuplicates: vi.fn(),
  fetchPendingDuplicateCount: vi.fn(),
  resolveDuplicate: vi.fn(),
}));

const fetchTalentsMock = vi.mocked(fetchTalents);
const searchTalentsMock = vi.mocked(searchTalents);
const restoreTalentMock = vi.mocked(restoreTalent);

function item(patch: Partial<TalentListItem> & Pick<TalentListItem, 'id' | 'name'>): TalentListItem {
  return {
    current_title: '后端工程师',
    work_years: 4,
    degree: '本科',
    city: '深圳',
    tags: ['python', 'fastapi'],
    tag_total: 2,
    source: 'resume',
    manual_edited_fields: [],
    vector_status: 'synced',
    deleted_at: null,
    updated_at: '2026-11-06T10:00:00',
    ...patch,
  };
}

const PAGE = {
  items: [
    item({ id: 5, name: '张三', vector_status: 'failed' }),
    item({ id: 6, name: '李四', tags: ['go'], tag_total: 1 }),
  ],
  total: 2,
  page: 1,
  page_size: 10,
};

describe('TalentListPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchTalentsMock.mockResolvedValue(PAGE);
    searchTalentsMock.mockResolvedValue([]);
    restoreTalentMock.mockResolvedValue({ restored: true, vector_status: 'synced' });
  });

  it('关键词模式渲染列表、标签与向量失败标记，姓名链接到详情', async () => {
    render(<TalentListPanel duplicateCount={2} onOpenDuplicates={vi.fn()} onChanged={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('张三')).toBeTruthy());
    expect(fetchTalentsMock).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 10 }));
    expect(screen.getByText('向量同步失败')).toBeTruthy();
    expect(screen.getByText('李四')).toBeTruthy();
    const link = screen.getByText('张三').closest('a');
    expect(link?.getAttribute('href')).toBe('/talents/5');
  });

  it('语义模式提交后走 searchTalents 并显示相似度列，不调列表接口', async () => {
    searchTalentsMock.mockResolvedValue([
      {
        id: 9,
        name: '王五',
        current_title: '算法工程师',
        work_years: 6,
        degree: '硕士',
        city: '北京',
        tags: ['机器学习'],
        tag_total: 1,
        vector_status: 'synced',
        similarity: 0.834,
      },
    ]);
    const { container } = render(
      <TalentListPanel duplicateCount={0} onOpenDuplicates={vi.fn()} onChanged={vi.fn()} />,
    );
    await waitFor(() => expect(fetchTalentsMock).toHaveBeenCalled());

    fireEvent.click(screen.getByText('语义检索'));
    const input = container.querySelector('.ant-input-search input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '会机器学习的人' } });
    fireEvent.click(container.querySelector('.ant-input-search-button') as HTMLButtonElement);

    await waitFor(() => expect(searchTalentsMock).toHaveBeenCalledWith('会机器学习的人', 20));
    await waitFor(() => expect(screen.getByText('王五')).toBeTruthy());
    // scroll.x 会渲染 measure 行，表头文案出现 ≥1 次即可
    expect(screen.getAllByText('相似度').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('83%')).toBeTruthy();
    // 语义模式下不再调列表接口（切换后只有本次检索）
    expect(fetchTalentsMock).toHaveBeenCalledTimes(1);
  });

  it('回收站视角显示删除时间与恢复按钮，恢复成功后回调 onChanged', async () => {
    const onChanged = vi.fn();
    const { container } = render(
      <TalentListPanel duplicateCount={0} onOpenDuplicates={vi.fn()} onChanged={onChanged} />,
    );
    await waitFor(() => expect(fetchTalentsMock).toHaveBeenCalled());

    fireEvent.click(container.querySelector('.ant-switch') as HTMLButtonElement);
    await waitFor(() =>
      expect(fetchTalentsMock).toHaveBeenLastCalledWith(expect.objectContaining({ deleted: true })),
    );

    // 刻意不用 getByRole(name) 定位：antd Table 在 jsdom 下每次角色查询都要遍历整表
    // 做可访问性与样式计算，单次可达数秒，是 REQ-C-20261121-133109-B 临界超时的根因。
    const restoreButtons = await waitFor(() => {
      const buttons = Array.from(container.querySelectorAll('button')).filter((btn) =>
        /恢\s*复/.test(btn.textContent ?? ''),
      );
      expect(buttons).toHaveLength(2);
      return buttons;
    });

    fireEvent.click(restoreButtons[0]);
    const confirmButton = await waitFor(() => {
      const button = document.querySelector<HTMLButtonElement>('.ant-popover .ant-btn-primary');
      expect(button?.textContent ?? '').toMatch(/确\s*定/);
      return button as HTMLButtonElement;
    });
    fireEvent.click(confirmButton);
    await waitFor(() => expect(restoreTalentMock).toHaveBeenCalledWith(5));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});
