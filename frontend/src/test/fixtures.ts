/** Shared test fixtures mirroring backend result shapes. */
import type { PriorityResult, ScheduleEntry } from "@/api/types";

export function makeEntry(overrides: Partial<ScheduleEntry> = {}): ScheduleEntry {
  return {
    entry_id: "e1",
    machine_id: "CNC-01",
    order_id: "R3D-10482",
    operation_id: "op1",
    sequence_on_machine: 1,
    setup_start: "2026-09-11T08:00:00Z",
    start: "2026-09-11T08:30:00Z",
    end: "2026-09-11T11:30:00Z",
    setup_minutes: 30,
    run_minutes: 180,
    quantity: 20,
    priority_score: 91,
    placement_reason: "Highest priority ready on CNC-01",
    is_last_operation: true,
    expected_completion: "2026-09-11T11:30:00Z",
    due_date: "2026-09-12T18:00:00Z",
    expected_lateness_hours: null,
    locked: false,
    batch_key: null,
    setup_family: "F-A",
    material_id: "AL-6061",
    customer_id: "C-1",
    ...overrides,
  };
}

export function makePriorityResult(overrides: Partial<PriorityResult> = {}): PriorityResult {
  return {
    order_id: "R3D-10482",
    score: 91,
    base_score: 85,
    factors: [
      { key: "due_date_urgency", name: "Due Date Urgency", kind: "bonus", raw_score: 95, weight: 0.25, points: 23.75, reason: "Due in 18 hours", details: {} },
      { key: "sla_risk", name: "SLA Risk", kind: "bonus", raw_score: 85, weight: 0.15, points: 12.75, reason: "SLA 25% remaining", details: {} },
      { key: "customer_importance", name: "Customer Importance", kind: "bonus", raw_score: 100, weight: 0.2, points: 20, reason: "Strategic customer", details: {} },
      { key: "setup_efficiency", name: "Setup Efficiency", kind: "penalty", raw_score: 60, weight: 0.05, points: -3, reason: "Changeover required", details: {} },
    ],
    adjustments: [{ kind: "aging", points: 6, reason: "Waiting 8 days", source_id: null }],
    readiness: "ready",
    blocked: false,
    blocking_reasons: [],
    risk_level: "high",
    explanation: "Order R3D-10482 scores 91.",
    profile_id: "PriorityProfile-A",
    profile_version: 1,
    computed_at: "2026-09-11T06:00:00Z",
    hours_until_due: 18,
    projected_completion: "2026-09-11T11:30:00Z",
    projected_lateness_hours: null,
    forced_next: false,
    rank: 1,
    ...overrides,
  };
}
