import "./States.css";

export interface LoadingStateProps {
  label?: string;
  compact?: boolean;
  fullPage?: boolean;
}

export function LoadingState({ label = "Loading", compact = false, fullPage = false }: LoadingStateProps) {
  const cls = ["state", compact ? "state-compact" : "", fullPage ? "state-fullpage" : ""].filter(Boolean).join(" ");
  return (
    <div className={cls} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="state-message">{label}…</span>
    </div>
  );
}
