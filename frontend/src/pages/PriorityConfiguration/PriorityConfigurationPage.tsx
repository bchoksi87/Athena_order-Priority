import { useMemo, useState } from "react";

import { describeError } from "@/api/client";
import { usePreviewPriorityProfile, usePriorityProfile, usePriorityProfileVersions, useSavePriorityProfile } from "@/api/priority";
import type { PriorityProfile, PriorityResult } from "@/api/types";
import { useAuth } from "@/app/auth";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { PageHeader } from "@/components/PageHeader";
import { ScoreBar } from "@/components/ScoreBar";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { FACTOR_NAMES } from "@/lib/constants";
import { formatPct } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

const previewColumns: Column<PriorityResult>[] = [
  { key: "rank", header: "#", width: 40, numeric: true, cell: (r) => r.rank ?? "—", sortValue: (r) => r.rank ?? 9999 },
  { key: "order", header: "Order", cell: (r) => <span className="mono strong">{r.order_id}</span>, sortValue: (r) => r.order_id, filterValue: (r) => r.order_id },
  { key: "score", header: "Score", width: 140, cell: (r) => <ScoreBar value={r.score} width={130} breakdown={r.factors.map((f) => ({ label: f.name, points: f.points }))} />, sortValue: (r) => r.score },
  { key: "risk", header: "Risk", cell: (r) => r.risk_level, sortValue: (r) => r.risk_level },
  { key: "explain", header: "Explanation", cell: (r) => <span className="text-muted truncate" style={{ maxWidth: 420, display: "inline-block" }} title={r.explanation}>{r.explanation}</span> },
];

function NumberField({ label, value, onChange, step = 1, disabled }: { label: string; value: number | null; onChange: (v: number) => void; step?: number; disabled?: boolean }) {
  return (
    <label className="field">
      <span className="label">{label}</span>
      <input className="input num" type="number" step={step} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

/** Priority profile editor: factor weights (normalised live), aging/fairness/expedite rules, preview and versioned save. */
export default function PriorityConfigurationPage() {
  const { hasMinRole } = useAuth();
  const toast = useToast();
  const canEdit = hasMinRole("admin");
  const profileQuery = usePriorityProfile();
  const versions = usePriorityProfileVersions();
  const save = useSavePriorityProfile();
  const preview = usePreviewPriorityProfile();
  // Local edits overlay the server profile; null means "no unsaved changes".
  const [edits, setEdits] = useState<PriorityProfile | null>(null);
  const draft = edits ?? profileQuery.data ?? null;
  const dirty = edits !== null && JSON.stringify(edits) !== JSON.stringify(profileQuery.data);
  const totalWeight = useMemo(() => (draft?.weights ?? []).filter((w) => w.enabled).reduce((s, w) => s + w.weight, 0), [draft]);

  const update = (patch: Partial<PriorityProfile>) => setEdits(draft ? { ...draft, ...patch } : null);
  const updateWeight = (key: string, patch: { weight?: number; enabled?: boolean }) =>
    setEdits(draft ? { ...draft, weights: draft.weights.map((w) => (w.key === key ? { ...w, ...patch } : w)) } : null);

  const onSave = async () => {
    if (!draft) return;
    try {
      const saved = await save.mutateAsync(draft);
      setEdits(null);
      toast.push({ tone: "success", title: `Profile saved as v${saved.version}` });
    } catch (err) {
      toast.push({ tone: "error", title: "Save failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Configure"
        title="Priority Configuration"
        subtitle={draft ? `${draft.profile_id} · ${draft.name} · v${draft.version}${dirty ? " · unsaved changes" : ""}` : "Factor weights and rules of the priority engine"}
        actions={
          <>
            <button type="button" className="btn" onClick={() => draft && preview.mutate(draft)} disabled={!draft || preview.isPending}>
              {preview.isPending ? "Previewing…" : "Preview impact"}
            </button>
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
      <AsyncContent query={profileQuery} loadingLabel="Loading profile">
        {() =>
          draft ? (
            <div className="grid grid-main-side">
              <div className="col gap-3">
                <Section title="Factor weights" count={`sum ${totalWeight.toFixed(0)} · normalised at runtime`} flush>
                  <table className="dt-plain" style={{ width: "100%", fontSize: "var(--fs-sm)" }}>
                    <thead>
                      <tr className="text-xs text-muted upper">
                        <th style={{ textAlign: "left", padding: "6px 12px" }}>Factor</th>
                        <th style={{ textAlign: "left", padding: "6px 12px" }}>On</th>
                        <th style={{ textAlign: "left", padding: "6px 12px", width: 260 }}>Weight</th>
                        <th style={{ textAlign: "right", padding: "6px 12px" }}>Share</th>
                      </tr>
                    </thead>
                    <tbody>
                      {draft.weights.map((w) => (
                        <tr key={w.key} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                          <td style={{ padding: "4px 12px" }}>
                            <div className="strong">{FACTOR_NAMES[w.key] ?? w.key}</div>
                            <div className="mono text-faint text-xs">{w.key}</div>
                          </td>
                          <td style={{ padding: "4px 12px" }}>
                            <input type="checkbox" checked={w.enabled} disabled={!canEdit} onChange={(e) => updateWeight(w.key, { enabled: e.target.checked })} aria-label={`Enable ${w.key}`} />
                          </td>
                          <td style={{ padding: "4px 12px" }}>
                            <div className="row">
                              <input type="range" min={0} max={100} value={w.weight} disabled={!canEdit || !w.enabled} onChange={(e) => updateWeight(w.key, { weight: Number(e.target.value) })} style={{ flex: 1 }} aria-label={`Weight ${w.key}`} />
                              <input className="input num" type="number" min={0} max={100} value={w.weight} disabled={!canEdit || !w.enabled} onChange={(e) => updateWeight(w.key, { weight: Number(e.target.value) })} style={{ width: 64 }} />
                            </div>
                          </td>
                          <td className="num" style={{ padding: "4px 12px", textAlign: "right" }}>
                            {w.enabled && totalWeight > 0 ? formatPct((100 * w.weight) / totalWeight, 1) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Section>
                <div className="grid grid-3">
                  <Section title="Due date thresholds">
                    <div className="col gap-1">
                      <NumberField label="Critical within (h)" value={draft.due_date.critical_hours} disabled={!canEdit} onChange={(v) => update({ due_date: { ...draft.due_date, critical_hours: v } })} />
                      <NumberField label="High within (days)" value={draft.due_date.high_days} disabled={!canEdit} onChange={(v) => update({ due_date: { ...draft.due_date, high_days: v } })} />
                      <NumberField label="Medium within (days)" value={draft.due_date.medium_days} disabled={!canEdit} onChange={(v) => update({ due_date: { ...draft.due_date, medium_days: v } })} />
                      <NumberField label="Low within (days)" value={draft.due_date.low_days} disabled={!canEdit} onChange={(v) => update({ due_date: { ...draft.due_date, low_days: v } })} />
                      <label className="row text-sm">
                        <input type="checkbox" checked={draft.due_date.use_projected_lateness} disabled={!canEdit} onChange={(e) => update({ due_date: { ...draft.due_date, use_projected_lateness: e.target.checked } })} />
                        Use projected lateness
                      </label>
                    </div>
                  </Section>
                  <Section title="Aging & fairness">
                    <div className="col gap-1">
                      <label className="row text-sm">
                        <input type="checkbox" checked={draft.aging.enabled} disabled={!canEdit} onChange={(e) => update({ aging: { ...draft.aging, enabled: e.target.checked } })} />
                        Aging enabled
                      </label>
                      <NumberField label="Start after (days)" value={draft.aging.start_after_days} disabled={!canEdit} onChange={(v) => update({ aging: { ...draft.aging, start_after_days: v } })} />
                      <NumberField label="Points per day" value={draft.aging.points_per_day} step={0.5} disabled={!canEdit} onChange={(v) => update({ aging: { ...draft.aging, points_per_day: v } })} />
                      <NumberField label="Max points" value={draft.aging.max_points} disabled={!canEdit} onChange={(v) => update({ aging: { ...draft.aging, max_points: v } })} />
                      <label className="row text-sm">
                        <input type="checkbox" checked={draft.fairness.enabled} disabled={!canEdit} onChange={(e) => update({ fairness: { ...draft.fairness, enabled: e.target.checked } })} />
                        Fairness enabled
                      </label>
                      <NumberField label="Starvation after (days)" value={draft.fairness.max_wait_days} disabled={!canEdit} onChange={(v) => update({ fairness: { ...draft.fairness, max_wait_days: v } })} />
                      <NumberField label="Starvation boost" value={draft.fairness.starvation_boost_points} disabled={!canEdit} onChange={(v) => update({ fairness: { ...draft.fairness, starvation_boost_points: v } })} />
                    </div>
                  </Section>
                  <Section title="Expedite & risk">
                    <div className="col gap-1">
                      <NumberField label="Default boost points" value={draft.expedite.default_boost_points} disabled={!canEdit} onChange={(v) => update({ expedite: { ...draft.expedite, default_boost_points: v } })} />
                      <NumberField label="Max boost points" value={draft.expedite.max_boost_points} disabled={!canEdit} onChange={(v) => update({ expedite: { ...draft.expedite, max_boost_points: v } })} />
                      <NumberField label="Default duration (h)" value={draft.expedite.default_duration_hours} disabled={!canEdit} onChange={(v) => update({ expedite: { ...draft.expedite, default_duration_hours: v } })} />
                      <NumberField label="High risk slack (h)" value={draft.risk.high_slack_hours} disabled={!canEdit} onChange={(v) => update({ risk: { ...draft.risk, high_slack_hours: v } })} />
                      <NumberField label="Medium risk slack (h)" value={draft.risk.medium_slack_hours} disabled={!canEdit} onChange={(v) => update({ risk: { ...draft.risk, medium_slack_hours: v } })} />
                      <NumberField label="Blocked order cap" value={draft.blocked_order_cap} disabled={!canEdit} onChange={(v) => update({ blocked_order_cap: Number.isNaN(v) ? null : v })} />
                    </div>
                  </Section>
                </div>
                {preview.data ? (
                  <Section title="Preview" count={preview.data.summary ?? `${preview.data.results.length} orders`} flush>
                    <DataTable rows={preview.data.results} columns={previewColumns} rowKey={(r) => r.order_id} initialSort={{ key: "score", direction: "desc" }} filters dense pageSize={50} />
                  </Section>
                ) : preview.isError ? (
                  <Section title="Preview">
                    <span className="tone-late text-sm">{describeError(preview.error)}</span>
                  </Section>
                ) : null}
              </div>
              <div className="col gap-3">
                <Section title="Profile">
                  <div className="col gap-1">
                    <label className="field">
                      <span className="label">Name</span>
                      <input className="input" value={draft.name} disabled={!canEdit} onChange={(e) => update({ name: e.target.value })} />
                    </label>
                    <dl className="kv mt-2">
                      <dt>Profile id</dt>
                      <dd className="mono">{draft.profile_id}</dd>
                      <dt>Version</dt>
                      <dd className="num">{draft.version}</dd>
                      <dt>ERP priority points</dt>
                      <dd className="mono">{Object.entries(draft.erp_priority_points).map(([k, v]) => `${k}:${v}`).join(" ")}</dd>
                    </dl>
                  </div>
                </Section>
                <Section title="Versions" flush>
                  <AsyncContent query={versions} emptyTitle="No versions" compact>
                    {(list) => (
                      <DataTable rows={list} columns={[{ key: "v", header: "Version", numeric: true, cell: (v) => v.version, sortValue: (v) => v.version }, { key: "at", header: "Created", cell: (v) => <span className="num">{formatDateTime(v.created_at)}</span> }, { key: "by", header: "By", cell: (v) => v.created_by ?? "—" }, { key: "active", header: "Active", cell: (v) => (v.active ? "●" : "") }]} rowKey={(v) => String(v.version)} initialSort={{ key: "v", direction: "desc" }} dense pageSize={20} />
                    )}
                  </AsyncContent>
                </Section>
              </div>
            </div>
          ) : null
        }
      </AsyncContent>
    </div>
  );
}
