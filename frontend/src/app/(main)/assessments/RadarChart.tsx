/** 测评能力雷达图：维度与得分由后端方案/结果驱动，维度数量不固定。 */

interface RadarChartProps {
  dimensions: string[];
  scores: number[];
  maxScore?: number;
  size?: number;
}

export default function RadarChart({ dimensions, scores, maxScore = 100, size = 280 }: RadarChartProps) {
  const count = dimensions.length;
  if (!count) return null;
  const center = size / 2;
  const radius = size / 2 - 48;
  const angle = (index: number) => (Math.PI * 2 * index) / count - Math.PI / 2;
  const at = (index: number, ratio: number) => {
    const safe = Math.max(0, Math.min(1, ratio));
    return [
      center + Math.cos(angle(index)) * radius * safe,
      center + Math.sin(angle(index)) * radius * safe,
    ] as const;
  };
  const ring = (ratio: number) =>
    dimensions
      .map((_, index) => at(index, ratio).join(','))
      .join(' ');
  const dataPoints = dimensions
    .map((_, index) => at(index, (scores[index] ?? 0) / maxScore).join(','))
    .join(' ');

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="能力雷达图">
      {[0.25, 0.5, 0.75, 1].map((ratio) => (
        <polygon
          key={ratio}
          points={ring(ratio)}
          fill="none"
          stroke="#e5e7eb"
          strokeWidth={ratio === 1 ? 1.2 : 0.8}
        />
      ))}
      {dimensions.map((_, index) => {
        const [x, y] = at(index, 1);
        return <line key={index} x1={center} y1={center} x2={x} y2={y} stroke="#e5e7eb" strokeWidth={0.8} />;
      })}
      <polygon points={dataPoints} fill="rgba(22,119,255,0.18)" stroke="#1677ff" strokeWidth={1.6} />
      {dimensions.map((dimension, index) => {
        const [x, y] = at(index, 1);
        const [labelX, labelY] = [center + Math.cos(angle(index)) * (radius + 26), center + Math.sin(angle(index)) * (radius + 26)];
        return (
          <g key={dimension}>
            <circle cx={x} cy={y} r={2.4} fill="#1677ff" />
            <text x={labelX} y={labelY} fontSize={11} textAnchor="middle" fill="#4b5563">
              {dimension}
            </text>
            <text x={labelX} y={labelY + 13} fontSize={10} textAnchor="middle" fill="#9ca3af">
              {Math.round(scores[index] ?? 0)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
