import { useMemo, useState } from "react";

import { describeError } from "@/api/client";
import { useSaveSchedulingConfig, useSchedulingConfig } from "@/api/config";
import type { BatchDimension, SchedulingConfig } from "@/api/types";
import { useAuth } from "@/app/auth";
import { AsyncContent } from "@/components/AsyncContent";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { formatPct } from "@/lib/formatters";

const BATCH_DIMENSIONS: BatchDimension[] = ["material", "machine", "tool", "fixture", "process", "part_family", "customer", "surface_finish", "technology"];

function Num({ label, value, onChange, step = 1, disabled, hint }: { label: string; value: number | null; onChange: (v: number | null) => void; step?: number; disabled?: boolean; hint?: string }) {
  return (
    <label className="field">
      <span className="label">{label}</span>
      <input className="input num" type="number" step={step} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      {hint ? <span className="text-faint text-xs">{hint}</span> : null}
    </label>
  );
}

function Check({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="row text-sm">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** Scheduling configuration editor (horizon, locking, stability, setup, batching, objectives, overtime). */
export default function SchedulingConfigurationPage() {
  const { hasMinRole } = useAuth();
  const toast = useToast();
  const canEdit = hasMinRole("admin");
  const query = useSchedulingConfig();
  const save = useSaveSchedulingConfig();
  // Local edits overlay the server configuration; null means "no unsaved changes".
  const [edits, setEdits] = useState<SchedulingConfig | null>(null);
  const draft = edits ?? query.data ?? null;
  const dirty = edits !== null && JSON.stringify(edits) !== JSON.stringify(query.data);
  const objectiveTotal = useMemo(() => (draft ? Object.values(draft.objectives).reduce((s, v) => s + v, 0) : 0), [draft]);
  const patch = <K extends keyof SchedulingConfig>(key: K, value: SchedulingConfig[K]) => setEdits(draft ? { ...draft, [key]: value } : null);

  const onSave = async () => {
    if (!draft) return;
    try {
      const saved = await save.mutateAsync(draft);
      setEdits(null);
      toast.push({ tone: "success", title: `Configuration saved as v${saved.version}` });
    } catch (err) {
      toast.push({ tone: "error", title: "Save failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Configure"
        title="Scheduling Configuration"
        subtitle={draft ? `${draft.config_id} · ${draft.name} · v${draft.version} · algorithm ${draft.algorithm}${dirty ? " · unsaved changes" : ""}` : "Horizon, locking, stability, setup, batching and objectives"}
        actions={
          <>
            <button type="button" className="btn" onClick={() => setEdits(null)} disabled={!dirty}>
              Discard
            </button>
            {canEdit ? (
              <button type="button" className="btn btn-primary" onClick={() => void onSave()} disabled={!dirty || save.isPending}>
                {save.isPending ? "Saving…" : "Save new version"}
              </button>
            ) : (
              <span className="badge">read only · admin edits</span>
            )}
          </>
        }
      />
      <AsyncContent query={query} loadingLabel="Loading configuration">
        {() =>
          draft ? (
            <div className="grid grid-3">
              <Section title="Horizon & locking">
                <div className="col gap-1">
                  <label className="field">
                    <span className="label">Name</span>
                    <input className="input" value={draft.name} disabled={!canEdit} onChange={(e) => patch("name", e.target.value)} />
                  </label>
                  <label className="field">
                    <span className="label">Algorithm</span>
                    <select className="select" value={draft.algorithm} disabled={!canEdit} onChange={(e) => patch("algorithm", e.target.value)}>
                      <option value="rule_based">rule_based (V1)</option>
                      <option value="cpsat">cpsat (V2, optional)</option>
                    </select>
                  </label>
                  <Num label="Horizon (days)" value={draft.horizon_days} disabled={!canEdit} onChange={(v) => patch("horizon_days", v ?? 0)} />
                  <Num label="Lock window (minutes)" value={draft.lock_window_minutes} disabled={!canEdit} onChange={(v) => patch("lock_window_minutes", v ?? 0)} hint="Entries starting within this window stay fixed" />
                  <Num label="At-risk slack (hours)" value={draft.at_risk_slack_hours} disabled={!canEdit} onChange={(v) => patch("at_risk_slack_hours", v ?? 0)} />
                  <Num label="Max orders per run" value={draft.max_orders_per_run} disabled={!canEdit} onChange={(v) => patch("max_orders_per_run", v)} hint="blank = all" />
                  <Check label="Schedule blocked orders after their blocker clears" checked={draft.schedule_blocked_orders} disabled={!canEdit} onChange={(v) => patch("schedule_blocked_orders", v)} />
                </div>
              </Section>
              <Section title="Stability & setup">
                <div className="col gap-1">
                  <Num label="Frozen window (minutes)" value={draft.stability.frozen_window_minutes} disabled={!canEdit} onChange={(v) => patch("stability", { ...draft.stability, frozen_window_minutes: v ?? 0 })} />
                  <Num label="Min improvement to replan (%)" value={draft.stability.min_improvement_pct} step={0.5} disabled={!canEdit} onChange={(v) => patch("stability", { ...draft.stability, min_improvement_pct: v ?? 0 })} />
                  <Num label="Max moves per replan" value={draft.stability.max_moves_per_replan} disabled={!canEdit} onChange={(v) => patch("stability", { ...draft.stability, max_moves_per_replan: v })} hint="blank = unlimited" />
                  <div className="divider" />
                  <Num label="Same-family setup factor" value={draft.setup.same_family_setup_factor} step={0.1} disabled={!canEdit} onChange={(v) => patch("setup", { ...draft.setup, same_family_setup_factor: v ?? 0 })} />
                  <Num label="Same-material setup factor" value={draft.setup.same_material_setup_factor} step={0.1} disabled={!canEdit} onChange={(v) => patch("setup", { ...draft.setup, same_material_setup_factor: v ?? 0 })} />
                  <Num label="Default setup (minutes)" value={draft.setup.default_setup_minutes} disabled={!canEdit} onChange={(v) => patch("setup", { ...draft.setup, default_setup_minutes: v ?? 0 })} />
                  <Num label="Setup penalty cost / minute" value={draft.setup.setup_penalty_cost_per_minute} step={0.1} disabled={!canEdit} onChange={(v) => patch("setup", { ...draft.setup, setup_penalty_cost_per_minute: v ?? 0 })} />
                </div>
              </Section>
              <Section title="Batching">
                <div className="col gap-1">
                  <Check label="Batching enabled" checked={draft.batching.enabled} disabled={!canEdit} onChange={(v) => patch("batching", { ...draft.batching, enabled: v })} />
                  <span className="label">Dimensions</span>
                  <div className="row row-wrap gap-1">
                    {BATCH_DIMENSIONS.map((dim) => {
                      const on = draft.batching.dimensions.includes(dim);
                      return (
                        <button key={dim} type="button" className={`chip${on ? " active" : ""}`} disabled={!canEdit} onClick={() => patch("batching", { ...draft.batching, dimensions: on ? draft.batching.dimensions.filter((d) => d !== dim) : [...draft.batching.dimensions, dim] })}>
                          {dim}
                        </button>
                      );
                    })}
                  </div>
                  <Num label="Max delay to batch (hours)" value={draft.batching.max_delay_hours} step={0.5} disabled={!canEdit} onChange={(v) => patch("batching", { ...draft.batching, max_delay_hours: v ?? 0 })} />
                  <Num label="Min priority gap" value={draft.batching.min_priority_gap} disabled={!canEdit} onChange={(v) => patch("batching", { ...draft.batching, min_priority_gap: v ?? 0 })} />
                </div>
              </Section>
              <Section title="Objectives" count={`sum ${objectiveTotal.toFixed(0)}`}>
                <div className="col gap-1">
                  {(Object.keys(draft.objectives) as Array<keyof SchedulingConfig["objectives"]>).map((k) => (
                    <div className="row" key={k}>
                      <span className="text-sm" style={{ width: 150 }}>
                        {k.replace(/_/g, " ")}
                      </span>
                      <input type="range" min={0} max={100} value={draft.objectives[k]} disabled={!canEdit} onChange={(e) => patch("objectives", { ...draft.objectives, [k]: Number(e.target.value) })} style={{ flex: 1 }} aria-label={k} />
                      <span className="num text-sm" style={{ width: 64, textAlign: "right" }}>
                        {objectiveTotal > 0 ? formatPct((100 * draft.objectives[k]) / objectiveTotal, 0) : "—"}
                      </span>
                    </div>
                  ))}
                </div>
              </Section>
              <Section title="Overtime & machine preference">
                <div className="col gap-1">
                  <Check label="Allow overtime" checked={draft.overtime.allow_overtime} disabled={!canEdit} onChange={(v) => patch("overtime", { ...draft.overtime, allow_overtime: v })} />
                  <Num label="Max overtime hours / day" value={draft.overtime.max_overtime_hours_per_day} step={0.5} disabled={!canEdit} onChange={(v) => patch("overtime", { ...draft.overtime, max_overtime_hours_per_day: v ?? 0 })} />
                  <Num label="Overtime cost / hour" value={draft.overtime.overtime_cost_per_hour} disabled={!canEdit} onChange={(v) => patch("overtime", { ...draft.overtime, overtime_cost_per_hour: v ?? 0 })} />
                  <div className="divider" />
                  <Num label="Non-preferred machine cost (min)" value={draft.machine_preference.non_preferred_machine_cost_minutes} disabled={!canEdit} onChange={(v) => patch("machine_preference", { ...draft.machine_preference, non_preferred_machine_cost_minutes: v ?? 0 })} />
                  <Num label="Utilisation balance cost / %" value={draft.machine_preference.utilization_balance_cost_per_pct} step={0.1} disabled={!canEdit} onChange={(v) => patch("machine_preference", { ...draft.machine_preference, utilization_balance_cost_per_pct: v ?? 0 })} />
                </div>
              </Section>
              <Section title="Quality weights">
                <div className="col gap-1">
                  {Object.entries(draft.quality_weights).map(([k, v]) => (
                    <Num key={k} label={k.replace(/_/g, " ")} value={v} disabled={!canEdit} onChange={(nv) => patch("quality_weights", { ...draft.quality_weights, [k]: nv ?? 0 })} />
                  ))}
                </div>
              </Section>
            </div>
          ) : null
        }
      </AsyncContent>
    </div>
  );
}
