import { scoreTone } from "../lib/format";

export function ScoreRing({
  percentage,
  size = 132,
  label = "score",
}: {
  percentage: number;
  size?: number;
  label?: string;
}) {
  const radius = (size - 14) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, percentage));
  const offset = circumference - (clamped / 100) * circumference;
  const color =
    percentage >= 75 ? "var(--success)" : percentage >= 50 ? "var(--warning)" : "var(--danger)";
  return (
    <div className="score-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--line)"
          strokeWidth={9}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={9}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dashoffset .6s ease" }}
        />
      </svg>
      <div className="score-ring-label">
        <strong>{Math.round(percentage)}%</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}

export function TrendChart({
  points,
  height = 150,
}: {
  points: Array<{ label?: string; percentage: number }>;
  height?: number;
}) {
  if (!points.length) {
    return <div className="chart-empty">Complete a practice test to build your trend.</div>;
  }
  const width = 520;
  const padding = 16;
  const maxY = Math.max(100, ...points.map((point) => point.percentage));
  const stepX = points.length > 1 ? (width - padding * 2) / (points.length - 1) : 0;
  const coords = points.map((point, index) => ({
    x: padding + index * stepX,
    y: height - padding - (point.percentage / maxY) * (height - padding * 2),
  }));
  const line = coords.map((point) => `${point.x},${point.y}`).join(" ");
  const area = `${padding},${height - padding} ${line} ${coords[coords.length - 1].x},${height - padding}`;
  return (
    <svg
      className="trend-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Score trend"
    >
      <defs>
        <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[25, 50, 75].map((tick) => {
        const y = height - padding - (tick / maxY) * (height - padding * 2);
        return (
          <line
            key={tick}
            x1={padding}
            x2={width - padding}
            y1={y}
            y2={y}
            stroke="var(--line)"
            strokeDasharray="3 5"
          />
        );
      })}
      <polygon points={area} fill="url(#trend-fill)" />
      <polyline
        points={line}
        fill="none"
        stroke="var(--accent)"
        strokeWidth={2.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {coords.map((point, index) => (
        <circle key={index} cx={point.x} cy={point.y} r={3.5} fill="var(--accent)" />
      ))}
    </svg>
  );
}

export function TopicBars({
  items,
}: {
  items: Array<{ label: string; percentage: number; detail?: string }>;
}) {
  if (!items.length) return <div className="chart-empty">No topic data yet.</div>;
  return (
    <div className="topic-bars">
      {items.map((item) => {
        const tone = scoreTone(item.percentage);
        return (
          <div className="topic-bar" key={item.label}>
            <div className="topic-bar-head">
              <span>{item.label}</span>
              <strong>
                {Math.round(item.percentage)}%
                {item.detail && <em>{item.detail}</em>}
              </strong>
            </div>
            <div className="topic-track">
              <div className={`topic-fill tone-${tone}`} style={{ width: `${Math.max(2, Math.min(100, item.percentage))}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function MiniBars({ values }: { values: number[] }) {
  const max = Math.max(...values, 1);
  return (
    <div className="mini-bars" aria-hidden>
      {values.map((value, index) => (
        <span key={index} style={{ height: `${Math.max(8, (value / max) * 100)}%` }} />
      ))}
    </div>
  );
}
