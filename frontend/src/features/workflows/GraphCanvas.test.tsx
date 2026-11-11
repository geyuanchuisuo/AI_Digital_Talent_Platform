/**
 * GraphCanvas 画布编辑器测试（T-D16/D10 前端半边）——jsdom 零依赖。
 *
 * 覆盖：调色板加节点（key 自动生成）、删除节点连带连线、连线模式建边
 * （审批源默认 approve 分支）、拖拽改位置（onChange 提交）、审批分支切换。
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { GraphCanvas } from './GraphCanvas';
import type { GraphValue } from './GraphCanvas';

const NODE_TYPES = [
  { type: 'profile_parse', label: '档案解析' },
  { type: 'job_match', label: '岗位匹配' },
  { type: 'human_approval', label: '人工审批' },
];

function baseGraph(): GraphValue {
  return { nodes: [], edges: [] };
}

function renderCanvas(value: GraphValue = baseGraph(), onChange = vi.fn()) {
  const utils = render(<GraphCanvas nodeTypes={NODE_TYPES} value={value} onChange={onChange} />);
  return { ...utils, onChange };
}

describe('GraphCanvas', () => {
  it('调色板添加节点：key 自动生成、onChange 提交', () => {
    const onChange = vi.fn();
    renderCanvas(baseGraph(), onChange);
    fireEvent.click(screen.getByTestId('palette-profile_parse'));
    expect(onChange).toHaveBeenCalledTimes(1);
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.nodes).toHaveLength(1);
    expect(arg.nodes[0].key).toBe('profile_parse_1');
    expect(arg.nodes[0].node_type).toBe('profile_parse');
  });

  it('同型节点 key 自动去重（_2 递增）', () => {
    const existing: GraphValue = {
      nodes: [{
        key: 'profile_parse_1', name: '已有', node_type: 'profile_parse',
        position: { x: 0, y: 0 }, config: null, inputs: [], outputs: [],
      }],
      edges: [],
    };
    const onChange = vi.fn();
    renderCanvas(existing, onChange);
    fireEvent.click(screen.getByTestId('palette-profile_parse'));
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.nodes[1].key).toBe('profile_parse_2');
  });

  it('删除节点连带删除其连线', () => {
    const graph: GraphValue = {
      nodes: [
        { key: 'a', name: 'A', node_type: 'profile_parse', position: { x: 10, y: 10 }, config: null, inputs: [], outputs: [] },
        { key: 'b', name: 'B', node_type: 'job_match', position: { x: 200, y: 10 }, config: null, inputs: [], outputs: [] },
      ],
      edges: [{ source: 'a', target: 'b', branch: '', condition: null }],
    };
    const onChange = vi.fn();
    renderCanvas(graph, onChange);
    fireEvent.click(screen.getByTestId('delete-node-a'));
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.nodes.map((n) => n.key)).toEqual(['b']);
    expect(arg.edges).toHaveLength(0), '连线连带删除';
  });

  it('连线模式：审批源建边默认 approve 分支', () => {
    const graph: GraphValue = {
      nodes: [
        { key: 'appr', name: '审批', node_type: 'human_approval', position: { x: 10, y: 10 }, config: null, inputs: [], outputs: [] },
        { key: 'ok', name: '通过', node_type: 'job_match', position: { x: 200, y: 10 }, config: null, inputs: [], outputs: [] },
      ],
      edges: [],
    };
    const onChange = vi.fn();
    renderCanvas(graph, onChange);
    // 点源节点连线按钮进入连线模式
    fireEvent.click(screen.getByTestId('connect-appr'));
    expect(screen.getByTestId('connect-state').textContent).toContain('appr');
    // 点目标完成建边
    fireEvent.click(screen.getByTestId('canvas-node-ok'));
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.edges).toHaveLength(1);
    expect(arg.edges[0].source).toBe('appr');
    expect(arg.edges[0].target).toBe('ok');
    expect(arg.edges[0].branch).toBe('approve');
  });

  it('非审批源建边 branch 为空', () => {
    const graph: GraphValue = {
      nodes: [
        { key: 'a', name: 'A', node_type: 'profile_parse', position: { x: 10, y: 10 }, config: null, inputs: [], outputs: [] },
        { key: 'b', name: 'B', node_type: 'job_match', position: { x: 200, y: 10 }, config: null, inputs: [], outputs: [] },
      ],
      edges: [],
    };
    const onChange = vi.fn();
    renderCanvas(graph, onChange);
    fireEvent.click(screen.getByTestId('connect-a'));
    fireEvent.click(screen.getByTestId('canvas-node-b'));
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.edges[0].branch).toBe('');
  });

  it('自环与重复连线不建边', () => {
    const graph: GraphValue = {
      nodes: [
        { key: 'a', name: 'A', node_type: 'profile_parse', position: { x: 10, y: 10 }, config: null, inputs: [], outputs: [] },
        { key: 'b', name: 'B', node_type: 'job_match', position: { x: 200, y: 10 }, config: null, inputs: [], outputs: [] },
      ],
      edges: [{ source: 'a', target: 'b', branch: '', condition: null }],
    };
    const onChange = vi.fn();
    renderCanvas(graph, onChange);
    // 自环
    fireEvent.click(screen.getByTestId('canvas-node-a'));
    fireEvent.click(screen.getByTestId('canvas-node-a'));
    expect(onChange).not.toHaveBeenCalled();
    // 重复连线
    fireEvent.click(screen.getByTestId('canvas-node-a'));
    fireEvent.click(screen.getByTestId('canvas-node-b'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('拖拽更新节点位置（onChange 提交新坐标）', () => {
    const graph: GraphValue = {
      nodes: [
        { key: 'a', name: 'A', node_type: 'profile_parse', position: { x: 40, y: 40 }, config: null, inputs: [], outputs: [] },
      ],
      edges: [],
    };
    const onChange = vi.fn();
    const { getByTestId } = renderCanvas(graph, onChange);
    const node = getByTestId('canvas-node-a');
    fireEvent.mouseDown(node, { clientX: 60, clientY: 60 }); // dx=20, dy=20
    const canvas = getByTestId('canvas-area');
    fireEvent.mouseMove(canvas, { clientX: 160, clientY: 140 }); // 期望 x=120, y=100
    const arg = onChange.mock.calls[0][0] as GraphValue;
    expect(arg.nodes[0].position).toEqual({ x: 140, y: 120 });
  });
});
