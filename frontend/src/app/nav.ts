/** Route table + navigation groups. Roles follow DESIGN_CONTRACT §9 (read access for executive). */
import type { Role } from "@/api/types";

export interface NavItem {
  path: string;
  label: string;
  /** Minimum role to open the screen (executive may read any read-only screen). */
  minRole: Role;
  /** Whether the screen is read-only (executive allowed) or a write screen. */
  readOnly: boolean;
  short: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const routes = {
  login: "/login",
  executive: "/dashboard",
  controlTower: "/control-tower",
  priorityQueue: "/priority-queue",
  machineSchedule: "/machines",
  gantt: "/gantt",
  orderDetail: (id = ":orderId") => `/orders/${id}`,
  machineDetail: (id = ":machineId") => `/machines/${id}`,
  bottlenecks: "/bottlenecks",
  capacity: "/capacity",
  simulation: "/simulation",
  priorityConfig: "/config/priority",
  schedulingConfig: "/config/scheduling",
  alerts: "/alerts",
  dataQuality: "/data-quality",
  audit: "/audit",
  admin: "/admin",
} as const;

export const navGroups: NavGroup[] = [
  {
    label: "Plan",
    items: [
      { path: routes.controlTower, label: "Control Tower", minRole: "operator", readOnly: true, short: "CT" },
      { path: routes.priorityQueue, label: "Priority Queue", minRole: "operator", readOnly: true, short: "PQ" },
      { path: routes.machineSchedule, label: "Machine Schedule", minRole: "operator", readOnly: true, short: "MS" },
      { path: routes.gantt, label: "Gantt Schedule", minRole: "operator", readOnly: true, short: "GS" },
    ],
  },
  {
    label: "Analyse",
    items: [
      { path: routes.executive, label: "Executive Dashboard", minRole: "executive", readOnly: true, short: "ED" },
      { path: routes.bottlenecks, label: "Bottleneck Analysis", minRole: "executive", readOnly: true, short: "BA" },
      { path: routes.capacity, label: "Capacity Planning", minRole: "executive", readOnly: true, short: "CP" },
      { path: routes.simulation, label: "What-If Simulation", minRole: "planner", readOnly: false, short: "WI" },
    ],
  },
  {
    label: "Configure",
    items: [
      { path: routes.priorityConfig, label: "Priority Configuration", minRole: "planner", readOnly: true, short: "PC" },
      { path: routes.schedulingConfig, label: "Scheduling Configuration", minRole: "planner", readOnly: true, short: "SC" },
    ],
  },
  {
    label: "System",
    items: [
      { path: routes.alerts, label: "Alerts", minRole: "supervisor", readOnly: true, short: "AL" },
      { path: routes.dataQuality, label: "Data Quality", minRole: "planner", readOnly: true, short: "DQ" },
      { path: routes.audit, label: "Audit Log", minRole: "production_manager", readOnly: true, short: "AU" },
      { path: routes.admin, label: "System Administration", minRole: "admin", readOnly: false, short: "SA" },
    ],
  },
];

/** Landing page after login, by role. */
export function homeForRole(role: Role): string {
  return role === "executive" ? routes.executive : routes.controlTower;
}
