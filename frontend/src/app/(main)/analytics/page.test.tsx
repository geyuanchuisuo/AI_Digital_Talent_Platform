import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AnalyticsPage from './page';
import { createFeedback, fetchActiveJobs, fetchFeedback, streamActiveJobsEvents } from './api';
import type { ActiveJobsData, MetricData } from './api';

// jsdom 无 canvas：mock 图表组件，仅验证「拿到数据即渲染」与传入的数据形态
vi.mock('@ant-design/charts', () => ({
  Bar: (props: { data: Array<Record<string, unknown>> }) => (
    <div data-testid="bar-chart">{JSON.stringify(props.data)}</div>
  ),
}));

vi.mock('./api', () => ({
  fetchActiveJobs: vi.fn(),
  streamActiveJobsEvents: vi.fn(),
  createFeedback: vi.fn(),
  fetchFeedback: vi.fn(),
}));

const fetchMock = vi.mocked(fetchActiveJobs);
const streamMock = vi.mocked(streamActiveJobsEvents);
const createFeedbackMock = vi.mocked(createFeedback);
const fetchFeedbackMock = vi.mocked(fetchFeedback);

function data(patch: Partial<MetricData> = {}): ActiveJobsData {
  return {
    metric_id: 'jobs.active',
    name: '活跃岗位数',
    value: 4,
    source: 'replica',
    cache_hit: false,
    data_time: '2026-11-08T00:00:00',
    snapshot_id: 1,
    table: { columns: ['department', 'active_jobs'], rows: [{ department: '技术部', active_jobs: 4 }] },
    note: null,
    group_by: null,
    chart: {
      chart_type: 'number',
      label: '活跃岗位数',
      value: 4,
    },
    interpretation: {
      generator: 'rule',
      segments: [
        { kind: 'fact', key: 'headline', text: '活跃岗位合计 4 个。', ref: { value: 4 } },
        {
          kind: 'inference',
          key: 'suggest',
          text: '建议按部门查看分布。',
          basis: ['headline'],
        },
      ],
    },
    ...patch,
  };
}

describe('AnalyticsPage（T-D01b 切片 + T-D10 多维分析 + T-D12 反馈）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockResolvedValue(data());
    fetchFeedbackMock.mockResolvedValue([]);
  });

  it('渲染数值与来源/缓存标记', async () => {
    const { container } = render(<AnalyticsPage />);
    await waitFor(() =>
      expect(container.querySelector('.ant-statistic-content-value')?.textContent).toBe('4'),
    );
    expect(screen.getByText('来源：PgSQL 副本')).toBeTruthy();
    expect(screen.getByText('缓存未命中')).toBeTruthy();
  });

  it('按部门分组：bar 图表数据与接口返回的 chart.data 一致', async () => {
    fetchMock.mockResolvedValue(
      data({
        group_by: 'department',
        chart: {
          chart_type: 'bar',
          x_field: 'name',
          y_field: 'value',
          data: [{ name: '技术部', value: 4 }],
        },
        interpretation: {
          generator: 'rule',
          segments: [
            { kind: 'fact', key: 'headline', text: '活跃岗位合计 4 个，分布于 1 个部门。', ref: {} },
          ],
        },
      }),
    );
    render(<AnalyticsPage />);
    const sw = screen.getByRole('switch');
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByTestId('bar-chart')).toBeTruthy());
    const payload = JSON.parse(
      (screen.getByTestId('bar-chart') as HTMLElement).textContent ?? '[]',
    );
    expect(payload).toEqual([{ name: '技术部', value: 4 }]);
    expect(screen.getByText('分组：按部门')).toBeTruthy();
  });

  it('解读分段渲染 事实/推断 标记（机器可区分）', async () => {
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText('事实')).toBeTruthy());
    expect(screen.getByText('推断')).toBeTruthy();
    expect(screen.getByText('活跃岗位合计 4 个。')).toBeTruthy();
    expect(screen.getByText('建议按部门查看分布。')).toBeTruthy();
  });

  it('SSE 按钮消费流并显示最终阶段', async () => {
    streamMock.mockImplementation(async (onEvent) => {
      onEvent('progress', { stage: 'started' });
      onEvent('done', { value: 4 });
    });
    render(<AnalyticsPage />);
    const sseButton = await waitFor(() => {
      const btn = screen.getByText('计算进度（SSE）').closest('button') as HTMLButtonElement;
      expect(btn.disabled).toBeFalsy();
      return btn;
    });
    fireEvent.click(sseButton);
    await waitFor(() => expect(screen.getByText('done')).toBeTruthy());
    expect(streamMock).toHaveBeenCalledTimes(1);
  });

  // ---- T-D12 反馈入口 ----

  it('标记不准确：提交反馈调用接口并刷新列表（验收①关联快照）', async () => {
    createFeedbackMock.mockResolvedValue({
      id: 9,
      snapshot_id: 1,
      comment: '数字对不上',
      created_by: 2,
      created_at: '2026-11-08T12:00:00',
      snapshot_metric_id: 'jobs.active',
    });
    fetchFeedbackMock.mockResolvedValue([
      {
        id: 9,
        snapshot_id: 1,
        comment: '数字对不上',
        created_by: 2,
        created_at: '2026-11-08T12:00:00',
        snapshot_metric_id: 'jobs.active',
        snapshot: { metric_id: 'jobs.active', params_json: null, data_time: null, result_json: null },
      },
    ]);
    render(<AnalyticsPage />);
    const markBtn = await waitFor(() => {
      const btn = screen.getByText('标记不准确').closest('button') as HTMLButtonElement;
      expect(btn.disabled).toBeFalsy();
      return btn;
    });
    fireEvent.click(markBtn);
    const okBtn = await waitFor(() => screen.getByText('提交反馈'));
    fireEvent.click(okBtn);
    await waitFor(() => expect(createFeedbackMock).toHaveBeenCalledTimes(1));
    expect(createFeedbackMock).toHaveBeenCalledWith(1, undefined); // 关联快照 #1
    await waitFor(() => expect(screen.getByText('数字对不上')).toBeTruthy());
  });

  it('反馈列表 403（非管理侧）时隐藏列表区', async () => {
    fetchFeedbackMock.mockRejectedValue(new Error('403'));
    render(<AnalyticsPage />);
    await waitFor(() => expect(fetchFeedbackMock).toHaveBeenCalled());
    // 403 → 列表区整体不渲染
    await waitFor(() => expect(screen.queryByText('反馈记录（管理侧可见）')).toBeNull());
  });

  it('反馈列表渲染条目与关联指标', async () => {
    fetchFeedbackMock.mockResolvedValue([
      {
        id: 7,
        snapshot_id: 2,
        comment: '缺口口径存疑',
        created_by: 2,
        created_at: '2026-11-08T11:00:00',
        snapshot_metric_id: 'job.demand_progress_gap',
        snapshot: {
          metric_id: 'job.demand_progress_gap',
          params_json: null,
          data_time: '2026-11-08T11:00:00',
          result_json: { value: 4 },
        },
      },
    ]);
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText('缺口口径存疑')).toBeTruthy());
    expect(screen.getByText('job.demand_progress_gap')).toBeTruthy();
  });

  it('无快照时标记按钮禁用（无关联对象）', async () => {
    fetchMock.mockResolvedValue(data({ snapshot_id: null }));
    render(<AnalyticsPage />);
    await waitFor(() => {
      const btn = screen.getByText('标记不准确').closest('button') as HTMLButtonElement;
      expect(btn.disabled).toBeTruthy();
    });
  });

  // ---- 错误态防御（T-A02 缺陷修复：错误响应不喂给下游 antd 组件） ----

  it('查询失败时渲染错误态而非崩溃（不把错误响应当数组 spread）', async () => {
    fetchMock.mockRejectedValue(new Error('未登记的指标（白名单外不支持）'));
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText('查询失败')).toBeTruthy());
    expect(screen.getByText('未登记的指标（白名单外不支持）')).toBeTruthy();
    // 错误态下不应出现数值卡内容（value 为 null 时 Statistic 显示 '-'，但来源/缓存 Tag 不应出现）
    expect(screen.queryByText('来源：PgSQL 副本')).toBeNull();
    // List 不应被错误响应喂成非数组（无崩溃即通过；这里断言解读区不出现）
    expect(screen.queryByText('事实')).toBeNull();
  });

  it('查询失败后重试按钮可再次触发查询', async () => {
    // 先排好 mock 队列：首次（mount 时 useEffect 触发）拒绝，二次（点重试）成功
    fetchMock
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValueOnce(data({ value: 7 }));
    const { container } = render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText('查询失败')).toBeTruthy());
    // antd Button 会把两字中文拆成两个 span（'重 试'），用锚定正则匹配
    fireEvent.click(screen.getByRole('button', { name: /^重\s*试$/ }));
    await waitFor(() =>
      expect(container.querySelector('.ant-statistic-content-value')?.textContent).toBe('7'),
    );
  });
});
