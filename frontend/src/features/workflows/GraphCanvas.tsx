/**
 * 工作流图编辑画布（T-D16/D10 前端半边）——零新增依赖（antd + 原生 SVG）。
 *
 * 职责：
 * - 节点：从白名单调色板添加（key 自动生成可改）、拖拽移动、选中后侧栏编辑、删除；
 * - 连线：连线模式（点源点→点目标）建立；人工审批出边强制选分支 approve/reject；
 *   其他节点出边 branch 为空；自环/重复连线在保存时由服务端校验兜底，前端预检提示；
 * - 删除节点连带删除其连线；删除连线不影响节点；
 * - 不做缩放/多选/框选（一期口径），JSON 输入保留为高级模式。
 *
 * 受控组件：value（节点/连线）/onChange 双向；拖拽为本地交互态，松手才提交 onChange。
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import { Button, Input, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { DeleteOutlined, LinkOutlined, PlusOutlined } from '@ant-design/icons';

export interface CanvasNode {
  key: string;
  name: string;
  node_type: string;
  position: { x: number; y: number };
  config: Record<string, unknown> | null;
  inputs: Array<Record<string, unknown>>;
  outputs: Array<Record<string, unknown>>;
}

export interface CanvasEdge {
  source: string;
  target: string;
  branch: string;
  condition: Record<string, unknown> | null;
}

export interface GraphValue {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

export interface NodeTypeMeta {
  type: string;
  label: string;
}

const NODE_W = 148;
const NODE_H = 56;
const CANVAS_W = 720;
const CANVAS_H = 420;

const EDGE_COLOR_DEFAULT = '#8c8c8c';
const EDGE_COLOR_APPROVE = '#52c41a';
const EDGE_COLOR_REJECT = '#ff4d4f';

function edgeColor(branch: string): string {
  if (branch === 'approve') return EDGE_COLOR_APPROVE;
  if (branch === 'reject') return EDGE_COLOR_REJECT;
  return EDGE_COLOR_DEFAULT;
}

interface Props {
  nodeTypes: NodeTypeMeta[];
  value: GraphValue;
  onChange: (value: GraphValue) => void;
}

export function GraphCanvas({ nodeTypes, value, onChange }: Props) {
  const { nodes, edges } = value;
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ key: string; dx: number; dy: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const typeLabel = useCallback(
    (t: string) => nodeTypes.find((nt) => nt.type === t)?.label ?? t,
    [nodeTypes],
  );

  const nodeByKey = useMemo(() => {
    const m: Record<string, CanvasNode> = {};
    for (const n of nodes) m[n.key] = n;
    return m;
  }, [nodes]);

  const updateNode = useCallback(
    (key: string, patch: Partial<CanvasNode>) => {
      onChange({
        nodes: nodes.map((n) => (n.key === key ? { ...n, ...patch } : n)),
        edges,
      });
    },
    [nodes, edges, onChange],
  );

  const addNode = useCallback(
    (nodeType: string) => {
      const base = nodeType;
      let i = 1;
      while (nodes.some((n) => n.key === `${base}_${i}`)) i += 1;
      const key = `${base}_${i}`;
      const label = nodeTypes.find((nt) => nt.type === nodeType)?.label ?? nodeType;
      const usedY = nodes.map((n) => n.position.y);
      const y = usedY.length ? Math.max(...usedY) + 96 : 40;
      onChange({
        nodes: [
          ...nodes,
          {
            key,
            name: `${label} ${i}`,
            node_type: nodeType,
            position: { x: 40 + ((nodes.length * 60) % 480), y },
            config: null,
            inputs: [],
            outputs: [],
          },
        ],
        edges,
      });
      setSelectedKey(key);
    },
    [nodes, edges, nodeTypes, onChange],
  );

  const deleteNode = useCallback(
    (key: string) => {
      onChange({
        nodes: nodes.filter((n) => n.key !== key),
        edges: edges.filter((e) => e.source !== key && e.target !== key),
      });
      if (selectedKey === key) setSelectedKey(null);
      if (connectFrom === key) setConnectFrom(null);
    },
    [nodes, edges, selectedKey, connectFrom, onChange],
  );

  const startConnect = useCallback(
    (key: string) => {
      if (connectFrom === key) {
        setConnectFrom(null);
        return;
      }
      if (connectFrom == null) {
        setConnectFrom(key);
        return;
      }
      // 连线模式：connectFrom → key
      const src = nodeByKey[connectFrom];
      if (src == null) return;
      const branch = src.node_type === 'human_approval' ? 'approve' : '';
      const dup = edges.some(
        (e) => e.source === connectFrom && e.target === key && e.branch === branch,
      );
      if (key === connectFrom) {
        setConnectFrom(null);
        return;
      }
      if (dup) {
        setConnectFrom(null);
        return;
      }
      onChange({ nodes, edges: [...edges, { source: connectFrom, target: key, branch, condition: null }] });
      setConnectFrom(null);
    },
    [connectFrom, nodeByKey, edges, nodes, onChange],
  );

  const deleteEdge = useCallback(
    (index: number) => {
      onChange({ nodes, edges: edges.filter((_, i) => i !== index) });
    },
    [nodes, edges, onChange],
  );

  const setEdgeBranch = useCallback(
    (index: number, branch: string) => {
      onChange({
        nodes,
        edges: edges.map((e, i) => (i === index ? { ...e, branch } : e)),
      });
    },
    [nodes, edges, onChange],
  );

  // 拖拽：mousemove 在画布层监听，松手提交
  const onNodeMouseDown = (e: React.MouseEvent, node: CanvasNode) => {
    e.stopPropagation();
    setSelectedKey(node.key);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    setDrag({
      key: node.key,
      dx: e.clientX - rect.left - node.position.x,
      dy: e.clientY - rect.top - node.position.y,
    });
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (drag == null) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = Math.max(0, Math.min(CANVAS_W - NODE_W, e.clientX - rect.left - drag.dx));
    const y = Math.max(0, Math.min(CANVAS_H - NODE_H, e.clientY - rect.top - drag.dy));
    onChange({
      nodes: nodes.map((n) => (n.key === drag.key ? { ...n, position: { x, y } } : n)),
      edges,
    });
  };

  const endDrag = () => setDrag(null);

  const selected = selectedKey != null ? nodeByKey[selectedKey] : null;

  return (
    <div data-testid="graph-canvas" style={{ display: 'flex', gap: 12 }}>
      {/* 调色板 */}
      <div style={{ width: 128, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          添加节点
        </Typography.Text>
        {nodeTypes.map((nt) => (
          <Button
            key={nt.type}
            size="small"
            icon={<PlusOutlined />}
            data-testid={`palette-${nt.type}`}
            onClick={() => addNode(nt.type)}
          >
            {nt.label}
          </Button>
        ))}
        <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
          连线模式
        </Typography.Text>
        <Tag color={connectFrom ? 'processing' : 'default'} data-testid="connect-state">
          {connectFrom ? `源：${connectFrom}（点目标完成）` : '点节点开始连线'}
        </Tag>
      </div>

      {/* 画布 */}
      <div
        ref={canvasRef}
        data-testid="canvas-area"
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onClick={() => setSelectedKey(null)}
        style={{
          position: 'relative',
          width: CANVAS_W,
          height: CANVAS_H,
          border: '1px solid #d9d9d9',
          borderRadius: 6,
          background: '#fafafa',
          overflow: 'hidden',
        }}
      >
        <svg width={CANVAS_W} height={CANVAS_H} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          {edges.map((e, i) => {
            const s = nodeByKey[e.source];
            const t = nodeByKey[e.target];
            if (s == null || t == null) return null;
            const x1 = s.position.x + NODE_W;
            const y1 = s.position.y + NODE_H / 2;
            const x2 = t.position.x;
            const y2 = t.position.y + NODE_H / 2;
            const mx = (x1 + x2) / 2;
            return (
              <g key={`${e.source}-${e.target}-${e.branch}-${i}`} data-testid={`edge-${i}`}>
                <path
                  d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                  stroke={edgeColor(e.branch)}
                  strokeWidth={1.5}
                  fill="none"
                  style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    deleteEdge(i);
                  }}
                />
                {e.branch && (
                  <text x={mx} y={(y1 + y2) / 2 - 6} fill={edgeColor(e.branch)} fontSize={11} textAnchor="middle">
                    {e.branch}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {nodes.map((n) => (
          <div
            key={n.key}
            data-testid={`canvas-node-${n.key}`}
            onMouseDown={(e) => onNodeMouseDown(e as unknown as React.MouseEvent, n)}
            onClick={(e) => {
              e.stopPropagation();
              if (connectFrom != null) startConnect(n.key);
              else setSelectedKey(n.key);
            }}
            style={{
              position: 'absolute',
              left: n.position.x,
              top: n.position.y,
              width: NODE_W,
              minHeight: NODE_H,
              padding: 8,
              borderRadius: 6,
              background: '#fff',
              border: `1.5px solid ${selectedKey === n.key ? '#1677ff' : connectFrom === n.key ? '#faad14' : '#bfbfbf'}`,
              cursor: 'grab',
              userSelect: 'none',
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 600 }}>{n.name}</div>
            <div style={{ fontSize: 11, color: '#8c8c8c' }}>
              {typeLabel(n.node_type)} · {n.key}
            </div>
            <Tooltip title="连线（点源节点后点目标节点）">
              <Button
                size="small"
                type="text"
                icon={<LinkOutlined />}
                data-testid={`connect-${n.key}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  startConnect(n.key);
                }}
                style={{ position: 'absolute', top: 2, right: 24, color: connectFrom === n.key ? '#faad14' : undefined }}
              />
            </Tooltip>
            <Tooltip title="删除节点（连带连线）">
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                data-testid={`delete-node-${n.key}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  deleteNode(n.key);
                }}
                style={{ position: 'absolute', top: 2, right: 2 }}
              />
            </Tooltip>
          </div>
        ))}
        {nodes.length === 0 && (
          <Typography.Text type="secondary" style={{ position: 'absolute', left: '42%', top: '46%' }}>
            从左侧调色板添加节点
          </Typography.Text>
        )}
      </div>

      {/* 选中节点编辑 */}
      <div style={{ width: 220, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          节点属性
        </Typography.Text>
        {selected == null ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            选中节点后编辑
          </Typography.Text>
        ) : (
          <>
            <Input
              size="small"
              value={selected.name}
              onChange={(e) => updateNode(selected.key, { name: e.target.value })}
              placeholder="节点名称"
            />
            <Tag>{typeLabel(selected.node_type)}</Tag>
            <Input
              size="small"
              value={selected.key}
              disabled={edges.some((e) => e.source === selected.key || e.target === selected.key)}
              onChange={(e) => {
                const nk = e.target.value;
                onChange({
                  nodes: nodes.map((n) => (n.key === selected.key ? { ...n, key: nk } : n)),
                  edges: edges.map((e) => ({
                    ...e,
                    source: e.source === selected.key ? nk : e.source,
                    target: e.target === selected.key ? nk : e.target,
                  })),
                });
                setSelectedKey(nk || null);
              }}
              placeholder="key（小写蛇形）"
            />
            <Input.TextArea
              size="small"
              rows={2}
              value={JSON.stringify(selected.config ?? {})}
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value) as Record<string, unknown>;
                  updateNode(selected.key, { config: parsed });
                } catch {
                  /* 非法 JSON 不即时提交（保存时服务端兜底） */
                }
              }}
              placeholder="config（JSON）"
            />
            <Input.TextArea
              size="small"
              rows={2}
              value={JSON.stringify(selected.inputs ?? [])}
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value) as Array<Record<string, unknown>>;
                  updateNode(selected.key, { inputs: parsed });
                } catch {
                  /* 同上 */
                }
              }}
              placeholder="inputs（JSON 数组）"
            />
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              data-testid="panel-delete-node"
              onClick={() => deleteNode(selected.key)}
            >
              删除节点
            </Button>
          </>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
          连线列表（点线删除）
        </Typography.Text>
        {edges.map((e, i) => (
          <div key={`${e.source}-${e.target}-${e.branch}-${i}`} style={{ fontSize: 12 }}>
            <Space size={4}>
              <LinkOutlined />
              <Typography.Text delete={false}>
                {e.source} → {e.target}
              </Typography.Text>
            </Space>
            {nodeByKey[e.source]?.node_type === 'human_approval' && (
              <Select
                size="small"
                value={e.branch || 'approve'}
                style={{ width: 90, marginLeft: 4 }}
                onChange={(v) => setEdgeBranch(i, v)}
                options={[
                  { value: 'approve', label: 'approve' },
                  { value: 'reject', label: 'reject' },
                ]}
              />
            )}
            <Button size="small" type="text" danger onClick={() => deleteEdge(i)}>
              删
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
