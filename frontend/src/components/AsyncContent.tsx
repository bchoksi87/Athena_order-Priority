import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { LoadingState } from "./LoadingState";

export interface AsyncContentProps<T> {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  /** Return true when the loaded data should be treated as empty. */
  isEmpty?: (data: T) => boolean;
  emptyTitle?: string;
  emptyMessage?: string;
  loadingLabel?: string;
  compact?: boolean;
}

/** Renders loading / error / empty states for a TanStack query and hands the data to `children`. */
export function AsyncContent<T>({
  query,
  children,
  isEmpty,
  emptyTitle,
  emptyMessage,
  loadingLabel,
  compact = false,
}: AsyncContentProps<T>) {
  if (query.isPending) return <LoadingState label={loadingLabel} compact={compact} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} compact={compact} />;
  const data = query.data;
  if (isEmpty ? isEmpty(data) : Array.isArray(data) && data.length === 0) {
    return <EmptyState title={emptyTitle} message={emptyMessage} compact={compact} />;
  }
  return <>{children(data)}</>;
}
