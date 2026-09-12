import type { ReactNode } from "react";

import type { MachineStatus, OrderStatus, ReadinessState, ScheduleStatus } from "@/api/types";
import {
  MACHINE_STATUS_TONE,
  ORDER_STATUS_TONE,
  READINESS_LABELS,
  READINESS_TONE,
  SCHEDULE_STATUS_TONE,
  humanize,
  type Tone,
} from "@/lib/constants";

import "./StatusPill.css";

export interface StatusPillProps {
  tone: Tone;
  children: ReactNode;
  outline?: boolean;
  size?: "sm" | "md";
  title?: string;
  dot?: boolean;
}

/** Compact coloured status chip. Use the typed helpers below for domain enums. */
export function StatusPill({ tone, children, outline = false, size = "md", title, dot = true }: StatusPillProps) {
  const cls = ["pill", `pill-${tone}`, outline ? "pill-outline" : "", size === "sm" ? "pill-sm" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={cls} title={title} data-tone={tone}>
      {dot ? <span className="pill-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function OrderStatusPill({ status, size }: { status: OrderStatus; size?: "sm" | "md" }) {
  return (
    <StatusPill tone={ORDER_STATUS_TONE[status] ?? "neutral"} size={size}>
      {humanize(status)}
    </StatusPill>
  );
}

export function ReadinessPill({ state, size }: { state: ReadinessState | null | undefined; size?: "sm" | "md" }) {
  if (!state) return <StatusPill tone="neutral" size={size}>Unknown</StatusPill>;
  return (
    <StatusPill tone={READINESS_TONE[state] ?? "neutral"} size={size}>
      {READINESS_LABELS[state] ?? humanize(state)}
    </StatusPill>
  );
}

export function MachineStatusPill({ status, size }: { status: MachineStatus; size?: "sm" | "md" }) {
  return (
    <StatusPill tone={MACHINE_STATUS_TONE[status] ?? "neutral"} size={size}>
      {humanize(status)}
    </StatusPill>
  );
}

export function ScheduleStatusPill({ status, size }: { status: ScheduleStatus; size?: "sm" | "md" }) {
  return (
    <StatusPill tone={SCHEDULE_STATUS_TONE[status] ?? "neutral"} size={size} outline>
      {humanize(status)}
    </StatusPill>
  );
}
