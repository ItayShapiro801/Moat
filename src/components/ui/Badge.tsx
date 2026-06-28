import { ReactNode } from "react";

type BadgeVariant = "success" | "danger" | "warning" | "neutral";

interface BadgeProps {
  children: ReactNode;
  variant?: BadgeVariant;
}

const variantClasses: Record<BadgeVariant, string> = {
  success: "bg-moat-accent/15 text-moat-accent border-moat-accent/30",
  danger: "bg-moat-danger/15 text-moat-danger border-moat-danger/30",
  warning: "bg-moat-warning/15 text-moat-warning border-moat-warning/30",
  neutral: "bg-moat-text-muted/15 text-moat-text-muted border-moat-text-muted/30",
};

export function Badge({ children, variant = "neutral" }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${variantClasses[variant]}`}
    >
      {children}
    </span>
  );
}
