import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowsPage from './page';
import {
  createDefinition,
  deactivateVersion,
  fetchDefinitions,
  fetchVersionDetail,
  fetchVersions,
  reviewVersion,
  rollbackDefinition,
  submitVersion,
} from './api';
import type { VersionDetail, WorkflowDefinition, WorkflowVersion } from './api';

vi.mock('./api', () => ({
  fetchDefinitions: vi.fn(),
  createDefinition: vi.fn(),
  fetchVersions: vi.fn(),
  createDraftVersion: vi.fn(),
  fetchVersionDetail: vi.fn(),
  updateDraftGraph: vi.fn(),
  submitVersion: vi.fn(),
  reviewVersion: vi.fn(),
  deactivateVersion: vi.fn(),
  rollbackDefinition: vi.fn(),
}));

const fetchDefinitionsMock = vi.mocked(fetchDefinitions);
const fetchVersionsMock = vi.mocked(fetchVersions);
const fetchVersionDetailMock = vi.mocked(fetchVersionDetail);
const createDefinitionMock = vi.mocked(createDefinition);
const submitVersionMock = vi.mocked(submitVersion);
const reviewVersionMock = vi.mocked(reviewVersion);
const deactivateVersionMock = vi.mocked(deactivateVersion);
const rollbackDefinitionMock = vi.mocked(rollbackDefinition);

const definition: WorkflowDefinition = {
  id: 7,
  code: 'hire_review',
  name: '招聘复核流程',
  description: '解析→审批→匹配',
  created_at: '2026-11-08T10:00:00',
};

function version(patch: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    id: 101,
    version: 1,
    status: 'draft',
    note: null,
    submitted_by: null,
    submitted_at: null,
    review_note: null,
    published_at: null,
    ...patch,
  };
}

const detail: VersionDetail = {
  definition: { id: 7, code: 'hire_review', name: '招聘复核流程' },
  version: version({ status: 'published', submitted_by: 2, published_at: '2026-11-08T11:00:00' }),
  nodes: [
    {
      node_key: 'parse_1',
      name: '解析简历',
      node_type: 'profile_parse',
      position: null,
      config: null,
      inputs: [{ as: 'resume_id', from: { kind: 'trigger' } }],
      outputs: [{ as: 'profile_id', type: 'int' }],
    },
  ],
  edges: [{ source: 'parse_1', target: 'match_1', branch: null, condition: null }],
};

// antd v5 对两字中文按钮自动插空格（停用→「停 用」），可访问名带空格且 name
// 归一化不去词内空格——用逐字允许空白的正则匹配，兼容两种渲染。
function buttonPattern(name: string): RegExp {
  return new RegExp(name.split('').join('\\s*'));
}

function buttonNamed(name: string): HTMLElement {
  return screen.getByRole('button', { name: buttonPattern(name) });
}

function queryButtonNamed(name: string): HTMLElement | null {
  return screen.queryByRole('button', { name: buttonPattern(name) });
}

/** 渲染并等待定义列表出现；可选点击定义行展开版本区，再等版本行操作按钮渲染。 */
async function renderAndOpen(options: { openRow?: boolean } = {}) {
  render(<WorkflowsPage />);
  const cell = await waitFor(() => screen.getByText('hire_review'));
  if (options.openRow) {
    fireEvent.click(cell.closest('tr')!);
    await waitFor(() => expect(fetchVersionsMock).toHaveBeenCalledWith(7));
    await waitFor(() => {
      const anyAction = ['提交审核', '通过', '停用', '回滚到此版'].find((name) =>
        queryButtonNamed(name),
      );
      expect(anyAction).toBeTruthy();
    });
  }
}

describe('WorkflowsPage（T-D15 受控生命周期）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchDefinitionsMock.mockResolvedValue([definition]);
    fetchVersionsMock.mockResolvedValue([version()]);
  });

  it('渲染定义列表；点击定义行加载版本列表', async () => {
    await renderAndOpen({ openRow: true });
    await waitFor(() => expect(screen.getByText('草稿')).toBeTruthy());
    expect(buttonNamed('提交审核')).toBeTruthy();
  });

  it('未选择定义时版本区提示先选择', async () => {
    await renderAndOpen();
    expect(screen.getByText(/先选择一个定义/)).toBeTruthy();
    expect(fetchVersionsMock).not.toHaveBeenCalled();
  });

  it('草稿版本提交审核：调用接口并刷新版本列表', async () => {
    submitVersionMock.mockResolvedValue({ id: 101, status: 'pending_review', submitted_by: 2 });
    await renderAndOpen({ openRow: true });
    // 提交成功后刷新会重新拉版本列表——先把刷新结果换为待审核（点击前替换，
    // reload 发生在下方 waitFor 期间）
    fetchVersionsMock.mockResolvedValue([version({ status: 'pending_review' })]);
    fireEvent.click(buttonNamed('提交审核'));
    await waitFor(() => expect(submitVersionMock).toHaveBeenCalledWith(101));
    await waitFor(() => expect(screen.getByText('待审核')).toBeTruthy());
  }, 60_000);

  it('待审版本审核：通过调用 reviewVersion(true)；驳回带意见调用 reviewVersion(false)', async () => {
    fetchVersionsMock.mockResolvedValue([version({ status: 'pending_review', submitted_by: 2 })]);
    reviewVersionMock.mockResolvedValue({ id: 101, status: 'published', review_note: null });
    await renderAndOpen({ openRow: true });
    const approveBtn = await waitFor(() => screen.getByTestId('approve-101'));
    fireEvent.click(approveBtn);
    await waitFor(() => expect(reviewVersionMock).toHaveBeenCalledWith(101, true));

    // 驳回：打开意见 Modal → 填意见 → 确认
    fetchVersionsMock.mockResolvedValue([version({ status: 'pending_review', submitted_by: 2 })]);
    fireEvent.click(buttonNamed('驳回'));
    const noteBox = (await waitFor(() =>
      screen.getByPlaceholderText('审核意见（可选，≤500 字）'),
    )) as HTMLTextAreaElement;
    fireEvent.change(noteBox, { target: { value: '缺人工审批节点' } });
    fireEvent.click(buttonNamed('确认驳回'));
    await waitFor(() =>
      expect(reviewVersionMock).toHaveBeenCalledWith(101, false, '缺人工审批节点'),
    );
  }, 60_000);

  it('发布版本操作：停用与回滚各调用对应接口', async () => {
    fetchVersionsMock.mockResolvedValue([
      version({ status: 'published', published_at: '2026-11-08T11:00:00' }),
    ]);
    deactivateVersionMock.mockResolvedValue({ id: 101, status: 'deprecated' });
    rollbackDefinitionMock.mockResolvedValue({ id: 101, version: 1, status: 'published' });
    await renderAndOpen({ openRow: true });
    fireEvent.click(buttonNamed('停用'));
    await waitFor(() => expect(deactivateVersionMock).toHaveBeenCalledWith(101));
    fireEvent.click(buttonNamed('回滚到此版'));
    await waitFor(() => expect(rollbackDefinitionMock).toHaveBeenCalledWith(7, 101));
  }, 60_000);

  it('新建定义：Modal 提交调用 createDefinition 并刷新列表', async () => {
    createDefinitionMock.mockResolvedValue({ id: 8, code: 'onboard', name: '入职流程' });
    await renderAndOpen();
    fireEvent.click(screen.getByTestId('create-definition'));
    fireEvent.change(await waitFor(() => screen.getByPlaceholderText('hire_review')), {
      target: { value: 'onboard' },
    });
    fireEvent.change(screen.getByPlaceholderText('招聘复核流程'), {
      target: { value: '入职流程' },
    });
    fireEvent.click(buttonNamed('创建'));
    await waitFor(() =>
      expect(createDefinitionMock).toHaveBeenCalledWith({ code: 'onboard', name: '入职流程' }),
    );
    await waitFor(() => expect(fetchDefinitionsMock).toHaveBeenCalledTimes(2));
  }, 60_000);

  it('版本详情可审阅：节点/连线与审核字段回显', async () => {
    fetchVersionsMock.mockResolvedValue([
      version({ status: 'published', published_at: '2026-11-08T11:00:00' }),
    ]);
    fetchVersionDetailMock.mockResolvedValue(detail);
    await renderAndOpen({ openRow: true });
    fireEvent.click(buttonNamed('查看'));
    await waitFor(() => expect(fetchVersionDetailMock).toHaveBeenCalledWith(101));
    // parse_1 在节点表与连线表各出现一次，用唯一文本断言
    await waitFor(() => expect(screen.getByText('解析简历')).toBeTruthy());
    expect(screen.getByText('profile_parse')).toBeTruthy();
    expect(screen.getByText('match_1')).toBeTruthy();
    expect(screen.getByText(/提交人 2/)).toBeTruthy();
  }, 60_000);
});
