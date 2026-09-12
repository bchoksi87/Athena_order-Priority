import type { AlertSeverity, DataQualitySeverity, RiskLevel } from "@/api/types";

import "./RiskBadge.css";

export interface RiskBadgeProps {
  level: RiskLevel | null | undefined;
  title?: string;
}

/** Uppercase mono badge for RiskLevel: LOW / MEDIUM / HIGH / CRITICAL. */
export function RiskBadge({ level, title }: RiskBadgeProps) {
  const cls = level ? `risk risk-${level}` : "risk risk-unknown";
  return (
    <span className={cls} title={title} data-level={level ?? "unknown"}>
      {level ?? "n/a"}
    </span>
  );
}

const ALERT_TO_RISK: Record<AlertSeverity, RiskLevel> = {
  info: "low",
  warning: "medium",
  high: "high",
  critical: "critical",
};

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  return (
    <span className={`risk risk-${ALERT_TO_RISK[severity]}`} data-severity={severity}>
      {severity}
    </span>
  );
}

const DQ_TO_RISK: Record<DataQualitySeverity, RiskLevel> = { blocking: "critical", warning: "medium", info: "low" };

export function DataQualitySeverityBadge({ severity }: { severity: DataQualitySeverity }) {
  return (
    <span className={`risk risk-${DQ_TO_RISK[severity]}`} data-severity={severity}>
      {severity}
    </span>
  );
}
