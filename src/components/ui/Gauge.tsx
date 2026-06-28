"use client";

interface GaugeProps {
  value: number;
  min: number;
  max: number;
  label?: string;
}

export function Gauge({ value, min, max, label }: GaugeProps) {
  const range = max - min;
  const normalized = Math.max(0, Math.min(1, (value - min) / range));

  const radius = 80;
  const strokeWidth = 12;
  const cx = 100;
  const cy = 100;
  const startAngle = Math.PI;
  const sweepAngle = Math.PI;

  function polarToCartesian(angle: number) {
    return {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  }

  function describeArc(startFraction: number, endFraction: number) {
    const start = polarToCartesian(startAngle + sweepAngle * startFraction);
    const end = polarToCartesian(startAngle + sweepAngle * endFraction);
    const largeArc = endFraction - startFraction > 0.5 ? 1 : 0;
    return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;
  }

  const needleAngle = startAngle + sweepAngle * normalized;
  const needleLength = radius - 20;
  const needleTip = {
    x: cx + needleLength * Math.cos(needleAngle),
    y: cy + needleLength * Math.sin(needleAngle),
  };

  const formattedValue =
    value >= 0 ? `+${value.toFixed(1)}%` : `${value.toFixed(1)}%`;

  return (
    <div className="flex flex-col items-center gap-2">
      <svg viewBox="0 0 200 120" className="w-56 h-auto">
        {/* Track background */}
        <path
          d={describeArc(0, 1)}
          fill="none"
          stroke="#1f2733"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
        {/* Red segment: 0–33% */}
        <path
          d={describeArc(0, 0.33)}
          fill="none"
          stroke="#f87171"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          opacity={0.8}
        />
        {/* Yellow segment: 33–60% */}
        <path
          d={describeArc(0.33, 0.6)}
          fill="none"
          stroke="#fbbf24"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          opacity={0.8}
        />
        {/* Green segment: 60–100% */}
        <path
          d={describeArc(0.6, 1)}
          fill="none"
          stroke="#34d399"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          opacity={0.8}
        />
        {/* Needle */}
        <line
          x1={cx}
          y1={cy}
          x2={needleTip.x}
          y2={needleTip.y}
          stroke="#e8eaed"
          strokeWidth={2.5}
          strokeLinecap="round"
        />
        <circle cx={cx} cy={cy} r={4} fill="#e8eaed" />
        {/* Value text */}
        <text
          x={cx}
          y={cy - 15}
          textAnchor="middle"
          className="font-mono text-lg fill-moat-text"
          fontSize="18"
          fontWeight="600"
        >
          {formattedValue}
        </text>
      </svg>
      {label && (
        <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          {label}
        </span>
      )}
    </div>
  );
}
