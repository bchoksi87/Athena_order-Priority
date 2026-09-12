/**
 * Scheduling Configuration: the scheduling, replanning, alert and data-quality rule sets
 * (each saved as its own version with a mandatory reason), plus versions with activate/rollback.
 */
import { useMemo, useState } from "react";

import { describeError } from "@/api/client";
import { fetchSchedulingConfigVersion, useActivateSchedulingConfigVersion, useSaveSchedulingConfiguration, useSchedulingConfigVersions, useSchedulingConfiguration } from "@/api/config";
import type { AlertConfig, DataQualityConfig, ReplanningConfig, SchedulingConfig, SchedulingConfigUpdateRequest, SystemConfig } from "@/api/types";
import { useAuth } from "@/app/auth";
import { ActionDialog } from "@/components/ActionDialog";
import { AsyncContent } from "@/components/AsyncContent";
import { SelectField, TextField } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { SchemaForm } from "@/components/SchemaForm";
import { Section } from "@/components/Section";
import { Tabs } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { ALERTS_SECTION, DATA_QUALITY_SECTION, REPLANNING_SECTION, SCHEDULING_SECTIONS } from "@/lib/configSchema";
import { diffJson } from "@/lib/diff";
import { formatPct } from "@/lib/formatters";

import { ConfigVersionsPanel } from "../shared/ConfigVersionsPanel";

type SectionKey = "scheduling" | "replanning" | "alerts" | "data_quality";

interface Drafts {
  scheduling: SchedulingConfig | null;
  replanning: ReplanningConfig | null;
  alerts: AlertConfig | null;
  data_quality: DataQualityConfig | null;
}

const SECTION_LABELS: Record<SectionKey, string> = { scheduling: "Scheduling", replanning: "Replanning", alerts: "Alerts", data_quality: "Data quality" };

/** Keys of SchedulingConfig edited by the "general" card (everything that is not a nested rule set). */
const GENERAL_KEYS = ["horizon_days", "lock_window_minutes", "at_risk_slack_hours", "schedule_blocked_orders", "max_orders_per_run"] as const;

export default function SchedulingConfigurationPage() {
  const { hasMinRole } = useAuth();
  const toast = useToast();
  const canEdit = hasMinRole("admin");
  const query = useSchedulingConfiguration();
  const versions = useSchedulingConfigVersions();
  const save = useSaveSchedulingConfiguration();
  const activate = useActivateSchedulingConfigVersion();
  const [drafts, setDrafts] = useState<Drafts>({ scheduling: null, replanning: null, alerts: null, data_quality: null });
  const [tab, setTab] = useState<SectionKey>("scheduling");
  const [saveOpen, setSaveOpen] = useState(false);

  const active = query.data ?? null;
  const scheduling = drafts.scheduling ?? active?.scheduling ?? null;
  const replanning = drafts.replanning ?? active?.replanning ?? null;
  const alerts = drafts.alerts ?? active?.alerts ?? null;
  const dataQuality = drafts.data_quality ?? active?.data_quality ?? null;

  const dirtySections = useMemo(() => {
    if (!active) return [] as SectionKey[];
    const out: SectionKey[] = [];
    if (drafts.scheduling && diffJson(active.scheduling, drafts.scheduling).length > 0) out.push("scheduling");
    if (drafts.replanning && diffJson(active.replanning, drafts.replanning).length > 0) out.push("replanning");
    if (drafts.alerts && diffJson(active.alerts, drafts.alerts).length > 0) out.push("alerts");
    if (drafts.data_quality && diffJson(active.data_quality, drafts.data_quality).length > 0) out.push("data_quality");
    return out;
  }, [active, drafts]);
  const dirty = dirtySections.length > 0;

  const patchScheduling = (patch: Partial<SchedulingConfig>) => scheduling && setDrafts((d) => ({ ...d, scheduling: { ...scheduling, ...patch } }));

  const objectiveTotal = scheduling ? Object.values(scheduling.objectives).reduce((s, v) => s + v, 0) : 0;

  const onSave = async (reason: string) => {
    if (!active) return;
    const body: SchedulingConfigUpdateRequest = { reason };
    if (dirtySections.includes("scheduling") && drafts.scheduling) body.scheduling = drafts.scheduling;
    if (dirtySections.includes("replanning") && drafts.replanning) body.replanning = drafts.replanning;
    if (dirtySections.includes("alerts") && drafts.alerts) body.alerts = drafts.alerts;
    if (dirtySections.includes("data_quality") && drafts.data_quality) body.data_quality = drafts.data_quality;
    try {
      const res = await save.mutateAsync(body);
      setDrafts({ scheduling: null, replanning: null, alerts: null, data_quality: null });
      setSaveOpen(false);
      toast.push({ tone: "success", title: `Configuration saved as v${res.version.version}`, message: `${dirtySections.map((s) => SECTION_LABELS[s]).join(", ")} updated · ${Object.keys(res.changed_new).length} field(s) changed.` });
    } catch (err) {
      toast.push({ tone: "error", title: "Save failed", message: describeError(err) });
    }
  };

  const onActivate = async (version: number, reason: string) => {
    try {
      const res = await activate.mutateAsync({ version, reason });
      setDrafts({ scheduling: null, replanning: null, alerts: null, data_quality: null });
      toast.push({ tone: "success", title: `Activated v${res.version.version}` });
    } catch (err) {
      toast.push({ tone: "error", title: "Activate failed", message: describeError(err) });
      throw err;
    }
  };

  return (
    <div className="page" data-testid="scheduling-configuration-page">
      <PageHeader
        eyebrow="Configure"
        title="Scheduling Configuration"
        subtitle={
          scheduling ? (
            <span className="row row-wrap">
              <span>
                <span className="mono">{scheduling.config_id}</span> · {scheduling.name} · v{scheduling.version} · algorithm <span className="mono">{scheduling.algorithm}</span>
              </span>
              {active?.version ? <span className="text-faint">active config v{active.version.version}</span> : null}
              {dirty ? <span className="pill pill-at-risk pill-sm">UNSAVED: {dirtySections.map((s) => SECTION_LABELS[s]).join(", ")}</span> : null}
              {!canEdit ? <span className="pill pill-neutral pill-sm">READ ONLY · admin saves</span> : null}
            </span>
          ) : (
            "Horizon, locking, stability, setup, batching, objectives, overtime, replanning, alerts and data quality rules"
          )
        }
        actions={
          <>
            <button type="button" className="btn" onClick={() => setDrafts({ scheduling: null, replanning: null, alerts: null, data_quality: null })} disabled={!dirty}>
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

      <AsyncContent query={query} loadingLabel="Loading configuration">
        {() =>
          scheduling && replanning && alerts && dataQuality ? (
            <div className="grid grid-main-side">
              <div className="col gap-3">
                <Tabs<SectionKey>
                  items={(Object.keys(SECTION_LABELS) as SectionKey[]).map((k) => ({ key: k, label: `${SECTION_LABELS[k]}${dirtySections.includes(k) ? " •" : ""}` }))}
                  value={tab}
                  onChange={setTab}
                  ariaLabel="Configuration sections"
                />

                {tab === "scheduling" ? (
                  <div className="grid grid-2">
                    <Section title="Identity">
                      <div className="col gap-1">
                        <TextField label="Name" value={scheduling.name} disabled={!canEdit} onChange={(v) => patchScheduling({ name: v })} />
                        <SelectField
                          label="Algorithm"
                          value={scheduling.algorithm}
                          disabled={!canEdit}
                          options={[
                            { value: "rule_based", label: "rule_based — deterministic priority-driven list scheduler (V1)" },
                            { value: "cpsat", label: "cpsat — constraint optimisation (V2, when installed)" },
                          ]}
                          onChange={(v) => patchScheduling({ algorithm: v })}
                          help="Which scheduler the planning pipeline runs; the rule-based scheduler is always available."
                        />
                        <dl className="kv mt-2">
                          <dt>Config id</dt>
                          <dd className="mono">{scheduling.config_id}</dd>
                          <dt>Version</dt>
                          <dd className="num">{scheduling.version}</dd>
                        </dl>
                      </div>
                    </Section>
                    {SCHEDULING_SECTIONS.map((section) => {
                      const isGeneral = section.key === "general";
                      const isQuality = section.key === "quality_weights";
                      const value: Record<string, unknown> = isGeneral
                        ? Object.fromEntries(GENERAL_KEYS.map((k) => [k, scheduling[k]]))
                        : isQuality
                          ? { quality_weights: scheduling.quality_weights }
                          : (scheduling[section.key as keyof SchedulingConfig] as Record<string, unknown>);
                      return (
                        <Section key={section.key} title={section.title} count={section.key === "objectives" ? `sum ${objectiveTotal.toFixed(0)}` : undefined}>
                          {section.description ? <p className="text-muted text-xs">{section.description}</p> : null}
                          <SchemaForm
                            schema={section}
                            value={value}
                            disabled={!canEdit}
                            onChange={(next) => {
                              if (isGeneral || isQuality) patchScheduling(next as Partial<SchedulingConfig>);
                              else patchScheduling({ [section.key]: next } as Partial<SchedulingConfig>);
                            }}
                          />
                          {section.key === "objectives" ? (
                            <div className="text-xs text-muted mt-2">
                              Normalised: {(Object.keys(scheduling.objectives) as Array<keyof SchedulingConfig["objectives"]>).map((k) => `${k.replace(/_/g, " ")} ${objectiveTotal > 0 ? formatPct((100 * scheduling.objectives[k]) / objectiveTotal, 0) : "—"}`).join(" · ")}
                            </div>
                          ) : null}
                        </Section>
                      );
                    })}
                  </div>
                ) : null}

                {tab === "replanning" ? (
                  <Section title={REPLANNING_SECTION.title}>
                    <p className="text-muted text-sm">{REPLANNING_SECTION.description}</p>
                    <SchemaForm schema={REPLANNING_SECTION} value={replanning as unknown as Record<string, unknown>} disabled={!canEdit} onChange={(next) => setDrafts((d) => ({ ...d, replanning: next as unknown as ReplanningConfig }))} />
                  </Section>
                ) : null}

                {tab === "alerts" ? (
                  <Section title={ALERTS_SECTION.title}>
                    <p className="text-muted text-sm">{ALERTS_SECTION.description}</p>
                    <SchemaForm schema={ALERTS_SECTION} value={alerts as unknown as Record<string, unknown>} disabled={!canEdit} onChange={(next) => setDrafts((d) => ({ ...d, alerts: next as unknown as AlertConfig }))} />
                  </Section>
                ) : null}

                {tab === "data_quality" ? (
                  <Section title={DATA_QUALITY_SECTION.title}>
                    <p className="text-muted text-sm">{DATA_QUALITY_SECTION.description}</p>
                    <SchemaForm schema={DATA_QUALITY_SECTION} value={dataQuality as unknown as Record<string, unknown>} disabled={!canEdit} onChange={(next) => setDrafts((d) => ({ ...d, data_quality: next as unknown as DataQualityConfig }))} />
                  </Section>
                ) : null}
              </div>

              <div className="col gap-3">
                <Section title="How saving works">
                  <p className="text-sm text-muted">Each section you change is stored as its own configuration version with your reason, user and timestamp (spec Phase 22). The scheduler picks the active version up on its next run; replanning stability rules limit how much a new version may move.</p>
                  {dirty ? (
                    <ul className="reason-list text-xs">
                      {dirtySections.map((s) => (
                        <li key={s}>
                          <span className="strong">{SECTION_LABELS[s]}</span>: {diffJson(active?.[s], drafts[s]).length} field(s) changed
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </Section>
                <ConfigVersionsPanel
                  versions={versions.data}
                  isPending={versions.isPending}
                  error={versions.error}
                  fetchVersion={fetchSchedulingConfigVersion}
                  queryKeyPrefix={["scheduling", "configuration"]}
                  extract={(cfg: SystemConfig) => ({ scheduling: cfg.scheduling, replanning: cfg.replanning, alerts: cfg.alerts, data_quality: cfg.data_quality })}
                  current={active ? { scheduling: active.scheduling, replanning: active.replanning, alerts: active.alerts, data_quality: active.data_quality } : null}
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
        title="Save new configuration version"
        description={`Saves ${dirtySections.map((s) => SECTION_LABELS[s]).join(", ")} as new version(s). Recorded in the audit log.`}
        submitLabel="Save version"
        busy={save.isPending}
        error={save.error}
        onSubmit={(reason) => void onSave(reason)}
        onClose={() => setSaveOpen(false)}
      />
    </div>
  );
}
