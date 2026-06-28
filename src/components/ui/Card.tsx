import { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  hover?: boolean;
  className?: string;
  id?: string;
}

export function Card({ children, hover = false, className = "", id }: CardProps) {
  return (
    <div
      id={id}
      className={`rounded-2xl bg-moat-surface border border-moat-border p-6 ${
        hover ? "transition-colors hover:bg-moat-surface-hover" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}
