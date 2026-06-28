interface StatBlockProps {
  label: string;
  value: string;
  delta?: {
    value: string;
    direction: "up" | "down";
  };
}

export function StatBlock({ label, value, delta }: StatBlockProps) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
        {label}
      </span>
      <span className="text-3xl font-semibold font-mono text-moat-text">
        {value}
      </span>
      {delta && (
        <span
          className={`inline-flex items-center gap-1 text-sm font-mono font-medium ${
            delta.direction === "up" ? "text-moat-accent" : "text-moat-danger"
          }`}
        >
          <span>{delta.direction === "up" ? "▲" : "▼"}</span>
          {delta.value}
        </span>
      )}
    </div>
  );
}
