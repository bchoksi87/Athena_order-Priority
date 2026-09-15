/**
 * In-memory state of the demo backend: the captured dataset (loaded lazily as its own
 * chunk), a persisted journal of everything the demo user changed (overlays, audit rows,
 * configuration versions, plan status, acknowledgements, users, runs) and the helpers every
 * handler shares (clock, ids, users, audit).
 *
 * The dataset is anchored to the day it was captured; at load time every date in it is
 * shifted forward by a whole number of weeks so "today" always falls inside the plan
 * horizon (weekday alignment keeps the plant calendar's weekend gaps where they belong).
 */
import type { AuditEntry, ConfigVersionInfo, CustomerRule, ExpediteResponse, LockResponse, OverrideResponse, PriorityOverride, Role, ScheduleLock, ScheduleStatus, SyncRunResponse, SystemConfig, UserAccount, UserInfo, WritebackReceipt } from "@/api/types";

import { loadDemoDataset } from "./demoData";
import type { DemoCredential, DemoData } from "./types";
import { clone, iso, newId } from "./util";

export const DEMO_STATE_KEY = "ppse.demo.state";
export const DEMO_STATE_VERSION = 1;

// ------------------------------------------------------------------ state

/** Live status of a schedule version (captured versions start with their captured status). */
export interface VersionPatch {
  status?: ScheduleStatus;
  approved_by?: string | null;
  approved_at?: string | null;
  published_by?: string | null;
  published_at?: string | null;
  superseded_at?: string | null;
  details?: Record<string, unknown>;
  writeback_receipt?: WritebackReceipt | null;
}

/** A schedule version created in the demo by cloning a captured one (generate / replan). */
export interface ClonedVersion {
  version_number: number;
  template: number;
  schedule_version_id: string;
  run_id: string;
  input_snapshot_id: string;
  generated_at: string;
  generated_by: string;
  label: string | null;
  notes: string | null;
  trigger: string;
  previous_version: number | null;
  status: ScheduleStatus;
}

export interface LiveUser extends UserAccount {
  password: string;
}

export interface DemoState {
  version: number;
  dataset: string;
  overrides: PriorityOverride[];
  expedites: ExpediteResponse[];
  locks: ScheduleLock[];
  audit: AuditEntry[];
  configVersions: Array<{ info: ConfigVersionInfo; config: SystemConfig }>;
  activeConfigVersion: number | null;
  versionPatches: Record<string, VersionPatch>;
  clonedVersions: ClonedVersion[];
  acknowledged: Record<string, { by: string; at: string }>;
  customerRules: Record<string, CustomerRule | null>;
  users: LiveUser[];
  userPatches: Record<string, { active?: boolean; last_login_at?: string | null; password?: string }>;
  syncRuns: SyncRunResponse[];
  dqRuns: Array<{ run_id: string; detected_at: string; by: string }>;
  generateCounter: number;
  replanCounter: number;
  /** Order ids whose score was recomputed after a planner action (computed_at = that moment). */
  scoredAt: Record<string, string>;
  profileScoredAt: string | null;
}

export function emptyState(dataset: string): DemoState {
  return {
    version: DEMO_STATE_VERSION,
    dataset,
    overrides: [],
    expedites: [],
    locks: [],
    audit: [],
    configVersions: [],
    activeConfigVersion: null,
    versionPatches: {},
    clonedVersions: [],
    acknowledged: {},
    customerRules: {},
    users: [],
    userPatches: {},
    syncRuns: [],
    dqRuns: [],
    generateCounter: 0,
    replanCounter: 0,
    scoredAt: {},
    profileScoredAt: null,
  };
}

// ---------------------------------------------------------------- shifting

const ISO_DATE_RE = /\d{4}-\d{2}-\d{2}/g;

function shiftDateText(text: string, days: number): string {
  return text.replace(ISO_DATE_RE, (match) => {
    const year = Number(match.slice(0, 4));
    const month = Number(match.slice(5, 7));
    const day = Number(match.slice(8, 10));
    if (month < 1 || month > 12 || day < 1 || day > 31) return match;
    const shifted = new Date(Date.UTC(year, month - 1, day + days));
    return shifted.toISOString().slice(0, 10);
  });
}

/** Shifts every ISO date (inside any string or object key) by `days`; ids never contain dates. */
export function shiftDates<T>(value: T, days: number): T {
  if (days === 0) return value;
  const walk = (node: unknown): unknown => {
    if (typeof node === "string") return node.length >= 10 && /\d{4}-\d{2}-\d{2}/.test(node) ? shiftDateText(node, days) : node;
    if (Array.isArray(node)) return node.map(walk);
    if (node && typeof node === "object") {
      const out: Record<string, unknown> = {};
      for (const [key, item] of Object.entries(node)) out[walk(key) as string] = walk(item);
      return out;
    }
    return node;
  };
  return walk(value) as T;
}

/** Whole weeks between the capture day and today (UTC), floored, so today lies in the plan's first week. */
export function computeShiftDays(capturedAt: string, now: Date): number {
  const captured = new Date(capturedAt);
  const captureDay = Date.UTC(captured.getUTCFullYear(), captured.getUTCMonth(), captured.getUTCDate());
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const diffDays = Math.round((today - captureDay) / 86_400_000);
  return 7 * Math.floor(diffDays / 7);
}

// ------------------------------------------------------------------ store

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface DemoStoreOptions {
  storage?: StorageLike | null;
  now?: () => Date;
  shiftDays?: number;
}

function browserStorage(): StorageLike | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export class DemoStore {
  readonly data: DemoData;
  readonly shiftDays: number;
  /** Identity of the capture (unshifted `meta.captured_at`); a new capture invalidates saved journals. */
  readonly datasetId: string;
  state: DemoState;
  requestCount = 0;
  /** Bumped on every commit/reset so cached read models (scores, versions) know they are stale. */
  revision = 0;
  private readonly storage: StorageLike | null;
  private readonly clock: () => Date;
  private listeners = new Set<() => void>();

  constructor(rawData: DemoData, options: DemoStoreOptions = {}) {
    this.clock = options.now ?? (() => new Date());
    this.storage = options.storage === undefined ? browserStorage() : options.storage;
    this.datasetId = rawData.meta.captured_at;
    this.shiftDays = options.shiftDays ?? computeShiftDays(rawData.meta.captured_at, this.clock());
    this.data = shiftDates(rawData, this.shiftDays);
    this.state = this.readState() ?? emptyState(this.datasetId);
  }

  /** Loads the dataset chunk (`demo-data.json` becomes its own JS chunk, never a runtime JSON fetch). */
  static async load(options: DemoStoreOptions = {}): Promise<DemoStore> {
    const data = await loadDemoDataset();
    return new DemoStore(data, options);
  }

  now(): Date {
    return this.clock();
  }

  nowIso(): string {
    return iso(this.now());
  }

  id(prefix: string): string {
    return newId(prefix, this.now());
  }

  // ------------------------------------------------------------ persistence

  private readState(): DemoState | null {
    if (!this.storage) return null;
    try {
      const raw = this.storage.getItem(DEMO_STATE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<DemoState>;
      if (parsed.version !== DEMO_STATE_VERSION || parsed.dataset !== this.datasetId) return null;
      return { ...emptyState(this.datasetId), ...parsed } as DemoState;
    } catch {
      return null;
    }
  }

  /** Persists the journal (best effort) and tells subscribers that derived read models are stale. */
  commit(): void {
    if (this.storage) {
      try {
        this.storage.setItem(DEMO_STATE_KEY, JSON.stringify(this.state));
      } catch {
        // quota exceeded / private mode: the in-memory state still works for this session
      }
    }
    this.revision += 1;
    for (const listener of this.listeners) listener();
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Drops every demo mutation (the "Reset demo data" action). */
  reset(): void {
    this.state = emptyState(this.datasetId);
    if (this.storage) {
      try {
        this.storage.removeItem(DEMO_STATE_KEY);
      } catch {
        // ignore
      }
    }
    this.revision += 1;
    for (const listener of this.listeners) listener();
  }

  hasMutations(): boolean {
    const s = this.state;
    return (
      s.overrides.length > 0 ||
      s.expedites.length > 0 ||
      s.locks.length > 0 ||
      s.audit.length > 0 ||
      s.configVersions.length > 0 ||
      s.clonedVersions.length > 0 ||
      Object.keys(s.versionPatches).length > 0 ||
      Object.keys(s.acknowledged).length > 0 ||
      Object.keys(s.customerRules).length > 0 ||
      s.users.length > 0 ||
      Object.keys(s.userPatches).length > 0 ||
      s.syncRuns.length > 0 ||
      s.dqRuns.length > 0
    );
  }

  // ------------------------------------------------------------------ users

  get credentials(): DemoCredential[] {
    return this.data.credentials;
  }

  /** Every account: the seeded ones (with live activation patches) plus users created in the demo. */
  allUsers(): UserAccount[] {
    const patched = (u: UserAccount): UserAccount => {
      const patch = this.state.userPatches[u.user_id];
      return patch ? { ...u, ...(patch.active !== undefined ? { active: patch.active } : {}), ...(patch.last_login_at !== undefined ? { last_login_at: patch.last_login_at } : {}) } : u;
    };
    const seeded = this.data.users.map(patched);
    const live = this.state.users.map(({ password: _password, ...rest }) => patched(rest));
    return [...seeded, ...live].sort((a, b) => (a.username < b.username ? -1 : a.username > b.username ? 1 : 0));
  }

  userByUsername(username: string): UserAccount | undefined {
    const wanted = username.trim().toLowerCase();
    return this.allUsers().find((u) => u.username.toLowerCase() === wanted);
  }

  userById(userId: string): UserAccount | undefined {
    return this.allUsers().find((u) => u.user_id === userId);
  }

  /** Credentials check for POST /auth/login (seeded passwords from the capture, live users from the journal). */
  authenticate(username: string, password: string): UserAccount | null {
    const user = this.userByUsername(username);
    if (!user || !user.active) return null;
    const seeded = this.credentials.find((c) => c.username.toLowerCase() === user.username.toLowerCase());
    const live = this.state.users.find((u) => u.user_id === user.user_id);
    const expected = this.state.userPatches[user.user_id]?.password ?? live?.password ?? seeded?.password;
    if (expected === undefined || expected !== password) return null;
    return user;
  }

  toUserInfo(user: UserAccount): UserInfo {
    return { user_id: user.user_id, username: user.username, role: user.role, display_name: user.display_name };
  }

  /** `demo.<role>` (seeded account of that role) or `demo.<role>.<username>`. */
  tokenFor(user: UserAccount): string {
    const seeded = this.data.users.find((u) => u.user_id === user.user_id);
    return seeded ? `demo.${user.role}` : `demo.${user.role}.${user.username}`;
  }

  userForToken(token: string): UserInfo | null {
    const parts = token.split(".");
    if (parts[0] !== "demo" || parts.length < 2) return null;
    const role = parts[1] as Role;
    if (parts.length >= 3) {
      const user = this.userByUsername(parts.slice(2).join("."));
      return user ? this.toUserInfo(user) : null;
    }
    const seeded = this.data.users.find((u) => u.role === role);
    if (seeded) return this.toUserInfo(seeded);
    const captured = this.data.auth.me[role];
    return captured ? { ...captured } : null;
  }

  // ------------------------------------------------------------------ audit

  /** Appends one audit row the way AuditService.record does (JSON previous/new, reason, details). */
  audit(user: UserInfo | string, entityType: string, entityId: string, action: string, previous: unknown, next: unknown, reason: string | null, details: Record<string, unknown> = {}): AuditEntry {
    const entry: AuditEntry = {
      audit_id: this.id("aud"),
      user_id: typeof user === "string" ? user : user.user_id,
      timestamp: this.nowIso(),
      entity_type: entityType,
      entity_id: entityId,
      action,
      previous_value: previous === null || previous === undefined ? null : (clone(previous) as AuditEntry["previous_value"]),
      new_value: next === null || next === undefined ? null : (clone(next) as AuditEntry["new_value"]),
      reason,
      request_id: null,
      details: clone(details),
    };
    this.state.audit.push(entry);
    return entry;
  }

  /** Captured rows plus the live ones, newest first (timestamp desc, audit_id desc). */
  allAudit(): AuditEntry[] {
    const rows = [...this.state.audit, ...this.data.audit];
    rows.sort((a, b) => (a.timestamp === b.timestamp ? (a.audit_id < b.audit_id ? 1 : -1) : a.timestamp < b.timestamp ? 1 : -1));
    return rows;
  }

  // ---------------------------------------------------------------- overlays

  private isActiveAt(item: { active: boolean; expires_at?: string | null; starts_at?: string }, now: Date): boolean {
    if (!item.active) return false;
    if (item.starts_at && new Date(item.starts_at) > now) return false;
    if (item.expires_at && new Date(item.expires_at) <= now) return false;
    return true;
  }

  activeOverrides(now: Date = this.now()): OverrideResponse[] {
    return [...this.data.overrides, ...this.state.overrides].filter((o) => this.isActiveAt(o, now));
  }

  overridesForOrder(orderId: string, activeOnly = true): OverrideResponse[] {
    const now = this.now();
    return [...this.data.overrides, ...this.state.overrides].filter((o) => o.order_id === orderId && (!activeOnly || this.isActiveAt(o, now)));
  }

  activeExpedites(now: Date = this.now()): ExpediteResponse[] {
    return [...this.data.expedites, ...this.state.expedites].filter((e) => this.isActiveAt(e, now));
  }

  expeditesForOrder(orderId: string, activeOnly = true): ExpediteResponse[] {
    const now = this.now();
    return [...this.data.expedites, ...this.state.expedites].filter((e) => e.order_id === orderId && (!activeOnly || this.isActiveAt(e, now)));
  }

  private lockActiveAt(lock: LockResponse, now: Date): boolean {
    if (!lock.active) return false;
    if (lock.window && new Date(lock.window.end) <= now) return false;
    return true;
  }

  activeLocks(now: Date = this.now()): LockResponse[] {
    return [...this.data.schedule.locks, ...this.state.locks].filter((l) => this.lockActiveAt(l, now));
  }

  allLocks(): LockResponse[] {
    return [...this.data.schedule.locks, ...this.state.locks];
  }

  locksForOrder(orderId: string, activeOnly = true): LockResponse[] {
    const now = this.now();
    return this.allLocks().filter((l) => (l.order_id === orderId || l.sequence_order_ids.includes(orderId)) && (!activeOnly || this.lockActiveAt(l, now)));
  }

  findOverride(overrideId: string): OverrideResponse | undefined {
    return [...this.data.overrides, ...this.state.overrides].find((o) => o.override_id === overrideId);
  }

  findExpedite(expediteId: string): ExpediteResponse | undefined {
    return [...this.data.expedites, ...this.state.expedites].find((e) => e.expedite_id === expediteId);
  }

  findLock(lockId: string): LockResponse | undefined {
    return this.allLocks().find((l) => l.lock_id === lockId);
  }

  /** Captured overlays are frozen JSON; a mutation clones them into the journal first. */
  mutableOverride(overrideId: string): OverrideResponse {
    let live = this.state.overrides.find((o) => o.override_id === overrideId);
    if (!live) {
      const captured = this.data.overrides.find((o) => o.override_id === overrideId);
      if (!captured) throw new Error(`unknown override ${overrideId}`);
      live = clone(captured);
      this.state.overrides.push(live);
      this.data.overrides = this.data.overrides.filter((o) => o.override_id !== overrideId);
    }
    return live;
  }

  mutableExpedite(expediteId: string): ExpediteResponse {
    let live = this.state.expedites.find((e) => e.expedite_id === expediteId);
    if (!live) {
      const captured = this.data.expedites.find((e) => e.expedite_id === expediteId);
      if (!captured) throw new Error(`unknown expedite ${expediteId}`);
      live = clone(captured);
      this.state.expedites.push(live);
      this.data.expedites = this.data.expedites.filter((e) => e.expedite_id !== expediteId);
    }
    return live;
  }

  mutableLock(lockId: string): LockResponse {
    let live = this.state.locks.find((l) => l.lock_id === lockId);
    if (!live) {
      const captured = this.data.schedule.locks.find((l) => l.lock_id === lockId);
      if (!captured) throw new Error(`unknown lock ${lockId}`);
      live = clone(captured);
      this.state.locks.push(live);
      this.data.schedule.locks = this.data.schedule.locks.filter((l) => l.lock_id !== lockId);
    }
    return live;
  }

  // ------------------------------------------------------------ customer rules

  customerRule(customerId: string): CustomerRule | null {
    if (customerId in this.state.customerRules) return this.state.customerRules[customerId] ?? null;
    const captured = this.data.customers.rules[customerId];
    if (!captured || "_error" in captured) return null;
    return captured;
  }
}
