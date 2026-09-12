import { useScheduleVersions } from "@/api/schedule";
import type { ScheduleVersionResponse } from "@/api/types";
import { humanize } from "@/lib/constants";
import { formatDateTime } from "@/lib/time";

export interface VersionSelectProps {
  /** Selected version number; null = the active plan. */
  value: number | null;
  onChange: (version: number | null) => void;
  /** The active plan, used for the default option's label. */
  active?: ScheduleVersionResponse | null;
  label?: string;
  /** Label of the "active plan" option; pass null to hide it (compare pickers). */
  activeOptionLabel?: string | null;
  /** Version numbers to leave out (e.g. the one already selected on the other side of a comparison). */
  exclude?: number[];
  ariaLabel?: string;
  disabled?: boolean;
}

export function describeVersion(v: ScheduleVersionResponse): string {
  const parts = [`v${v.version_number}`, humanize(v.status), formatDateTime(v.generated_at, "dd MMM HH:mm")];
  if (v.label) parts.push(v.label);
  else if (v.trigger && v.trigger !== "manual") parts.push(v.trigger);
  return parts.join(" · ");
}

/** Schedule version picker backed by GET /schedule/versions (spec Phase 37): active plan vs a draft. */
export function VersionSelect({ value, onChange, active, label, activeOptionLabel = "Active plan", exclude = [], ariaLabel = "Schedule version", disabled = false }: VersionSelectProps) {
  const versions = useScheduleVersions({ page_size: 100 });
  const items = (versions.data?.items ?? []).filter((v) => !exclude.includes(v.version_number));
  const select = (
    <select
      className="select"
      value={value === null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      aria-label={ariaLabel}
      disabled={disabled}
    >
      {activeOptionLabel !== null ? (
        <option value="">
          {activeOptionLabel}
          {active ? ` (v${active.version_number} · ${humanize(active.status)})` : versions.isPending ? "" : " (none)"}
        </option>
      ) : (
        <option value="">Choose version…</option>
      )}
      {items.map((v) => (
        <option key={v.version_number} value={v.version_number}>
          {describeVersion(v)}
        </option>
      ))}
      {versions.isError ? <option disabled>versions unavailable</option> : null}
    </select>
  );
  if (!label) return select;
  return (
    <label className="row gap-1">
      <span className="label" style={{ marginBottom: 0 }}>
        {label}
      </span>
      {select}
    </label>
  );
}
