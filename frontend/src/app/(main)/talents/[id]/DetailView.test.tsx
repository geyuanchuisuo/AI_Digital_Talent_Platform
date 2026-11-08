import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import DetailView, { READONLY_EXPERIENCE_KEYS } from './DetailView';
import { deleteTalent, downloadResumeFile, fetchDuplicates, getTalent, revectorTalent } from '../api';
import type { TalentDetailData } from '../types';
import {setAuth, clearAuth} from '@/lib/auth';

const { pushMock, replaceMock } = vi.hoisted(() => ({ pushMock: vi.fn(), replaceMock: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: pushMock, replace: replaceMock }) }));

vi.mock('../api', () => ({
  getTalent: vi.fn(),
  patchTalent: vi.fn(),
  revectorTalent: vi.fn(),
  deleteTalent: vi.fn(),
  downloadResumeFile: vi.fn(),
  fetchDuplicates: vi.fn(),
  resolveDuplicate: vi.fn(),
  fetchTalents: vi.fn(),
  searchTalents: vi.fn(),
  restoreTalent: vi.fn(),
  fetchPendingDuplicateCount: vi.fn(),
}));

const getTalentMock = vi.mocked(getTalent);
const revectorMock = vi.mocked(revectorTalent);
const deleteMock = vi.mocked(deleteTalent);
const downloadMock = vi.mocked(downloadResumeFile);
const fetchDuplicatesMock = vi.mocked(fetchDuplicates);

const DETAIL: TalentDetailData = {
  profile: {
    id: 5,
    name: '张三',
    gender: '男',
    birth_year: 1996,
    phone: '13800000000',
    email: 'z@x.com',
    city: '深圳',
    degree: '本科',
    work_years: 4,
    current_title: '后端工程师',
    expected_title: '高级后端',
    skills: ['python', 'fastapi'],
    tags: [
      { tag: 'python', tag_type: 'skill', source: 'llm' },
      { tag: '团队协作', tag_type: 'general', source: 'manual' },
    ],
    ability_scores: {
      professional: { score: 85, basis: '主导重构' },
      project_experience: { score: 78, basis: '核心项目' },
      education: { score: 70, basis: '本科' },
      stability: { score: 88, basis: '两段各3年' },
      growth_potential: { score: 80, basis: '持续学习' },
    },
    manual_edited_fields: ['phone'],
    vector_status: 'failed',
    created_at: '2026-11-05T10:00:00',
    updated_at: '2026-11-06T10:00:00',
  },
  educations: [{ school: '某大学', major: '计算机', degree: '本科', start: '2014-09', end: '2018-06' }],
  employments: [
    { company: 'A 公司', title: '后端工程师', start: '2018-07', end: '2021-06', description: '订单系统' },
  ],
  projects: [],
  certificates: [{ name: 'PMP' }],
  resume_files: [{ resume_file_id: 9, filename: '张三简历.pdf', upload_batch_id: '01JABCDEFGHJKMNPQRSTVWXYZ' }],
  screen_history: [
    { batch_id: 2, job_title: '高级后端工程师', effective_verdict: 'rejected', score: 82.5, created_at: '2026-11-05T12:00:00' },
  ],
  match_history: [],
};

describe('DetailView（T-21）', () => {
  beforeEach(() => {
    clearAuth();
    vi.clearAllMocks();
    getTalentMock.mockResolvedValue(DETAIL);
    fetchDuplicatesMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    revectorMock.mockResolvedValue({ vector_status: 'synced', vector_synced_at: '2026-11-06T11:00:00' });
    deleteMock.mockResolvedValue({ deleted: true, vector_deleted: true });
    downloadMock.mockResolvedValue(undefined);
  });

  it('personal accounts use the readonly route without management requests', async () => {
    setAuth('test',{id:2,username:'personal',role:'personal',person_id:5});
    render(<DetailView talentId={5} />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith('/my-profile'));
    expect(getTalentMock).not.toHaveBeenCalled();
    expect(fetchDuplicatesMock).not.toHaveBeenCalled();
    expect(screen.queryByRole('button',{name:/编辑|删除/})).toBeNull();
  });

  it('渲染信息卡、标签（type/source 提示）、经历时间线与人工修正标记；经历类无编辑入口', async () => {
    const { container } = render(<DetailView talentId={5} />);

    await waitFor(() => expect(screen.getByText('张三')).toBeTruthy());
    expect(screen.getByText('13800000000')).toBeTruthy();
    // 字段级保护视觉标识（manual_edited_fields=['phone']）
    expect(screen.getAllByText('人工').length).toBeGreaterThanOrEqual(1);
    // 标签存在（来源/类型在 Tooltip title 里，悬停才渲染 portal，此处验证标签本身）
    expect(screen.getByText('团队协作')).toBeTruthy();
    // 经历时间线（只读；antd Tabs 惰性渲染，需点开对应页签）
    expect(screen.getByText('A 公司')).toBeTruthy();
    fireEvent.click(screen.getByText(/教育（1）/));
    expect(await screen.findByText('某大学')).toBeTruthy();
    // 雷达图为 SVG（含数据多边形与分值）
    expect(screen.getByTestId('ability-radar')).toBeTruthy();
    // D-J 自检：页面无任何经历类编辑输入（整页只有标签/基本信息两个编辑按钮）
    expect(container.querySelectorAll('input').length).toBe(0);
    expect(screen.queryByText('编辑经历')).toBeNull();
    // 常量与后端口径一致
    expect(READONLY_EXPERIENCE_KEYS.join(',')).toBe('educations,employments,projects,certificates');
  });

  it('顶部提供返回列表出口，点击后跳回 /talents', async () => {
    render(<DetailView talentId={5} />);
    await waitFor(() => expect(screen.getByText('张三')).toBeTruthy());

    const back = screen.getByRole('button', { name: /返回人才画像列表/ });
    fireEvent.click(back);
    expect(pushMock).toHaveBeenCalledWith('/talents');
  });

  it('vector_status=failed 显示告警条，点击重建向量调 revector 并刷新', async () => {
    render(<DetailView talentId={5} />);
    const alert = await screen.findByText('向量同步失败：语义检索与向量相关功能暂不可用');
    expect(alert).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /重\s*建\s*向\s*量/ }));
    await waitFor(() => expect(revectorMock).toHaveBeenCalledWith(5));
    await waitFor(() => expect(getTalentMock).toHaveBeenCalledTimes(2)); // 初始 + 刷新
  });

  it('原始简历 Tab：点击下载原文调 blob 下载', async () => {
    render(<DetailView talentId={5} />);
    await screen.findByText('张三');

    fireEvent.click(screen.getByText(/原始简历/));
    fireEvent.click(await screen.findByRole('button', { name: /下\s*载\s*原\s*文/ }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith(5, 9, '张三简历.pdf'));
  });

  it('筛选历史 Tab 展示有效结论（manual 覆盖 verdict）', async () => {
    render(<DetailView talentId={5} />);
    await screen.findByText('张三');

    fireEvent.click(screen.getByText(/筛选历史/));
    expect(await screen.findByText('淘汰')).toBeTruthy(); // effective_verdict=rejected
    expect(screen.getByText('高级后端工程师')).toBeTruthy();
    expect(screen.getByText('82.5')).toBeTruthy();
  });

  it('pending 查重（本画像为画像 A）显示告警条，弹窗并排对比并支持合并/保留', async () => {
    fetchDuplicatesMock.mockResolvedValue({
      items: [
        {
          id: 1,
          reason: 'phone_match',
          matched_value: '138****0000',
          status: 'pending',
          profile_a: { id: 5, name: '张三', current_title: '后端工程师', created_at: '2026-11-05T10:00:00' },
          profile_b: null,
          parsed_resume: { id: 12, filename: '张三-新版.pdf', name: '张三', phone: '138****0000' },
          created_at: '2026-11-06T09:00:00',
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    });
    render(<DetailView talentId={5} />);

    expect(await screen.findByText('该画像有待确认的重复简历')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '去处理' }));
    expect(await screen.findByText('既有画像 A（合并目标）')).toBeTruthy();
    expect(screen.getByText('新解析结果')).toBeTruthy();
    expect(screen.getByRole('button', { name: '合并到画像 A' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '保留为独立画像' })).toBeTruthy();
  });

  it('删除：二次确认后调 DELETE 并跳回列表（mock router）', async () => {
    render(<DetailView talentId={5} />);
    await screen.findByText('张三');

    fireEvent.click(screen.getByRole('button', { name: /删\s*除/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确定删除' }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(5));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/talents'));
  });
});
