import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AnalyticsPage from './page';
import { fetchActiveJobs, streamActiveJobsEvents } from './api';
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
}));

const fetchMock = vi.mocked(fetchActiveJobs);
const streamMock = vi.mocked(streamActiveJobsEvents);

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

describe('AnalyticsPage（T-D01b 切片 + T-D10 多维分析）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockResolvedValue(data());
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
});
