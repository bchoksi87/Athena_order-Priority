import type { ReactNode } from "react";

import "./States.css";

export interface EmptyStateProps {
  title?: string;
  message?: string;
  action?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ title = "Nothing to show", message, action, compact = false }: EmptyStateProps) {
  return (
    <div className={`state${compact ? " state-compact" : ""}`}>
      <div className="state-title">{title}</div>
      {message ? <div className="state-message">{message}</div> : null}
      {action}
    </div>
  );
}
