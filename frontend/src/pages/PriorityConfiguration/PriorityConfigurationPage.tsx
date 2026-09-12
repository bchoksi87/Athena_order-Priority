/**
 * Priority Configuration (spec Phase 14): factor weights with live normalisation, the scoring
 * sub-configurations (schema-driven forms), "Preview impact" against the live order book, and
 * versioned saves with activate/rollback.
 */
import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { describeError } from "@/api/client";
import { fetchPriorityProfileVersion, useActivatePriorityProfileVersion, usePreviewPriorityProfile, usePriorityConfiguration, usePriorityProfileVersions, useSavePriorityProfile } from "@/api/priority";
import type { FactorWeight, OrderMove, PreviewResponse, PriorityProfile, SystemConfig, WeightChange } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ActionDialog } from "@/components/ActionDialog";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { SchemaForm } from "@/components/SchemaForm";
import { Section } from "@/components/Section";
import { Tabs } from "@/components/Tabs";
import { TextField } from "@/components/Field";
import { useToast } from "@/components/Toast";
import { FACTOR_NAMES } from "@/lib/constants";
import { FACTOR_HELP, PRIORITY_SECTIONS, PRIORITY_TOP_LEVEL } from "@/lib/configSchema";
import { diffJson } from "@/lib/diff";
import { formatNumber, formatPct, formatSigned } from "@/lib/formatters";

import { ConfigVersionsPanel } from "../shared/ConfigVersionsPanel";
import "./PriorityConfiguration.css";

/** Normalised share (0–100) of each enabled weight; disabled or zero-sum weights get 0. */
export function normaliseWeights(weights: FactorWeight[]): Record<string, number> {
  const enabled = weights.filter((w) => w.enabled && w.weight > 0);
  const total = enabled.reduce((s, w) => s + w.weight, 0);
  const out: Record<string, number> = {};
  for (const w of weights) out[w.key] = w.enabled && total > 0 ? (100 * w.weight) / total : 0;
  return out;
}

const CAT = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6", "--cat-7", "--cat-8", "--status-hold", "--status-done", "--fg-faint"];

function WeightBar({ weights, shares }: { weights: FactorWeight[]; shares: Record<string, number> }) {
  return (
    <div className="wbar" role="img" aria-label="Normalised weight shares" data-testid="weight-bar">
      {weights.map((w, i) => {
        const pct = shares[w.key] ?? 0;
        if (pct <= 0) return null;
        return <span key={w.key} className="wbar-seg" style={{ width: `${pct}%`, background: `var(${CAT[i % CAT.length]})` }} title={`${FACTOR_NAMES[w.key] ?? w.key} ${formatPct(pct, 1)}`} />;
      })}
    </div>
  );
}

const weightChangeColumns: Column<WeightChange>[] = [
  { key: "key", header: "Factor", cell: (w) => FACTOR_NAMES[w.key] ?? w.key },
  { key: "prev", header: "Active", numeric: true, cell: (w) => formatPct(w.previous_pct, 1) },
  { key: "new", header: "Candidate", numeric: true, cell: (w) => formatPct(w.new_pct, 1) },
  { key: "delta", header: "Δ", numeric: true, cell: (w) => <span className={w.new_pct > w.previous_pct ? "tone-ready" : w.new_pct < w.previous_pct ? "tone-late" : ""}>{formatSigned(w.new_pct - w.previous_pct, 1)} pp</span> },
];

function moveColumns(): Column<OrderMove & { direction?: string }>[] {
  return [
    { key: "order", header: "Order", cell: (m) => <Link className="mono strong" to={routes.orderDetail(encodeURIComponent(m.order_id))}>{m.order_id}</Link>, sortValue: (m) => m.order_id },
    { key: "rank", header: "Rank Δ", numeric: true, cell: (m) => <span className={m.rank_delta < 0 ? "tone-ready strong" : m.rank_delta > 0 ? "tone-late" : ""}>{m.rank_delta < 0 ? `▲ ${-m.rank_delta}` : m.rank_delta > 0 ? `▼ ${m.rank_delta}` : "="}</span>, sortValue: (m) => m.rank_delta, title: "Negative = moves up the queue" },
    { key: "score", header: "Score Δ", numeric: true, cell: (m) => <span className={m.score_delta > 0 ? "tone-ready" : m.score_delta < 0 ? "tone-late" : ""}>{formatSigned(m.score_delta, 1)}</span>, sortValue: (m) => m.score_delta },
  ];
}

function PreviewResult({ preview, topN }: { preview: PreviewResponse; topN: number }) {
  const moves = new Map(preview.biggest_moves.map((m) => [m.order_id, m]));
  const withMoves = (ids: string[]) => ids.map((id) => moves.get(id) ?? { order_id: id, rank_delta: 0, score_delta: 0 });
  const rankBefore = new Map(preview.top_n_before.map((id, i) => [id, i + 1]));
  const rankAfter = new Map(preview.top_n_after.map((id, i) => [id, i + 1]));
  const entered = preview.entered_top_n.map((id) => ({ order_id: id, rank_delta: (rankAfter.get(id) ?? topN) - (rankBefore.get(id) ?? topN + 1), score_delta: moves.get(id)?.score_delta ?? 0 }));
  const left = preview.left_top_n.map((id) => ({ order_id: id, rank_delta: (rankAfter.get(id) ?? topN + 1) - (rankBefore.get(id) ?? topN), score_delta: moves.get(id)?.score_delta ?? 0 }));
  return (
    <div className="col gap-3" data-testid="preview-result">
      <div className="preview-summary">{preview.summary}</div>
      <div className="grid grid-kpi">
        <KpiCard label="Orders evaluated" value={formatNumber(preview.orders_evaluated)} tone="neutral" />
        <KpiCard label={`Enter top ${preview.top_n}`} value={preview.entered_top_n.length} tone="ready" />
        <KpiCard label={`Leave top ${preview.top_n}`} value={preview.left_top_n.length} tone="late" />
        <KpiCard label="Changed rank" value={preview.orders_changed_rank} tone="at-risk" />
        <KpiCard label="Mean |Δ score|" value={formatNumber(preview.mean_abs_score_delta, 2)} tone="neutral" hint={`max ${formatNumber(preview.max_abs_score_delta, 1)}`} />
      </div>
      <div className="grid grid-3">
        <Section title="Weight changes" count={preview.weight_changes.length} flush>
          <DataTable rows={preview.weight_changes} columns={weightChangeColumns} rowKey={(w) => w.key} dense pageSize={50} emptyMessage="No weight changes" />
        </Section>
        <Section title={`Entering top ${preview.top_n}`} count={entered.length} flush>
          <DataTable rows={entered} columns={moveColumns()} rowKey={(m) => m.order_id} dense pageSize={50} emptyMessage="No orders enter" initialSort={{ key: "rank", direction: "asc" }} />
        </Section>
        <Section title={`Leaving top ${preview.top_n}`} count={left.length} flush>
          <DataTable rows={left} columns={moveColumns()} rowKey={(m) => m.order_id} dense pageSize={50} emptyMessage="No orders leave" initialSort={{ key: "rank", direction: "desc" }} />
        </Section>
      </div>
      <Section title="Biggest rank moves" count={preview.biggest_moves.length} flush>
        <DataTable rows={withMoves(preview.biggest_moves.map((m) => m.order_id))} columns={moveColumns()} rowKey={(m) => m.order_id} dense pageSize={25} emptyMessage="No rank changes" />
      </Section>
    </div>
  );
}

type TabKey = "weights" | (typeof PRIORITY_SECTIONS)[number]["key"] | "profile";

export default function PriorityConfigurationPage() {
  const { hasMinRole } = useAuth();
  const toast = useToast();
  const canEdit = hasMinRole("admin");
  const canPreview = hasMinRole("planner");
  const configQuery = usePriorityConfiguration();
  const versions = usePriorityProfileVersions();
  const save = useSavePriorityProfile();
  const activate = useActivatePriorityProfileVersion();
  const preview = usePreviewPriorityProfile();
  const [edits, setEdits] = useState<PriorityProfile | null>(null);
  const [tab, setTab] = useState<TabKey>("weights");
  const [topN, setTopN] = useState(50);
  const [saveOpen, setSaveOpen] = useState(false);

  const active = configQuery.data?.profile ?? null;
  const draft = edits ?? active;
  const changes = useMemo(() => (edits && active ? diffJson(active, edits) : []), [edits, active]);
  const dirty = changes.length > 0;
  const shares = useMemo(() => normaliseWeights(draft?.weights ?? []), [draft]);
  const rawTotal = useMemo(() => (draft?.weights ?? []).filter((w) => w.enabled).reduce((s, w) => s + w.weight, 0), [draft]);

  const update = useCallback((patch: Partial<PriorityProfile>) => setEdits((prev) => (prev ?? active ? { ...(prev ?? (active as PriorityProfile)), ...patch } : null)), [active]);
  const updateWeight = (key: string, patch: Partial<FactorWeight>) => {
    if (!draft) return;
    update({ weights: draft.weights.map((w) => (w.key === key ? { ...w, ...patch } : w)) });
  };

  const runPreview = () => {
    if (!draft) return;
    preview.mutate({ profile: draft, top_n: topN });
  };

  const onSave = async (reason: string) => {
    if (!draft) return;
    try {
      const res = await save.mutateAsync({ profile: draft, reason });
      setEdits(null);
      setSaveOpen(false);
      preview.reset();
      toast.push({ tone: "success", title: `Profile saved as v${res.version.version}`, message: `${Object.keys(res.changed_new).length} field${Object.keys(res.changed_new).length === 1 ? "" : "s"} changed${res.previous_version !== null ? ` from v${res.previous_version}` : ""}.` });
    } catch (err) {
      toast.push({ tone: "error", title: "Save failed", message: describeError(err) });
    }
  };

  const onActivate = async (version: number, reason: string) => {
    try {
      const res = await activate.mutateAsync({ version, reason });
      setEdits(null);
      toast.push({ tone: "success", title: `Activated v${res.version.version}` });
    } catch (err) {
      toast.push({ tone: "error", title: "Activate failed", message: describeError(err) });
      throw err;
    }
  };

  const tabs: Array<{ key: TabKey; label: string }> = [{ key: "weights", label: "Weights" }, ...PRIORITY_SECTIONS.map((s) => ({ key: s.key as TabKey, label: s.title })), { key: "profile", label: PRIORITY_TOP_LEVEL.title }];
  const section = PRIORITY_SECTIONS.find((s) => s.key === tab);

  return (
    <div className="page" data-testid="priority-configuration-page">
      <PageHeader
        eyebrow="Configure"
        title="Priority Configuration"
        subtitle={
          draft ? (
            <span className="row row-wrap">
              <span>
                <span className="mono">{draft.profile_id}</span> · {draft.name} · v{draft.version}
              </span>
              {configQuery.data?.version ? <span className="text-faint">active config v{configQuery.data.version.version}</span> : null}
              {dirty ? <span className="pill pill-at-risk pill-sm">{changes.length} UNSAVED CHANGE{changes.length > 1 ? "S" : ""}</span> : null}
              {!canEdit ? <span className="pill pill-neutral pill-sm">READ ONLY · admin saves</span> : null}
            </span>
          ) : (
            "Factor weights and scoring rules of the priority engine"
          )
        }
        actions={
          <>
            <label className="row text-sm">
              <span className="text-muted">Top N</span>
              <input className="input num" type="number" min={1} max={500} value={topN} style={{ width: 70 }} onChange={(e) => setTopN(Math.max(1, Math.min(500, Number(e.target.value) || 50)))} aria-label="Preview top N" />
            </label>
            <button type="button" className="btn" onClick={runPreview} disabled={!draft || preview.isPending || !canPreview} title={canPreview ? "Simulate the candidate profile against the live order book" : "Planner role required"}>
              {preview.isPending ? "Previewing…" : "Preview impact"}
            </button>
            <button type="button" className="btn" onClick={() => { setEdits(null); preview.reset(); }} disabled={!dirty}>
              Discard
            </button>
            {canEdit ? (
              <button type="button" className="btn btn-primary" onClick={() => setSaveOpen(true)} disabled={!dirty || save.isPending}>
                {save.isPending ? "Saving…" : "Save new version"}
              </button>
            ) : null}
          </>
        }
      />

      <AsyncContent query={configQuery} loadingLabel="Loading profile">
        {() =>
          draft ? (
            <div className="grid grid-main-side">
              <div className="col gap-3">
                <Section
                  title="Factor weights"
                  count={`raw sum ${formatNumber(rawTotal, 0)} · normalised to 100%`}
                  actions={
                    <span className="text-xs text-faint">
                      server: {Object.entries(configQuery.data?.weights_pct ?? {}).map(([k, v]) => `${FACTOR_NAMES[k] ?? k} ${formatPct(v, 0)}`).join(" · ")}
                    </span>
                  }
                >
                  <WeightBar weights={draft.weights} shares={shares} />
                  <table className="wtable" data-testid="weights-table">
                    <thead>
                      <tr>
                        <th>Factor</th>
                        <th>On</th>
                        <th style={{ width: 300 }}>Weight</th>
                        <th className="num">Share</th>
                      </tr>
                    </thead>
                    <tbody>
                      {draft.weights.map((w, i) => (
                        <tr key={w.key} className={w.enabled ? "" : "wrow-off"}>
                          <td>
                            <div className="row">
                              <span className="wswatch" style={{ background: `var(${CAT[i % CAT.length]})` }} />
                              <div>
                                <div className="strong">{FACTOR_NAMES[w.key] ?? w.key}</div>
                                <div className="text-faint text-xs">{FACTOR_HELP[w.key] ?? w.key}</div>
                              </div>
                            </div>
                          </td>
                          <td>
                            <input type="checkbox" checked={w.enabled} disabled={!canEdit} onChange={(e) => updateWeight(w.key, { enabled: e.target.checked })} aria-label={`Enable ${w.key}`} />
                          </td>
                          <td>
                            <div className="row">
                              <input type="range" min={0} max={100} step={1} value={w.weight} disabled={!canEdit || !w.enabled} onChange={(e) => updateWeight(w.key, { weight: Number(e.target.value) })} style={{ flex: 1 }} aria-label={`Weight ${w.key}`} />
                              <input className="input num" type="number" min={0} max={100} step={1} value={w.weight} disabled={!canEdit || !w.enabled} onChange={(e) => updateWeight(w.key, { weight: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} style={{ width: 64 }} aria-label={`Weight value ${w.key}`} />
                            </div>
                          </td>
                          <td className="num" data-testid={`weight-share-${w.key}`}>
                            {w.enabled ? formatPct(shares[w.key] ?? 0, 1) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Section>

                <Section title="Scoring rules">
                  <Tabs<TabKey> items={tabs} value={tab} onChange={setTab} ariaLabel="Priority sub-configurations" />
                  <div className="mt-4">
                    {tab === "weights" ? (
                      <p className="text-muted text-sm">Choose a rule set above to edit its thresholds. Weights decide how much each factor's 0–100 raw score contributes; the rule sets decide how the raw score is computed.</p>
                    ) : section ? (
                      <>
                        {section.description ? <p className="text-muted text-sm">{section.description}</p> : null}
                        <SchemaForm schema={section} value={draft[section.key as keyof PriorityProfile] as Record<string, unknown>} disabled={!canEdit} onChange={(next) => update({ [section.key]: next } as Partial<PriorityProfile>)} />
                      </>
                    ) : (
                      <SchemaForm schema={PRIORITY_TOP_LEVEL} value={{ erp_priority_points: draft.erp_priority_points, blocked_order_cap: draft.blocked_order_cap }} disabled={!canEdit} onChange={(next) => update(next as Partial<PriorityProfile>)} />
                    )}
                  </div>
                </Section>

                {preview.data ? (
                  <Section title="Preview impact" count={`top ${preview.data.top_n}`} actions={<span className="text-xs text-faint">{preview.data.active_profile_id} → {preview.data.candidate_profile_id}</span>}>
                    <PreviewResult preview={preview.data} topN={preview.data.top_n} />
                  </Section>
                ) : preview.isError ? (
                  <Section title="Preview impact">
                    <span className="tone-late text-sm">{describeError(preview.error)}</span>
                  </Section>
                ) : null}
              </div>

              <div className="col gap-3">
                <Section title="Profile">
                  <div className="col gap-1">
                    <TextField label="Name" value={draft.name} disabled={!canEdit} onChange={(v) => update({ name: v })} />
                    <dl className="kv mt-2">
                      <dt>Profile id</dt>
                      <dd className="mono">{draft.profile_id}</dd>
                      <dt>Profile version</dt>
                      <dd className="num">{draft.version}</dd>
                      <dt>Active config</dt>
                      <dd className="num">{configQuery.data?.version ? `v${configQuery.data.version.version} · ${configQuery.data.version.created_by ?? "system"}` : "—"}</dd>
                      <dt>Enabled factors</dt>
                      <dd className="num">
                        {draft.weights.filter((w) => w.enabled).length} / {draft.weights.length}
                      </dd>
                    </dl>
                  </div>
                </Section>
                {dirty ? (
                  <Section title="Unsaved changes" count={changes.length}>
                    <ul className="reason-list text-xs">
                      {changes.slice(0, 12).map((c) => (
                        <li key={c.path}>
                          <span className="mono">{c.path}</span>: <span className="tone-late">{String(c.before ?? "—")}</span> → <span className="tone-ready">{String(c.after ?? "—")}</span>
                        </li>
                      ))}
                      {changes.length > 12 ? <li className="text-faint">… {changes.length - 12} more</li> : null}
                    </ul>
                  </Section>
                ) : null}
                <ConfigVersionsPanel
                  versions={versions.data}
                  isPending={versions.isPending}
                  error={versions.error}
                  fetchVersion={fetchPriorityProfileVersion}
                  queryKeyPrefix={["priority", "configuration"]}
                  extract={(cfg: SystemConfig) => cfg.priority_profile}
                  current={active}
                  canActivate={canEdit}
                  activating={activate.isPending}
                  activateError={activate.error}
                  onActivate={onActivate}
                />
              </div>
            </div>
          ) : null
        }
      </AsyncContent>

      <ActionDialog
        open={saveOpen}
        title="Save new profile version"
        description={`Saves the ${changes.length} change${changes.length === 1 ? "" : "s"} as a new configuration version and activates it. The next engine run scores orders with it.`}
        submitLabel="Save version"
        busy={save.isPending}
        error={save.error}
        onSubmit={(reason) => void onSave(reason)}
        onClose={() => setSaveOpen(false)}
      />
    </div>
  );
}
