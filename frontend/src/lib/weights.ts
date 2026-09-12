/** Live normalisation of the priority factor weights (spec Phase 14): enabled weights share 100%. */
import type { FactorWeight, PriorityProfile } from "@/api/types";

/** Normalised share (0–100) of each enabled weight; disabled or zero-sum weights get 0. */
export function normaliseWeights(weights: FactorWeight[]): Record<string, number> {
  const enabled = weights.filter((w) => w.enabled && w.weight > 0);
  const total = enabled.reduce((s, w) => s + w.weight, 0);
  const out: Record<string, number> = {};
  for (const w of weights) out[w.key] = w.enabled && total > 0 ? (100 * w.weight) / total : 0;
  return out;
}

/** Profile with the weights array re-keyed by factor, so structural diffs report per-factor changes. */
export function profileForDiff(profile: PriorityProfile): Record<string, unknown> {
  const weights: Record<string, { weight: number; enabled: boolean }> = {};
  for (const w of profile.weights) weights[w.key] = { weight: w.weight, enabled: w.enabled };
  return { ...profile, weights };
}
