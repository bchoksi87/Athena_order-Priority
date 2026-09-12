/** Versions list for the configuration screens: diff summary vs. the active version, activate/rollback with reason. */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import type { ApiClient } from "@/api/client";
import { useApiClient } from "@/api/context";
import type { ConfigVersionInfo, SystemConfig, SystemConfigVersionResponse } from "@/api/types";
import { ActionDialog } from "@/components/ActionDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { JsonDiff } from "@/components/JsonDiff";
import { LoadingState } from "@/components/LoadingState";
import { Modal } from "@/components/Modal";
import { Section } from "@/components/Section";
import { StatusPill } from "@/components/StatusPill";
import { diffJson } from "@/lib/diff";
import { formatDateTime } from "@/lib/time";

export interface ConfigVersionsPanelProps {
  title?: string;
  versions: ConfigVersionInfo[] | undefined;
  isPending: boolean;
  error: unknown;
  /** Loads one version (GET …/versions/{v}). */
  fetchVersion: (client: ApiClient, version: number) => Promise<SystemConfigVersionResponse>;
  queryKeyPrefix: readonly unknown[];
  /** Section(s) of the SystemConfig this screen edits, for the diff summary. */
  extract: (config: SystemConfig) => unknown;
  /** The currently active value of the same section(s). */
  current: unknown;
  canActivate: boolean;
  activating: boolean;
  activateError?: unknown;
  onActivate: (version: number, reason: string) => Promise<void>;
}

export function ConfigVersionsPanel({ title = "Versions", versions, isPending, error, fetchVersion, queryKeyPrefix, extract, current, canActivate, activating, activateError, onActivate }: ConfigVersionsPanelProps) {
  const client = useApiClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [activate, setActivate] = useState<ConfigVersionInfo | null>(null);
  const detail = useQuery({
    queryKey: [...queryKeyPrefix, "version", selected ?? 0],
    queryFn: () => fetchVersion(client, selected ?? 0),
    enabled: selected !== null,
  });
  const diff = useMemo(() => (detail.data ? diffJson(current, extract(detail.data.config)) : []), [detail.data, current, extract]);

  const columns = useMemo<Column<ConfigVersionInfo>[]>(
    () => [
      { key: "v", header: "Version", width: 70, numeric: true, cell: (v) => <span className="strong">v{v.version}</span>, sortValue: (v) => v.version },
      { key: "state", header: "", width: 70, cell: (v) => (v.is_active ? <StatusPill tone="ready" size="sm">active</StatusPill> : null) },
      { key: "at", header: "Created", cell: (v) => <span className="num text-nowrap">{formatDateTime(v.created_at, "dd MMM HH:mm")}</span>, sortValue: (v) => v.created_at ?? "" },
      { key: "by", header: "By", cell: (v) => v.created_by ?? <span className="text-faint">system</span> },
      { key: "reason", header: "Reason", cell: (v) => <span className="truncate" style={{ maxWidth: 220, display: "inline-block" }} title={v.reason ?? undefined}>{v.reason ?? "—"}</span> },
      {
        key: "act",
        header: "",
        align: "right",
        cell: (v) =>
          canActivate && !v.is_active ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                setActivate(v);
              }}
            >
              Activate
            </button>
          ) : null,
      },
    ],
    [canActivate],
  );

  return (
    <>
      <Section title={title} count={versions?.length} flush>
        {isPending ? (
          <LoadingState compact label="Loading versions" />
        ) : error ? (
          <ErrorState compact error={error} />
        ) : !versions || versions.length === 0 ? (
          <EmptyState compact title="No versions" />
        ) : (
          <DataTable rows={versions} columns={columns} rowKey={(v) => String(v.version)} initialSort={{ key: "v", direction: "desc" }} onRowClick={(v) => setSelected(v.version)} selectedKey={selected === null ? null : String(selected)} dense pageSize={25} />
        )}
      </Section>

      <Modal open={selected !== null} title={`Version ${selected ?? ""} vs. active`} onClose={() => setSelected(null)} wide>
        {detail.isPending ? (
          <LoadingState compact label="Loading version" />
        ) : detail.isError ? (
          <ErrorState compact error={detail.error} />
        ) : detail.data ? (
          <div className="col gap-3">
            <dl className="kv">
              <dt>Created</dt>
              <dd className="num">{formatDateTime(detail.data.version.created_at)}</dd>
              <dt>By</dt>
              <dd>{detail.data.version.created_by ?? "system"}</dd>
              <dt>Reason</dt>
              <dd>{detail.data.version.reason ?? "—"}</dd>
              <dt>Differences</dt>
              <dd>{diff.length === 0 ? "identical to the active configuration" : `${diff.length} field${diff.length > 1 ? "s" : ""} differ (active → this version)`}</dd>
            </dl>
            <JsonDiff before={current} after={extract(detail.data.config)} />
            {canActivate && !detail.data.version.is_active ? (
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button type="button" className="btn btn-primary" onClick={() => setActivate(detail.data.version)}>
                  Activate v{detail.data.version.version}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </Modal>

      <ActionDialog
        open={activate !== null}
        title={activate ? `Activate configuration v${activate.version}` : ""}
        description="Rolls the active configuration back to this version. The priority engine and scheduler use it from the next run; recorded in the audit log."
        submitLabel="Activate"
        busy={activating}
        error={activateError}
        onSubmit={(reason) => {
          if (!activate) return;
          void onActivate(activate.version, reason)
            .then(() => {
              setActivate(null);
              setSelected(null);
            })
            .catch(() => undefined);
        }}
        onClose={() => setActivate(null)}
      />
    </>
  );
}
