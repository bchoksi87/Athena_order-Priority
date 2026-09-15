/**
 * DEMO MODE: the complete control tower running from a static bundle with an in-page backend
 * that answers every API call from real engine output captured once from the Python backend
 * (backend/scripts/capture_demo_data.py → src/demo/demo-data.json).
 *
 * Enabled at build time with VITE_DEMO_MODE=true (`npm run build:demo`, `.env.demo`).
 */
import type { Role } from "@/api/types";

import { DEMO_STATE_KEY } from "./store";
import { createDemoFetch, getDemoRuntime, setDemoRuntime } from "./transport";

export { isDemoMode } from "./mode";
export { createDemoFetch, getDemoRuntime, setDemoRuntime } from "./transport";

export interface DemoAccount {
  username: string;
  password: string;
  role: Role;
  display_name: string;
  blurb: string;
}

/** The six seeded accounts (backend/app/db/seed.py DEV_USERS) with what each one can do. */
export const DEMO_ACCOUNTS: readonly DemoAccount[] = [
  { username: "admin", password: "admin123", role: "admin", display_name: "System Administrator", blurb: "everything, including configuration versions and users" },
  { username: "manager", password: "manager123", role: "production_manager", display_name: "Production Manager", blurb: "approve / publish plans, expedite, override, move, lock" },
  { username: "planner", password: "planner123", role: "planner", display_name: "Production Planner", blurb: "generate drafts, what-if, hold / release, preview weights" },
  { username: "supervisor", password: "supervisor123", role: "supervisor", display_name: "Shift Supervisor", blurb: "boards and alerts (acknowledge)" },
  { username: "operator", password: "operator123", role: "operator", display_name: "Machine Operator", blurb: "read-only boards and the queue" },
  { username: "executive", password: "executive123", role: "executive", display_name: "Executive Viewer", blurb: "read-only dashboards" },
];

/** The "what to try" script shown on the login page. */
export const DEMO_TOUR: ReadonlyArray<{ title: string; text: string }> = [
  { title: "Produce next", text: "Control Tower: the ranked queue, blockers, bottleneck and capacity gap from the active plan." },
  { title: "Why?", text: "Press Why? on any order for the engine's factor-by-factor explanation of its score." },
  { title: "Expedite", text: "Sign in as manager, expedite an order: a +30 adjustment appears in its explanation and the queue re-ranks." },
  { title: "Change weights", text: "Priority Configuration: move Due Date Urgency to 40, Preview impact, then save (admin) — every order is re-scored." },
  { title: "What-if", text: "What-If Simulation: run one of the preset scenario kinds (machine down 8 h, urgent orders, extra Saturday…) to see baseline vs scenario." },
  { title: "Approve / reject", text: "Control Tower plan bar (manager): approve or reject the newest draft with an audited reason. Publishing an approved plan supersedes v1 and records the read-only writeback receipt (POST /schedule/publish; the plan bar only offers it when no plan is published yet)." },
];

/**
 * The app's `fetch` in demo mode. It is also exposed as `window.__ppseDemo` ({fetch, runtime, reset})
 * so the in-page backend can be driven from the browser console or an end-to-end test exactly like
 * the real API (`__ppseDemo.fetch("/api/v1/schedule", {headers: {Authorization: "Bearer demo.admin"}})`).
 */
export function installDemoFetch(): typeof fetch {
  const fetchImpl = createDemoFetch();
  if (typeof window !== "undefined") {
    (window as Window & { __ppseDemo?: unknown }).__ppseDemo = { fetch: fetchImpl, runtime: () => getDemoRuntime(), reset: resetDemoData };
  }
  return fetchImpl;
}

/** Drops every change made in this browser (overlays, audit rows, versions, users) and reloads. */
export async function resetDemoData(reload = true): Promise<void> {
  try {
    localStorage.removeItem(DEMO_STATE_KEY);
  } catch {
    // storage unavailable
  }
  try {
    const runtime = await getDemoRuntime();
    runtime.store.reset();
  } catch {
    setDemoRuntime(null);
  }
  if (reload && typeof window !== "undefined") window.location.reload();
}
