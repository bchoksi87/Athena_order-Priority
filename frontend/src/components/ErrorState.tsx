import type { ReactNode } from "react";

import { describeError, isApiError } from "@/api/client";

import "./States.css";

export interface ErrorStateProps {
  title?: string;
  message?: string;
  error?: unknown;
  onRetry?: () => void;
  action?: ReactNode;
  compact?: boolean;
}

/** Renders a normalised API/JS error with an optional retry button. */
export function ErrorState({ title, message, error, onRetry, action, compact = false }: ErrorStateProps) {
  const api = isApiError(error) ? error : null;
  const heading =
    title ??
    (api?.isNetwork
      ? "Backend unreachable"
      : api?.isForbidden
        ? "Not authorised"
        : api?.isNotFound
          ? "Not found"
          : "Request failed");
  const text = message ?? (error ? describeError(error) : undefined);
  return (
    <div className={`state state-error${compact ? " state-compact" : ""}`} role="alert">
      <div className="state-title">{heading}</div>
      {text ? <div className="state-message">{text}</div> : null}
      {api ? (
        <div className="state-code">
          {api.code}
          {api.status ? ` · HTTP ${api.status}` : ""}
        </div>
      ) : null}
      {onRetry ? (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
      {action}
    </div>
  );
}
