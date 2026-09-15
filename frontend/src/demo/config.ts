/**
 * Versioned configuration of the demo backend (ConfigService port): the active
 * SystemConfig, PUT of one section as a new active version with an audited key diff,
 * activate/rollback, the profile preview and customer rules.
 */
import type {
  ConfigUpdateResponse,
  ConfigVersionInfo,
  CustomerResponse,
  CustomerRule,
  PreviewResponse,
  PriorityConfigurationResponse,
  PriorityProfile,
  SchedulingConfig,
  SchedulingConfigurationResponse,
  SystemConfig,
  SystemConfigVersionResponse,
  UserInfo,
} from "@/api/types";
import { FACTOR_NAMES } from "@/lib/constants";

import { conflict, notFound, validation } from "./errors";
import type { OrderModel } from "./orders";
import { rankResults, scoreOrder, weightMap, type ScoredResult } from "./scoring";
import type { DemoStore } from "./store";
import { isCapturedError } from "./types";
import { clone, deepEqual, fmtG, jsonDiff, sortBy } from "./util";

export type ConfigSection = "priority_profile" | "scheduling" | "replanning" | "alerts" | "data_quality";
const ENTITY_CONFIG = "system_config";
const ENTITY_CUSTOMER_RULE = "customer_rule";
const MAX_BOOST_POINTS = 100;
const MAX_SLA_HOURS = 24 * 365;
export const MAX_PREVIEW_TOP_N = 500;

export interface ConfigVersion {
  info: ConfigVersionInfo;
  config: SystemConfig;
}

export class ActiveConfig {
  constructor(private readonly store: DemoStore) {}

  /** Every version (captured + live), newest first, with the live `is_active` flag. */
  versions(): ConfigVersion[] {
    const active = this.activeVersionNumber();
    const captured = Object.values(this.store.data.configuration.priority_version_detail).map((v) => ({ info: v.version, config: v.config }));
    const all = [...captured, ...this.store.state.configVersions].map((v) => ({ info: { ...v.info, is_active: v.info.version === active }, config: v.config }));
    return sortBy(all, (v) => [v.info.version], true);
  }

  activeVersionNumber(): number {
    if (this.store.state.activeConfigVersion !== null) return this.store.state.activeConfigVersion;
    const captured = this.store.data.configuration.priority.version;
    return captured ? captured.version : 1;
  }

  active(): ConfigVersion {
    const wanted = this.activeVersionNumber();
    const found = this.versions().find((v) => v.info.version === wanted);
    if (found) return found;
    // No captured version detail: fall back to the captured configuration endpoints.
    const cfg = this.store.data.configuration;
    return {
      info: cfg.priority.version ?? { config_id: "cfg_demo", version: 1, is_active: true, created_by: null, reason: null, created_at: null, profile_id: cfg.priority.profile.profile_id, scheduling_config_id: cfg.scheduling.scheduling.config_id },
      config: { priority_profile: cfg.priority.profile, scheduling: cfg.scheduling.scheduling, replanning: cfg.scheduling.replanning, alerts: cfg.scheduling.alerts, data_quality: cfg.scheduling.data_quality, currency: "INR" },
    };
  }

  system(): SystemConfig {
    return this.active().config;
  }

  profile(): PriorityProfile {
    return this.system().priority_profile;
  }

  scheduling(): SchedulingConfig {
    return this.system().scheduling;
  }

  versionDetail(version: number): SystemConfigVersionResponse {
    const found = this.versions().find((v) => v.info.version === version);
    if (!found) throw notFound(`configuration version ${version} not found`, { version });
    return { version: found.info, config: found.config };
  }

  versionInfos(limit = 100): ConfigVersionInfo[] {
    return this.versions()
      .slice(0, limit)
      .map((v) => v.info);
  }

  priorityConfiguration(): PriorityConfigurationResponse {
    const profile = this.profile();
    const weights = weightMap(profile);
    return {
      version: this.active().info,
      profile,
      weights_pct: Object.fromEntries(Object.entries(weights).map(([k, v]) => [k, Math.round(v * 100 * 10_000) / 10_000])),
    };
  }

  schedulingConfiguration(): SchedulingConfigurationResponse {
    const system = this.system();
    return { version: this.active().info, scheduling: system.scheduling, replanning: system.replanning, alerts: system.alerts, data_quality: system.data_quality };
  }

  // ---------------------------------------------------------------- updates

  private validateProfile(profile: PriorityProfile): void {
    const keys = profile.weights.map((w) => w.key);
    if (new Set(keys).size !== keys.length) throw validation("configuration is invalid", { errors: "duplicate factor keys in profile weights" });
    if (!profile.weights.some((w) => w.enabled && w.weight > 0)) throw validation("configuration is invalid", { errors: "at least one enabled factor must have a positive weight" });
    for (const w of profile.weights) {
      if (typeof w.weight !== "number" || w.weight < 0 || w.weight > 100) throw validation("configuration is invalid", { errors: `weight of ${w.key} must be in 0..100` });
    }
  }

  private nextVersionNumber(): number {
    return Math.max(0, ...this.versions().map((v) => v.info.version)) + 1;
  }

  /** Stores one changed section as a new active version (ConfigService._save_section). */
  updateSection(section: ConfigSection, value: unknown, user: UserInfo, reason: string, extraDetails: Record<string, unknown> = {}): ConfigUpdateResponse {
    const previous = this.active();
    const candidate = clone(previous.config) as unknown as Record<string, unknown>;
    candidate[section] = clone(value);
    const candidateConfig = candidate as unknown as SystemConfig;
    if (section === "priority_profile") this.validateProfile(candidateConfig.priority_profile);
    const strip = (v: unknown) => {
      const copy = clone(v) as Record<string, unknown>;
      delete copy.version;
      return copy;
    };
    if (deepEqual(strip((previous.config as unknown as Record<string, unknown>)[section]), strip(candidate[section]))) {
      throw conflict(`${section} is unchanged; nothing to save`, { section });
    }
    const version = this.nextVersionNumber();
    candidateConfig.priority_profile = { ...candidateConfig.priority_profile, version };
    candidateConfig.scheduling = { ...candidateConfig.scheduling, version };
    const info: ConfigVersionInfo = {
      config_id: this.store.id("cfg"),
      version,
      is_active: true,
      created_by: user.user_id,
      reason,
      created_at: this.store.nowIso(),
      profile_id: candidateConfig.priority_profile.profile_id,
      scheduling_config_id: candidateConfig.scheduling.config_id,
    };
    this.store.state.configVersions.push({ info, config: candidateConfig });
    this.store.state.activeConfigVersion = version;
    const [changedPrevious, changedNew] = jsonDiff(previous.config, candidateConfig);
    this.store.audit(
      user,
      ENTITY_CONFIG,
      info.config_id,
      `config.update_${section}`,
      { version: previous.info.version, changes: changedPrevious },
      { version, changes: changedNew },
      reason,
      { section, previous_version: previous.info.version, version, changed_keys: Object.keys(changedNew).sort(), ...extraDetails },
    );
    if (section === "priority_profile") this.store.state.profileScoredAt = this.store.nowIso();
    this.store.commit();
    return { version: info, previous_version: previous.info.version, changed_previous: changedPrevious, changed_new: changedNew, config: candidateConfig };
  }

  activateVersion(version: number, user: UserInfo, reason: string): ConfigUpdateResponse {
    const previous = this.active();
    if (previous.info.version === version) throw conflict(`configuration version ${version} is already active`);
    const target = this.versions().find((v) => v.info.version === version);
    if (!target) throw notFound(`configuration version ${version} not found`, { version });
    this.store.state.activeConfigVersion = version;
    const [changedPrevious, changedNew] = jsonDiff(previous.config, target.config);
    this.store.audit(
      user,
      ENTITY_CONFIG,
      target.info.config_id,
      "config.activate_version",
      { version: previous.info.version, changes: changedPrevious },
      { version, changes: changedNew },
      reason,
      { previous_version: previous.info.version, version },
    );
    this.store.state.profileScoredAt = this.store.nowIso();
    this.store.commit();
    return { version: { ...target.info, is_active: true }, previous_version: previous.info.version, changed_previous: changedPrevious, changed_new: changedNew, config: target.config };
  }

  // ---------------------------------------------------------------- preview

  /** "Changing Due Date Urgency weight from 25% to 40% would move N order(s) into the top 50 and M out". */
  preview(candidate: PriorityProfile, topN: number, orders: OrderModel): PreviewResponse {
    if (topN < 1 || topN > MAX_PREVIEW_TOP_N) throw validation(`top_n must be in 1..${MAX_PREVIEW_TOP_N}`, { top_n: topN });
    this.validateProfile(candidate);
    const active = this.profile();
    const now = this.store.now();
    const resultsA = orders.results();
    const resultsB = new Map<string, ScoredResult>();
    const scoredB: ScoredResult[] = [];
    for (const id of resultsA.keys()) {
      const input = orders.scoringInputFor(id, now);
      if (!input) continue;
      const result = scoreOrder(input, candidate, now);
      scoredB.push(result);
      resultsB.set(id, result);
    }
    rankResults(scoredB, candidate.fairness);
    const top = (results: Map<string, ScoredResult>) =>
      Array.from(results.values())
        .filter((r) => r.rank !== null)
        .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
        .slice(0, topN)
        .map((r) => r.order_id);
    const topA = top(resultsA);
    const topB = top(resultsB);
    const setA = new Set(topA);
    const setB = new Set(topB);
    const entered = topB.filter((id) => !setA.has(id));
    const left = topA.filter((id) => !setB.has(id));
    const rankDeltas = new Map<string, number>();
    const scoreDeltas = new Map<string, number>();
    for (const id of Array.from(resultsA.keys()).sort()) {
      const b = resultsB.get(id);
      const a = resultsA.get(id);
      if (!a || !b) continue;
      rankDeltas.set(id, (b.rank ?? 0) - (a.rank ?? 0));
      scoreDeltas.set(id, b.score - a.score);
    }
    const absDeltas = Array.from(scoreDeltas.values()).map((d) => Math.abs(d));
    const rawWeights = (p: PriorityProfile) => Object.fromEntries(p.weights.map((w) => [w.key, w.enabled ? w.weight : 0]));
    const wa = rawWeights(active);
    const wb = rawWeights(candidate);
    const changes = Array.from(new Set([...Object.keys(wa), ...Object.keys(wb)]))
      .sort()
      .filter((k) => (wa[k] ?? 0) !== (wb[k] ?? 0))
      .map((k) => ({ key: k, previous_pct: wa[k] ?? 0, new_pct: wb[k] ?? 0 }));
    let what: string;
    if (changes.length > 0) {
      const parts = changes.slice(0, 3).map((c) => `${FACTOR_NAMES[c.key] ?? c.key} weight from ${fmtG(c.previous_pct)}% to ${fmtG(c.new_pct)}%`);
      if (changes.length > 3) parts.push(`${changes.length - 3} more weight(s)`);
      what = `Changing ${parts.join(" and ")}`;
    } else {
      what = `Switching from profile '${active.profile_id}' to '${candidate.profile_id}'`;
    }
    const moves = Array.from(rankDeltas.entries())
      .filter(([, delta]) => delta !== 0)
      .map(([order_id, rank_delta]) => ({ order_id, rank_delta, score_delta: scoreDeltas.get(order_id) ?? 0 }))
      .sort((x, y) => Math.abs(y.rank_delta) - Math.abs(x.rank_delta) || (x.order_id < y.order_id ? -1 : 1))
      .slice(0, 25);
    return {
      summary: `${what} would move ${entered.length} order(s) into the top ${topN} and ${left.length} out`,
      active_profile_id: active.profile_id,
      candidate_profile_id: candidate.profile_id,
      top_n: topN,
      orders_evaluated: rankDeltas.size,
      entered_top_n: entered,
      left_top_n: left,
      orders_changed_rank: Array.from(rankDeltas.values()).filter((d) => d !== 0).length,
      mean_abs_score_delta: absDeltas.length ? absDeltas.reduce((s, d) => s + d, 0) / absDeltas.length : 0,
      max_abs_score_delta: absDeltas.length ? Math.max(...absDeltas) : 0,
      weight_changes: changes,
      top_n_before: topA,
      top_n_after: topB,
      biggest_moves: moves,
    };
  }

  // --------------------------------------------------------- customer rules

  customerBase(customerId: string): CustomerResponse {
    const captured = this.store.data.customers.detail[customerId];
    if (!captured) throw notFound(`customer '${customerId}' not found`, { customer_id: customerId });
    return captured;
  }

  customerResponse(captured: CustomerResponse): CustomerResponse {
    const rule = this.store.customerRule(captured.customer_id);
    const activeRule = rule && rule.active ? rule : null;
    return {
      ...captured,
      effective_tier: activeRule?.tier_override ?? captured.customer_tier,
      effective_sla_hours: activeRule && activeRule.sla_hours !== null ? activeRule.sla_hours : captured.sla_hours,
      rule: rule ? { ...rule } : null,
    };
  }

  customerRule(customerId: string): CustomerRule {
    this.customerBase(customerId);
    const rule = this.store.customerRule(customerId);
    if (!rule) throw notFound(`customer '${customerId}' has no rule`, { customer_id: customerId });
    return rule;
  }

  upsertRule(customerId: string, user: UserInfo, reason: string, body: { sla_hours: number | null; tier_override: CustomerRule["tier_override"]; priority_boost_points: number; notes: string | null; active: boolean }): CustomerRule {
    this.customerBase(customerId);
    if (body.sla_hours !== null && (body.sla_hours <= 0 || body.sla_hours > MAX_SLA_HOURS)) throw validation(`sla_hours must be in (0, ${fmtG(MAX_SLA_HOURS)}]`, { sla_hours: body.sla_hours });
    if (Math.abs(body.priority_boost_points) > MAX_BOOST_POINTS) throw validation(`priority_boost_points must be within ±${MAX_BOOST_POINTS}`, { priority_boost_points: body.priority_boost_points });
    const previous = this.store.customerRule(customerId);
    const rule: CustomerRule = { customer_id: customerId, sla_hours: body.sla_hours, tier_override: body.tier_override, priority_boost_points: body.priority_boost_points, notes: body.notes, active: body.active };
    this.store.state.customerRules[customerId] = rule;
    this.store.audit(user, ENTITY_CUSTOMER_RULE, customerId, previous ? "customer_rule.update" : "customer_rule.create", previous, rule, reason, { customer_id: customerId });
    this.store.commit();
    return rule;
  }

  deleteRule(customerId: string, user: UserInfo, reason: string): CustomerRule {
    this.customerBase(customerId);
    const previous = this.store.customerRule(customerId);
    if (!previous) throw notFound(`customer '${customerId}' has no rule`, { customer_id: customerId });
    this.store.state.customerRules[customerId] = null;
    this.store.audit(user, ENTITY_CUSTOMER_RULE, customerId, "customer_rule.delete", previous, null, reason);
    this.store.commit();
    return previous;
  }

  hasCapturedRule(customerId: string): boolean {
    const captured = this.store.data.customers.rules[customerId];
    return Boolean(captured) && !isCapturedError(captured);
  }
}
