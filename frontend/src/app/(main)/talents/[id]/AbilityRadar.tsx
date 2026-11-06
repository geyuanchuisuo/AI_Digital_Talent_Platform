/**
 * 能力雷达图（T-21，需求 F2-4）。
 *
 * **实现取舍（开发日志 T-21）**：架构 §5.7 原定 `@ant-design/charts` Radar；实测 v2.6.7
 * 的 Radar 只在极坐标上画线（默认 `children:[{type:'line'}]`），填充面/刻度等 mark 级
 * 配置不透出，且 canvas 依赖迫使用 `next/dynamic` 关 SSR、测试需 mock——权衡后改为
 * **手写 SVG**：五轴确定渲染、无 canvas/SSR 问题、`@ant-design/charts` 保留依赖暂不删。
 *
 * 五维键名固定：professional / project_experience / education / stability / growth_potential
 * （Prompt B 契约，`schemas/profile.py` AbilityScores）；score 0–100（后端已 clamp）。
 */

'use client';

export const DIM_LABELS: Record<string, string> = {
  professional: '专业能力',
  project_experience: '项目经验',
  education: '教育背景',
  stability: '稳定性',
  growth_potential: '成长潜力',
};

export function abilityToChartData(scores: Record<string, { score: number; basis: string }>) {
  return Object.entries(DIM_LABELS).map(([key, label]) => ({
    key,
    dim: label,
    score: scores[key]?.score ?? 0,
    basis: scores[key]?.basis ?? '',
  }));
}

const SIZE = 280;
const CENTER = SIZE / 2;
const RADIUS = 92;
/** 从正上方开始，顺时针排五维 */
const RINGS = [0.25, 0.5, 0.75, 1];

function point(dimIndex: number, value: number): [number, number] {
  const angle = (Math.PI * 2 * dimIndex) / 5 - Math.PI / 2;
  const r = (value / 100) * RADIUS;
  return [CENTER + r * Math.cos(angle), CENTER + r * Math.sin(angle)];
}

function polygon(values: number[]): string {
  // SVG <polygon points> 只接受 "x,y x,y ..." 坐标列表（不是 path 的 M/L/Z 语法）
  return values
    .map((value, index) => {
      const [x, y] = point(index, value);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export default function AbilityRadar({
  scores,
}: {
  scores: Record<string, { score: number; basis: string }>;
}) {
  const data = abilityToChartData(scores);
  return (
    <svg
      width="100%"
      viewBox={`0 0 ${SIZE} ${SIZE + 8}`}
      role="img"
      aria-label="能力雷达图"
      data-testid="ability-radar"
    >
      {/* 网格环 */}
      {RINGS.map((ring) => (
        <polygon
          key={ring}
          points={polygon(data.map(() => ring * 100))}
          fill="none"
          stroke="#e8e8e8"
          strokeWidth={1}
        />
      ))}
      {/* 轴线 + 维度标签 + 分值 */}
      {data.map((item, index) => {
        const [ax, ay] = point(index, 100);
        const [lx, ly] = point(index, 128);
        const [vx, vy] = point(index, item.score);
        return (
          <g key={item.key}>
            <line x1={CENTER} y1={CENTER} x2={ax} y2={ay} stroke="#e8e8e8" strokeWidth={1} />
            <text
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={12}
              fill="rgba(0,0,0,0.65)"
            >
              {item.dim}
            </text>
            <text x={vx} y={vy - 10} textAnchor="middle" fontSize={11} fill="#1677ff">
              {item.score}
            </text>
          </g>
        );
      })}
      {/* 数据多边形 */}
      <polygon
        points={polygon(data.map((item) => item.score))}
        fill="rgba(22,119,255,0.18)"
        stroke="#1677ff"
        strokeWidth={2}
      />
      {data.map((item, index) => {
        const [x, y] = point(index, item.score);
        return <circle key={item.key} cx={x} cy={y} r={3.5} fill="#1677ff" />;
      })}
    </svg>
  );
}
