/**
 * The views the demo derives client-side (scores, ranks, explanations, breakdowns, gantt, day
 * schedules, machine schedules, capacity slices, list rows) compared with the real backend's
 * answers captured alongside the dataset (src/demo/.demo-verify.json, written by the capture
 * script and git-ignored). Skipped when that file is absent.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

import { loadDemoDataset } from "./demoData";
import { DemoStore } from "./store";
import { createDemoFetch, createRuntime } from "./transport";
import type { DemoData } from "./types";

const VERIFY_PATH = join(dirname(fileURLToPath(import.meta.url)), ".demo-verify.json");
const hasVerify = existsSync(VERIFY_PATH);

type Json = Record<string, unknown>;

interface Verify {
  captured_at: string;
  orders_list: Json[];
  orders_list_all: Json[];
  explanations: Record<string, Json>;
  priority_explanation: Record<string, string>;
  order_breakdown: Record<string, Json[]>;
  order_schedule: Record<string, Json | null>;
  order_overrides: Record<string, Json[]>;
  machine_schedule: Record<string, Json>;
  machine_upcoming: Record<string, Json[]>;
  gantt: Record<string, Json>;
  days: Record<string, Record<string, Json>>;
  version_entries: Record<string, Json[]>;
  plan_entries: Record<string, unknown>;
  capacity: Record<string, Json>;
  generate_versions: Record<string, Json>;
}

/** Fields whose value is the request instant (or a request id) rather than engine output. */
const VOLATILE = new Set(["evaluated_at", "generated_at", "as_of", "request_id"]);
const ISO_FRACTION = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

/** Rounds floats, trims ISO timestamps to milliseconds (JS precision) and drops request-time fields. */
function normalise(value: unknown, drop: ReadonlySet<string> = VOLATILE): unknown {
  if (Array.isArray(value)) return value.map((v) => normalise(v, drop));
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [k, v] of Object.entries(value as Json)) {
      if (drop.has(k)) continue;
      out[k] = normalise(v, drop);
    }
    return out;
  }
  if (typeof value === "number") return Math.round(value * 1e6) / 1e6;
  if (typeof value === "string") {
    const m = ISO_FRACTION.exec(value);
    if (m) {
      const ms = (m[2] ?? "").slice(1, 4).padEnd(3, "0");
      return `${m[1]}${ms === "000" ? "" : `.${ms}`}${m[3]}`;
    }
  }
  return value;
}

describe.skipIf(!hasVerify)("demo derivations vs the captured backend answers", () => {
  let dataset: DemoData;
  let verify: Verify;
  let get: <T = Json>(path: string) => Promise<T>;

  beforeAll(async () => {
    dataset = await loadDemoDataset();
    verify = JSON.parse(readFileSync(VERIFY_PATH, "utf8")) as Verify;
    const capturedAt = new Date(dataset.meta.captured_at).getTime();
    const store = new DemoStore(structuredClone(dataset), { storage: null, shiftDays: 0, now: () => new Date(capturedAt) });
    const fetchImpl = createDemoFetch({ runtime: createRuntime(store) });
    get = async <T,>(path: string) => {
      const res = await fetchImpl(`/api/v1${path}`, { headers: { Authorization: "Bearer demo.admin" } });
      const body = (await res.json()) as T;
      if (!res.ok) throw new Error(`${path} -> ${res.status} ${JSON.stringify(body)}`);
      return body;
    };
  });

  it("re-scores every open order to the captured score, rank and factor breakdown", async () => {
    // The capture listed with sort=rank (ties: due date, then order id).
    const page = await get<{ items: Json[]; total: number }>("/orders?sort=rank&page_size=500");
    expect(page.total).toBe(verify.orders_list.length);
    expect(normalise(page.items)).toEqual(normalise(verify.orders_list));
    const all = await get<{ items: Json[]; total: number }>("/orders?sort=rank&open_only=false&page_size=500");
    expect(normalise(all.items)).toEqual(normalise(verify.orders_list_all));
  });

  it("renders the same explanation lines and sentences as the engine", async () => {
    const ids = Object.keys(verify.explanations);
    expect(ids.length).toBeGreaterThan(0);
    let compared = 0;
    for (const id of ids) {
      const captured = verify.explanations[id] as Json & { _error?: { status: number } };
      if (captured._error) {
        const res = await get<{ error?: string }>(`/orders/${id}/explanation`).catch((e: Error) => ({ error: e.message }));
        expect(res.error).toContain("404");
        continue;
      }
      const live = await get(`/orders/${id}/explanation`);
      expect(normalise(live)).toEqual(normalise(captured));
      const detail = await get<{ breakdown: Json[]; priority: { explanation: string | null } | null; schedule: Json | null; overrides: Json[] }>(`/orders/${id}`);
      expect(normalise(detail.breakdown)).toEqual(normalise(verify.order_breakdown[id]));
      expect(detail.priority?.explanation ?? null).toBe(verify.priority_explanation[id] ?? null);
      expect(normalise(detail.schedule)).toEqual(normalise(verify.order_schedule[id] ?? null));
      compared += 1;
    }
    expect(compared).toBeGreaterThan(100);
  });

  it("rebuilds the gantt, the day schedules and the machine schedules from the version entries", async () => {
    // The capture asked for the whole horizon of each version (and of the active plan for machines).
    const horizonOf = (version: string) => {
      const detail = dataset.schedule.version_detail[version];
      if (!detail) throw new Error(`no version ${version}`);
      return `start=${encodeURIComponent(detail.horizon_start)}&end=${encodeURIComponent(detail.horizon_end)}`;
    };
    const activeHorizon = `start=${encodeURIComponent(dataset.meta.horizon_start)}&end=${encodeURIComponent(dataset.meta.horizon_end)}`;
    // The backend's gantt / day / machine views were served from the plan cache of the same run
    // (priority scores a few 1e-5 away from the stored entries, a preference cost off by one in a
    // handful of placement reasons); the demo derives the views from the stored version entries,
    // so scores are compared with a tolerance and placement reasons with their numbers masked.
    const VIEW_DROP = new Set([...VOLATILE, "priority_score", "placement_reason"]);
    const reasonsClose = (live: unknown, captured: unknown) => {
      const collect = (node: unknown, out: string[]): string[] => {
        if (Array.isArray(node)) node.forEach((n) => collect(n, out));
        else if (node && typeof node === "object") {
          for (const [k, v] of Object.entries(node as Json)) {
            if (k === "placement_reason" && typeof v === "string") out.push(v);
            else collect(v, out);
          }
        }
        return out;
      };
      const a = collect(live, []);
      const b = collect(captured, []);
      expect(a.length).toBe(b.length);
      const mask = (s: string) => s.replace(/\d+(\.\d+)?/g, "#");
      a.forEach((reason, i) => expect(mask(reason)).toBe(mask(b[i] ?? "")));
      const verbatim = a.filter((reason, i) => reason === b[i]).length;
      expect(verbatim / Math.max(1, a.length)).toBeGreaterThan(0.95);
    };
    const scoresClose = (live: unknown, captured: unknown) => {
      const collect = (node: unknown, out: number[]): number[] => {
        if (Array.isArray(node)) node.forEach((n) => collect(n, out));
        else if (node && typeof node === "object") {
          for (const [k, v] of Object.entries(node as Json)) {
            if (k === "priority_score" && typeof v === "number") out.push(v);
            else collect(v, out);
          }
        }
        return out;
      };
      const a = collect(live, []);
      const b = collect(captured, []);
      expect(a.length).toBe(b.length);
      a.forEach((score, i) => expect(Math.abs(score - (b[i] ?? NaN))).toBeLessThan(1e-3));
    };
    for (const [version, captured] of Object.entries(verify.gantt)) {
      const live = await get(`/schedule/gantt?version=${version}&${horizonOf(version)}`);
      expect(normalise(live, VIEW_DROP)).toEqual(normalise(captured, VIEW_DROP));
      scoresClose(live, captured);
      reasonsClose(live, captured);
    }
    for (const [version, days] of Object.entries(verify.days)) {
      for (const [date, captured] of Object.entries(days)) {
        const live = await get(`/schedule/${date}?version=${version}`);
        expect(normalise(live, VIEW_DROP)).toEqual(normalise(captured, VIEW_DROP));
        scoresClose(live, captured);
        reasonsClose(live, captured);
      reasonsClose(live, captured);
      }
    }
    for (const [machineId, captured] of Object.entries(verify.machine_schedule)) {
      const live = await get(`/machines/${machineId}/schedule?${activeHorizon}`);
      expect(normalise(live, VIEW_DROP)).toEqual(normalise(captured, VIEW_DROP));
      scoresClose(live, captured);
      reasonsClose(live, captured);
      const detail = await get<{ upcoming: Json[] }>(`/machines/${machineId}`);
      expect(normalise(detail.upcoming, VIEW_DROP)).toEqual(normalise(verify.machine_upcoming[machineId], VIEW_DROP));
    }
    for (const [version, captured] of Object.entries(verify.version_entries)) {
      const items: Json[] = [];
      for (let page = 1; page <= 10; page += 1) {
        const live = await get<{ items: Json[]; total: number; has_more: boolean }>(`/schedule/versions/${version}/entries?page=${page}&page_size=500`);
        expect(live.total).toBe(captured.length);
        items.push(...live.items);
        if (!live.has_more) break;
      }
      expect(normalise(items, VIEW_DROP)).toEqual(normalise(captured, VIEW_DROP));
      scoresClose(items, captured);
      reasonsClose(items, captured);
    }
  });

  it("slices the capacity rows per dimension, period and horizon like the analytics service", async () => {
    for (const [key, captured] of Object.entries(verify.capacity)) {
      const [dimension, period, horizon] = key.split("|") as [string, string, string];
      const query = horizon === "default" ? "" : `&horizon_days=${horizon}`;
      const live = await get<Json & { rows: Json[] }>(`/analytics/capacity?dimension=${dimension}&period=${period}${query}`);
      const { rows: liveRows, ...liveRest } = live;
      const { rows: capturedRows, ...capturedRest } = captured as Json & { rows: Json[] };
      expect(normalise(liveRest)).toEqual(normalise(capturedRest));
      expect(liveRows.length).toBe(capturedRows.length);
    }
  });
});
