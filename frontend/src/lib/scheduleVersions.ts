/** Presentation helpers for schedule versions (spec Phase 37). */
import type { ScheduleVersionResponse } from "@/api/types";

import { humanize } from "./constants";
import { formatDateTime } from "./time";

/** "v4 · Draft · 12 Sep 13:19 · e2e" for pickers and lists. */
export function describeVersion(v: ScheduleVersionResponse): string {
  const parts = [`v${v.version_number}`, humanize(v.status), formatDateTime(v.generated_at, "dd MMM HH:mm")];
  if (v.label) parts.push(v.label);
  else if (v.trigger && v.trigger !== "manual") parts.push(v.trigger);
  return parts.join(" · ");
}
