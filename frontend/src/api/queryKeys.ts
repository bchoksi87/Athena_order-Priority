/** Centralised TanStack Query keys so invalidation stays consistent across modules. */
export const queryKeys = {
  auth: { me: ["auth", "me"] as const },
  orders: {
    all: ["orders"] as const,
    list: (params: unknown) => ["orders", "list", params] as const,
    detail: (id: string) => ["orders", "detail", id] as const,
    explanation: (id: string) => ["orders", "explanation", id] as const,
    machines: (id: string) => ["orders", "machines", id] as const,
  },
  machines: {
    all: ["machines"] as const,
    list: (params: unknown) => ["machines", "list", params] as const,
    detail: (id: string) => ["machines", "detail", id] as const,
    schedule: (id: string, params: unknown) => ["machines", "schedule", id, params] as const,
  },
  schedule: {
    all: ["schedule"] as const,
    current: (params: unknown) => ["schedule", "current", params] as const,
    byDate: (date: string) => ["schedule", "date", date] as const,
    versions: ["schedule", "versions"] as const,
    version: (v: number) => ["schedule", "version", v] as const,
  },
  priority: {
    all: ["priority"] as const,
    configuration: ["priority", "configuration"] as const,
    versions: ["priority", "configuration", "versions"] as const,
  },
  scheduling: {
    configuration: ["scheduling", "configuration"] as const,
  },
  customers: {
    rules: (id: string) => ["customers", "rules", id] as const,
  },
  analytics: {
    kpis: ["analytics", "kpis"] as const,
    capacity: (params: unknown) => ["analytics", "capacity", params] as const,
    bottlenecks: ["analytics", "bottlenecks"] as const,
    otd: (params: unknown) => ["analytics", "on-time-delivery", params] as const,
    scheduleQuality: ["analytics", "schedule-quality"] as const,
  },
  alerts: {
    all: ["alerts"] as const,
    list: (params: unknown) => ["alerts", "list", params] as const,
  },
  audit: {
    all: ["audit"] as const,
    list: (params: unknown) => ["audit", "list", params] as const,
  },
  dataQuality: {
    all: ["data-quality"] as const,
    list: (params: unknown) => ["data-quality", "list", params] as const,
  },
  system: {
    health: ["system", "health"] as const,
    syncRuns: ["system", "sync-runs"] as const,
  },
} as const;
