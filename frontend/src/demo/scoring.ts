/**
 * Client-side port of the priority engine's arithmetic (backend/app/engines/priority/
 * {engine,adjustments,ranking,explanation}.py) so the demo can re-score orders when a
 * weight changes or a planner adds an expedite / override / hold:
 *
 *   base_score = Σ factor.points            (points = normalised weight × captured raw score,
 *                                             negative for penalty factors)
 *   score      = base_score + ERP priority + customer rule + aging + starvation + expedite
 *              + overrides (INCREASE/DECREASE delta, SET_PRIORITY, FORCE_NEXT → 100)
 *   score      = clamp(score, 0, 100), then the optional blocked-order cap
 *
 * Raw factor scores come from the captured engine run; only the weighting, the
 * adjustments, the clamp/cap, the ranking (with the fairness top-N share rule) and the
 * explanation lines are recomputed here — exactly as the engine does it.
 */
import type {
  CustomerRule,
  ExpediteResponse,
  ExplanationLine,
  FactorScore,
  FairnessConfig,
  OverrideResponse,
  PriorityAdjustment,
  PriorityProfile,
  ReadinessState,
  RiskLevel,
} from "@/api/types";

import { describeHours, fmtG, sortBy } from "./util";

export const SCORE_MIN = 0;
export const SCORE_MAX = 100;
export const FORCE_NEXT_SCORE = 100;
const EPS = 1e-9;

export const ADJUSTMENT_LABELS: Record<string, string> = {
  aging: "Aging",
  fairness: "Fairness",
  expedite: "Expedite",
  override: "Override",
  customer_rule: "Customer rule",
  erp_priority: "ERP priority",
};

export interface LiveHold {
  by: string;
  reason: string;
}

/** Everything the scorer needs for one order (captured facts + live planner overlays). */
export interface ScoringInput {
  order_id: string;
  customer_id: string;
  erp_priority: number | null;
  due_date: string | null;
  factors: FactorScore[];
  /** Adjustments the captured engine run produced (ERP, customer rule, aging, starvation, fairness cap). */
  capturedAdjustments: PriorityAdjustment[];
  readiness: ReadinessState;
  blocked: boolean;
  blocking_reasons: string[];
  risk_level: RiskLevel;
  hours_until_due: number | null;
  projected_completion: string | null;
  projected_lateness_hours: number | null;
  computed_at: string;
  /** Active planner hold (the ERP hold flag is already part of the captured readiness). */
  hold: LiveHold | null;
  /** Strongest active expedite. */
  expedite: ExpediteResponse | null;
  /** Active priority overrides (increase/decrease/set/force). */
  overrides: OverrideResponse[];
  rule: CustomerRule | null;
}

export interface ScoredResult {
  order_id: string;
  customer_id: string;
  due_date: string | null;
  score: number;
  base_score: number;
  factors: FactorScore[];
  adjustments: PriorityAdjustment[];
  readiness: ReadinessState;
  blocked: boolean;
  blocking_reasons: string[];
  risk_level: RiskLevel;
  forced_next: boolean;
  rank: number | null;
  hours_until_due: number | null;
  projected_completion: string | null;
  projected_lateness_hours: number | null;
  profile_id: string;
  profile_version: number;
  computed_at: string;
  explanation: string;
}

function clamp(value: number, low = SCORE_MIN, high = SCORE_MAX): number {
  return Math.max(low, Math.min(high, value));
}

/** Weights normalised so the enabled, positive weights sum to 1.0 (PriorityProfile.weight_map). */
export function weightMap(profile: PriorityProfile): Record<string, number> {
  const active = profile.weights.filter((w) => w.enabled && w.weight > 0);
  const total = active.reduce((s, w) => s + w.weight, 0);
  const out: Record<string, number> = {};
  if (!total) return out;
  for (const w of active) out[w.key] = w.weight / total;
  return out;
}

function factorPoints(kind: FactorScore["kind"], weight: number, raw: number): number {
  const points = weight * raw;
  return kind === "penalty" ? -points : points;
}

function erpPriorityAdjustment(erp: number | null, profile: PriorityProfile): PriorityAdjustment | null {
  if (erp === null || erp === undefined) return null;
  const points = profile.erp_priority_points[String(erp)];
  if (points === undefined || points === null || points === 0) return null;
  return { kind: "erp_priority", points, reason: `ERP priority ${erp}`, source_id: null };
}

function customerRuleAdjustment(customerId: string, rule: CustomerRule | null): PriorityAdjustment | null {
  if (!rule || !rule.active || rule.priority_boost_points === 0) return null;
  return {
    kind: "customer_rule",
    points: rule.priority_boost_points,
    reason: `Customer rule for ${customerId}${rule.notes ? `: ${rule.notes}` : ""}`,
    source_id: customerId,
  };
}

function expediteAdjustment(expedite: ExpediteResponse | null, profile: PriorityProfile, now: Date): PriorityAdjustment | null {
  if (!expedite) return null;
  const points = Math.min(Math.max(0, expedite.boost_points), profile.expedite.max_boost_points);
  const remaining = (new Date(expedite.expires_at).getTime() - now.getTime()) / 3_600_000;
  let reason = `Expedited by ${expedite.created_by}: ${expedite.reason} (expires in ${describeHours(remaining)})`;
  if (expedite.boost_points > points) reason += `, capped at ${fmtG(points)}`;
  return { kind: "expedite", points, reason, source_id: expedite.expedite_id };
}

/** Planner overrides in creation order; returns `[adjustments, forced_next]` (adjustments.override_adjustments). */
function overrideAdjustments(overrides: OverrideResponse[], runningScore: number): [PriorityAdjustment[], boolean] {
  const active = sortBy(overrides, (o) => [o.created_at, o.override_id]);
  const adjustments: PriorityAdjustment[] = [];
  let score = runningScore;
  let forced: OverrideResponse | null = null;
  for (const override of active) {
    const kind = override.override_type;
    const who = `by ${override.created_by}: ${override.reason}`;
    if (kind === "force_next") {
      forced = override;
      continue;
    }
    if (kind === "increase_priority" || kind === "decrease_priority") {
      if (override.value === null) continue;
      const delta = kind === "increase_priority" ? Math.abs(override.value) : -Math.abs(override.value);
      const verb = delta >= 0 ? "raised" : "lowered";
      adjustments.push({ kind: "override", points: delta, reason: `Priority ${verb} ${who}`, source_id: override.override_id });
      score += delta;
    } else if (kind === "set_priority") {
      if (override.value === null) continue;
      const delta = override.value - score;
      adjustments.push({ kind: "override", points: delta, reason: `Priority set to ${fmtG(override.value)} ${who}`, source_id: override.override_id });
      score += delta;
    }
  }
  if (forced) {
    adjustments.push({ kind: "override", points: FORCE_NEXT_SCORE - score, reason: `Forced next by ${forced.created_by}: ${forced.reason}`, source_id: forced.override_id });
  }
  return [adjustments, forced !== null];
}

/** Scores one order against `profile` (no rank: ranking needs the whole population). */
export function scoreOrder(input: ScoringInput, profile: PriorityProfile, now: Date): ScoredResult {
  const weights = weightMap(profile);
  const factors: FactorScore[] = input.factors.map((f) => {
    const weight = weights[f.key] ?? 0;
    let raw = clamp(f.raw_score);
    let reason = f.reason;
    if (input.hold && f.key === "production_readiness") {
      raw = clamp(profile.readiness.hold_score);
      reason = `On hold (held by ${input.hold.by})`;
    }
    return { ...f, raw_score: raw, weight, points: factorPoints(f.kind, weight, raw), reason };
  });
  const baseScore = factors.reduce((s, f) => s + f.points, 0);

  const adjustments: PriorityAdjustment[] = [];
  let score = baseScore;
  const engineAdjustments: Array<PriorityAdjustment | null> = [
    erpPriorityAdjustment(input.erp_priority, profile),
    customerRuleAdjustment(input.customer_id, input.rule),
    profile.aging.enabled ? (input.capturedAdjustments.find((a) => a.kind === "aging") ?? null) : null,
    (() => {
      const cfg = profile.fairness;
      if (!cfg.enabled || cfg.starvation_boost_points <= 0) return null;
      const starvation = input.capturedAdjustments.find((a) => a.kind === "fairness" && a.points > 0);
      return starvation ? { ...starvation, points: cfg.starvation_boost_points } : null;
    })(),
    expediteAdjustment(input.expedite, profile, now),
  ];
  for (const adjustment of engineAdjustments) {
    if (adjustment) {
      adjustments.push(adjustment);
      score += adjustment.points;
    }
  }
  const [overrides, forcedNext] = overrideAdjustments(input.overrides, score);
  adjustments.push(...overrides);
  score += overrides.reduce((s, a) => s + a.points, 0);
  score = clamp(score);

  let readiness = input.readiness;
  let blockingReasons = [...input.blocking_reasons];
  if (input.hold) {
    readiness = "on_hold";
    blockingReasons = [`held by ${input.hold.by}: ${input.hold.reason}`, ...blockingReasons.filter((r) => !r.startsWith("held by "))];
  }
  const blocked = readiness !== "ready";
  if (blocked && profile.blocked_order_cap !== null && profile.blocked_order_cap !== undefined && !forcedNext) {
    score = Math.min(score, profile.blocked_order_cap);
  }

  return {
    order_id: input.order_id,
    customer_id: input.customer_id,
    due_date: input.due_date,
    score,
    base_score: baseScore,
    factors,
    adjustments,
    readiness,
    blocked,
    blocking_reasons: blockingReasons,
    risk_level: input.risk_level,
    forced_next: forcedNext,
    rank: null,
    hours_until_due: input.hours_until_due,
    projected_completion: input.projected_completion,
    projected_lateness_hours: input.projected_lateness_hours,
    profile_id: profile.profile_id,
    profile_version: profile.version,
    computed_at: input.computed_at,
    explanation: "",
  };
}

/** Top-N slots one customer may hold (ranking.max_slots_per_customer). */
export function maxSlotsPerCustomer(fairness: FairnessConfig): number {
  return Math.max(1, Math.floor(fairness.top_n * fairness.max_top_n_share_per_customer));
}

/** Assigns `rank` in place: forced first, score desc, earliest due (none last), id; then the fairness pass. */
export function rankResults(results: ScoredResult[], fairness: FairnessConfig): string[] {
  let ordered = sortBy(results, (r) => [!r.forced_next, -r.score, r.due_date === null, r.due_date === null ? 0 : new Date(r.due_date).getTime(), r.order_id]);
  if (fairness.enabled && fairness.top_n > 0 && fairness.max_top_n_share_per_customer > 0 && fairness.max_top_n_share_per_customer < 1) {
    const limit = maxSlotsPerCustomer(fairness);
    const counts = new Map<string, number>();
    const kept: ScoredResult[] = [];
    const demoted: ScoredResult[] = [];
    const rest: ScoredResult[] = [];
    for (const result of ordered) {
      if (kept.length >= fairness.top_n) {
        rest.push(result);
        continue;
      }
      const count = counts.get(result.customer_id) ?? 0;
      if (result.forced_next || count < limit) {
        kept.push(result);
        counts.set(result.customer_id, count + 1);
      } else {
        demoted.push(result);
        result.adjustments.push({
          kind: "fairness",
          points: 0,
          reason: `Fairness cap: customer ${result.customer_id} already holds ${limit} of the top ${fairness.top_n} slots; ranked below them (score unchanged)`,
          source_id: result.customer_id,
        });
      }
    }
    ordered = [...kept, ...demoted, ...rest];
  }
  ordered.forEach((result, index) => {
    result.rank = index + 1;
  });
  return ordered.map((r) => r.order_id);
}

// ------------------------------------------------------------- explanation

/** Python-style fixed formatting: correctly rounded, exact ties to even (`f"{x:.1f}"`). */
export function pyFixed(value: number, digits: number): string {
  const factor = 10 ** digits;
  const scaled = value * factor;
  if (Number.isInteger(scaled * 2) && !Number.isInteger(scaled)) {
    const floor = Math.floor(scaled);
    const rounded = floor % 2 === 0 ? floor : floor + 1;
    return (rounded / factor).toFixed(digits);
  }
  return value.toFixed(digits);
}

/** `+28` / `-3` / `+27.4` / `0` (explanation.format_points). */
export function formatPoints(points: number): string {
  if (Math.abs(points) < 0.05) return "0";
  let text = pyFixed(points, 1);
  if (!text.startsWith("-")) text = `+${text}`;
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

/** Structured lines (factor / adjustment / cap) exactly as GET /orders/{id}/explanation renders them. */
export function explanationLines(result: ScoredResult): ExplanationLine[] {
  const lines: ExplanationLine[] = [];
  for (const factor of result.factors) {
    lines.push({
      kind: "factor",
      key: factor.key,
      label: factor.name,
      points: factor.points,
      reason: factor.weight > 0 ? factor.reason : `${factor.reason} (not weighted)`,
      raw_score: factor.raw_score,
      weight: factor.weight,
      source_id: null,
    });
  }
  for (const adj of result.adjustments) {
    lines.push({ kind: "adjustment", key: adj.kind, label: ADJUSTMENT_LABELS[adj.kind] ?? adj.kind, points: adj.points, reason: adj.reason, raw_score: null, weight: null, source_id: adj.source_id });
  }
  const rawTotal = result.base_score + result.adjustments.reduce((s, a) => s + a.points, 0);
  const clamped = clamp(rawTotal);
  if (Math.abs(clamped - rawTotal) > EPS) {
    lines.push({ kind: "cap", key: "clamp", label: "Cap", points: clamped - rawTotal, reason: `Score limited to ${SCORE_MIN}..${SCORE_MAX} (raw total ${pyFixed(rawTotal, 1)})`, raw_score: null, weight: null, source_id: null });
  }
  if (Math.abs(result.score - clamped) > EPS) {
    lines.push({ kind: "cap", key: "blocked_cap", label: "Cap", points: result.score - clamped, reason: "Blocked order: score capped by the priority profile", raw_score: null, weight: null, source_id: null });
  }
  return lines;
}

export function renderExplanation(result: ScoredResult): string {
  const out = [`ORDER #${result.order_id}`, `Priority: ${pyFixed(result.score, 0)}`, "Why?"];
  for (const line of explanationLines(result)) out.push(`${formatPoints(line.points)} — ${line.label}: ${line.reason}`);
  out.push(`Total: ${pyFixed(result.score, 0)}`);
  if (result.blocked) out.push(`Blocked: ${result.blocking_reasons.length ? result.blocking_reasons.join("; ") : result.readiness}`);
  if (result.forced_next) out.push("Forced next by planner override");
  return out.join("\n");
}
