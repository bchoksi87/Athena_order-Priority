/**
 * Catalogue of the audited planner/manager actions on one order (spec Phase 9/16) and the
 * role rules that decide who sees which action. Kept separate from the dialog component so
 * tables and menus can import it without pulling React into a non-component module.
 */
import type { OrderActionKind } from "@/api/orders";
import type { Role } from "@/api/types";

export type { OrderActionKind } from "@/api/orders";

export interface OrderActionSpec {
  kind: OrderActionKind;
  label: string;
  title: string;
  minRole: Role;
  danger?: boolean;
  primary?: boolean;
  description: string;
}

/** Order of the menu follows the spec list; roles follow DESIGN_CONTRACT §9 (planner may hold/release). */
export const ORDER_ACTIONS: OrderActionSpec[] = [
  { kind: "expedite", label: "Expedite", title: "Expedite order", minRole: "production_manager", primary: true, description: "Temporarily boosts the priority score. After the expiry the normal calculation resumes; other orders are not permanently overridden." },
  { kind: "force-next", label: "Force next", title: "Force order to run next", minRole: "production_manager", description: "The scheduler places this order first on its eligible machine (until the override expires or is cancelled)." },
  { kind: "override-priority", label: "Override priority", title: "Override priority score", minRole: "production_manager", description: "Increase or decrease the engine score by a number of points, or set it to an absolute value (0–100)." },
  { kind: "move", label: "Move to machine", title: "Move order to a machine", minRole: "production_manager", description: "Pins the next operation to a machine (optionally at a start time) with a lock, and records the move as an override." },
  { kind: "lock-machine", label: "Lock machine", title: "Lock machine assignment", minRole: "production_manager", description: "Keeps the order on the chosen machine across replans." },
  { kind: "hold", label: "Hold", title: "Put order on hold", minRole: "planner", danger: true, description: "Removes the order from the queue until released (or until the optional expiry)." },
  { kind: "release", label: "Release hold", title: "Release hold", minRole: "planner", description: "Returns the order to the queue; the priority engine scores it again on the next run." },
];

export function specFor(kind: OrderActionKind): OrderActionSpec {
  const spec = ORDER_ACTIONS.find((a) => a.kind === kind);
  if (!spec) throw new Error(`unknown order action ${kind}`);
  return spec;
}

/** Actions the current user may take on an order, given its hold state. */
export function visibleOrderActions(hasMinRole: (r: Role) => boolean, onHold: boolean): OrderActionSpec[] {
  return ORDER_ACTIONS.filter((a) => hasMinRole(a.minRole)).filter((a) => (a.kind === "release" ? onHold : a.kind === "hold" ? !onHold : true));
}

/** "yyyy-MM-ddTHH:mm" from <input type=datetime-local> → ISO UTC (null when blank/invalid). */
export function localInputToIso(value: string): string | null {
  if (!value.trim()) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
