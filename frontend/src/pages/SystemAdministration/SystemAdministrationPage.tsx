/** System Administration (admin): users, environment/health, configuration links, sync controls (placeholder until the endpoints exist). */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { describeError, isApiError } from "@/api/client";
import { useSchedulingConfiguration } from "@/api/config";
import { usePriorityConfiguration } from "@/api/priority";
import { useHealth, useMetrics, useRunSync, useSyncRuns } from "@/api/system";
import { useCreateUser, useResetUserPassword, useSetUserActive, useUsers } from "@/api/users";
import type { Role, SyncRun, UserAccount } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ActionDialog } from "@/components/ActionDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SelectField, TextField } from "@/components/Field";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { ROLES, ROLE_LABELS, WRITEBACK_LABELS } from "@/lib/constants";
import { formatDateTime, formatRelative } from "@/lib/time";

const syncColumns: Column<SyncRun>[] = [
  { key: "started", header: "Started", cell: (r) => <span className="num">{formatDateTime(r.started_at, "dd MMM HH:mm:ss")}</span>, sortValue: (r) => r.started_at },
  { key: "mode", header: "Mode", cell: (r) => r.mode },
  { key: "status", header: "Status", cell: (r) => <StatusPill tone={r.status === "succeeded" || r.status === "success" ? "ready" : r.status === "failed" ? "late" : "running"} size="sm">{r.status}</StatusPill> },
  { key: "finished", header: "Finished", cell: (r) => <span className="num">{formatDateTime(r.finished_at, "HH:mm:ss")}</span> },
  { key: "fetched", header: "Fetched", numeric: true, cell: (r) => r.records_fetched ?? "—" },
  { key: "upserted", header: "Upserted", numeric: true, cell: (r) => r.records_upserted ?? "—" },
  { key: "errors", header: "Errors", cell: (r) => (r.errors?.length ? <span className="tone-late">{r.errors.length}: {r.errors[0]}</span> : (r.message ?? "—")) },
];

type UserDialog = { kind: "create" } | { kind: "toggle"; user: UserAccount } | { kind: "reset"; user: UserAccount } | null;

function CreateUserForm({ value, onChange }: { value: { username: string; password: string; role: Role; display_name: string; email: string }; onChange: (v: typeof value) => void }) {
  return (
    <div className="grid grid-2">
      <TextField label="Username" value={value.username} onChange={(v) => onChange({ ...value, username: v })} autoComplete="off" required mono />
      <TextField label="Display name" value={value.display_name} onChange={(v) => onChange({ ...value, display_name: v })} required />
      <TextField label="Password" type="password" value={value.password} onChange={(v) => onChange({ ...value, password: v })} autoComplete="new-password" required help="8–72 characters" />
      <SelectField label="Role" value={value.role} options={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))} onChange={(v) => onChange({ ...value, role: v })} />
      <TextField label="Email (optional)" type="email" value={value.email} onChange={(v) => onChange({ ...value, email: v })} />
    </div>
  );
}

export default function SystemAdministrationPage() {
  const toast = useToast();
  const { user, hasMinRole } = useAuth();
  const isAdmin = hasMinRole("admin");
  const now = useMemo(() => new Date(), []);
  const health = useHealth(15_000);
  const metrics = useMetrics(30_000);
  const syncRuns = useSyncRuns(isAdmin);
  const runSync = useRunSync();
  const priority = usePriorityConfiguration();
  const scheduling = useSchedulingConfiguration();
  const users = useUsers(false, isAdmin);
  const createUser = useCreateUser();
  const setActive = useSetUserActive();
  const resetPassword = useResetUserPassword();
  const [dialog, setDialog] = useState<UserDialog>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "operator" as Role, display_name: "", email: "" });
  const [newPassword, setNewPassword] = useState("");

  const syncUnavailable = syncRuns.isError && isApiError(syncRuns.error) && syncRuns.error.isNotFound;

  const onSync = async (mode: "full" | "incremental") => {
    try {
      const r = await runSync.mutateAsync(mode);
      toast.push({ tone: "success", title: `${mode} sync ${r.status}`, message: r.message ?? undefined });
    } catch (err) {
      toast.push({ tone: "error", title: "Sync failed", message: isApiError(err) && err.isNotFound ? "The sync endpoints are not available in this backend build yet (POST /sync/run)." : describeError(err) });
    }
  };

  const userColumns = useMemo<Column<UserAccount>[]>(
    () => [
      { key: "username", header: "Username", cell: (u) => <span className="mono strong">{u.username}</span>, sortValue: (u) => u.username, filterValue: (u) => u.username },
      { key: "name", header: "Display name", cell: (u) => u.display_name, sortValue: (u) => u.display_name, filterValue: (u) => u.display_name },
      { key: "role", header: "Role", cell: (u) => <StatusPill tone={u.role === "admin" ? "late" : u.role === "executive" ? "neutral" : "running"} size="sm" dot={false}>{ROLE_LABELS[u.role]}</StatusPill>, sortValue: (u) => u.role, filterValue: (u) => u.role },
      { key: "email", header: "Email", cell: (u) => u.email ?? <span className="text-faint">—</span> },
      { key: "active", header: "State", cell: (u) => (u.active ? <StatusPill tone="ready" size="sm">active</StatusPill> : <StatusPill tone="neutral" size="sm">deactivated</StatusPill>), sortValue: (u) => (u.active ? 1 : 0) },
      { key: "login", header: "Last login", cell: (u) => <span className="num">{u.last_login_at ? formatRelative(u.last_login_at, now) : "never"}</span>, sortValue: (u) => u.last_login_at ?? "" },
      { key: "created", header: "Created", cell: (u) => <span className="num">{formatDateTime(u.created_at, "dd MMM yyyy")}</span>, sortValue: (u) => u.created_at ?? "" },
      {
        key: "act",
        header: "",
        align: "right",
        cell: (u) => (
          <span className="row gap-1" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-sm" onClick={() => { setNewPassword(""); setDialog({ kind: "reset", user: u }); }}>
              Reset password
            </button>
            <button type="button" className={`btn btn-sm${u.active ? " btn-ghost" : ""}`} onClick={() => setDialog({ kind: "toggle", user: u })} disabled={u.user_id === user?.user_id && u.active} title={u.user_id === user?.user_id ? "You cannot deactivate your own account" : undefined}>
              {u.active ? "Deactivate" : "Activate"}
            </button>
          </span>
        ),
      },
    ],
    [now, user?.user_id],
  );

  const submitUserDialog = async (reason: string) => {
    if (!dialog) return;
    try {
      if (dialog.kind === "create") {
        const created = await createUser.mutateAsync({ username: form.username.trim(), password: form.password, role: form.role, display_name: form.display_name.trim(), email: form.email.trim() || null, reason });
        toast.push({ tone: "success", title: `User ${created.username} created`, message: ROLE_LABELS[created.role] });
        setForm({ username: "", password: "", role: "operator", display_name: "", email: "" });
      } else if (dialog.kind === "toggle") {
        const updated = await setActive.mutateAsync({ userId: dialog.user.user_id, active: !dialog.user.active, reason });
        toast.push({ tone: "success", title: `${updated.username} ${updated.active ? "activated" : "deactivated"}` });
      } else {
        await resetPassword.mutateAsync({ userId: dialog.user.user_id, password: newPassword, reason });
        toast.push({ tone: "success", title: `Password reset for ${dialog.user.username}` });
      }
      setDialog(null);
    } catch (err) {
      toast.push({ tone: "error", title: "User change failed", message: describeError(err) });
    }
  };

  const validateUserDialog = (): string | null => {
    if (!dialog) return null;
    if (dialog.kind === "create") {
      if (!form.username.trim()) return "Username is required.";
      if (form.password.length < 8) return "Password must be at least 8 characters.";
      if (!form.display_name.trim()) return "Display name is required.";
    }
    if (dialog.kind === "reset" && newPassword.length < 8) return "Password must be at least 8 characters.";
    return null;
  };

  const m = metrics.data;
  const dialogBusy = createUser.isPending || setActive.isPending || resetPassword.isPending;
  const dialogError = dialog?.kind === "create" ? createUser.error : dialog?.kind === "toggle" ? setActive.error : resetPassword.error;

  return (
    <div className="page" data-testid="system-administration-page">
      <PageHeader
        eyebrow="System"
        title="System Administration"
        subtitle={`Signed in as ${user?.display_name ?? "—"} (${user ? ROLE_LABELS[user.role] : "—"})`}
        actions={
          <>
            <button type="button" className="btn" onClick={() => void onSync("incremental")} disabled={runSync.isPending || syncUnavailable} title={syncUnavailable ? "POST /sync/run is not available in this backend build" : undefined}>
              Incremental sync
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void onSync("full")} disabled={runSync.isPending || syncUnavailable} title={syncUnavailable ? "POST /sync/run is not available in this backend build" : undefined}>
              {runSync.isPending ? "Syncing…" : "Full ERP sync"}
            </button>
          </>
        }
      />
      <div className="grid grid-kpi">
        <KpiCard label="API" value={health.data?.status ?? (health.isError ? "down" : "…")} tone={health.data?.status === "ok" ? "ready" : "late"} hint={health.data ? `v${health.data.version} · ${health.data.environment}` : undefined} loading={health.isPending} />
        <KpiCard label="Database" value={health.data?.database ?? "—"} tone={health.data?.database === "ok" ? "ready" : "late"} hint={health.data ? `server time ${formatDateTime(health.data.time, "HH:mm:ss")}` : undefined} loading={health.isPending} />
        <KpiCard label="Environment" value={health.data?.environment ?? "—"} tone="neutral" hint={typeof import.meta.env.VITE_APP_ENV === "string" ? `UI build ${import.meta.env.VITE_APP_ENV}` : undefined} loading={health.isPending} />
        <KpiCard label="Writeback mode" value={WRITEBACK_LABELS.read_only} tone="at-risk" hint="ERP is never written in read-only mode (deployment setting; not exposed by /health)" />
        <KpiCard label="Requests" value={typeof m?.requests_total === "number" ? m.requests_total : "—"} tone="neutral" hint={typeof m?.errors_total === "number" ? `${m.errors_total} errors (5xx)` : undefined} loading={metrics.isPending} />
        <KpiCard label="Priority profile" value={priority.data ? `v${priority.data.profile.version}` : "—"} tone="neutral" hint={priority.data ? `${priority.data.profile.profile_id} · config v${priority.data.version?.version ?? "?"}` : undefined} loading={priority.isPending} />
        <KpiCard label="Scheduling config" value={scheduling.data ? `v${scheduling.data.scheduling.version}` : "—"} tone="neutral" hint={scheduling.data ? `${scheduling.data.scheduling.algorithm} · ${scheduling.data.scheduling.horizon_days}d horizon` : undefined} loading={scheduling.isPending} />
      </div>

      <div className="grid grid-main-side">
        <div className="col gap-3">
          <Section
            title="Users"
            count={users.data?.length}
            actions={
              isAdmin ? (
                <button type="button" className="btn btn-sm btn-primary" onClick={() => setDialog({ kind: "create" })}>
                  New user
                </button>
              ) : null
            }
            flush
          >
            {!isAdmin ? (
              <EmptyState compact title="Admin only" message="User management requires the administrator role." />
            ) : users.isPending ? (
              <LoadingState compact label="Loading users" />
            ) : users.isError ? (
              <ErrorState compact error={users.error} onRetry={() => void users.refetch()} />
            ) : (
              <DataTable rows={users.data} columns={userColumns} rowKey={(u) => u.user_id} initialSort={{ key: "username", direction: "asc" }} filters dense pageSize={100} ariaLabel="Users" />
            )}
          </Section>

          <Section title="ERP sync runs" count={syncRuns.data?.length} flush>
            {syncUnavailable ? (
              <EmptyState compact title="Sync endpoints not available yet" message="GET /sync/runs and POST /sync/run (contract §9) are not exposed by this backend build. Sync is driven by `python -m app.cli sync` and the worker until they land; this panel will list runs automatically once the endpoints exist." />
            ) : syncRuns.isPending ? (
              <LoadingState compact label="Loading sync runs" />
            ) : syncRuns.isError ? (
              <ErrorState compact error={syncRuns.error} onRetry={() => void syncRuns.refetch()} />
            ) : syncRuns.data.length === 0 ? (
              <EmptyState compact title="No sync runs yet" message="Trigger a full sync to load master data through the connector." />
            ) : (
              <DataTable rows={syncRuns.data} columns={syncColumns} rowKey={(r) => r.run_id} initialSort={{ key: "started", direction: "desc" }} dense pageSize={20} />
            )}
          </Section>
        </div>

        <div className="col gap-3">
          <Section title="Configuration">
            <div className="col gap-1 text-sm">
              <Link to={routes.priorityConfig} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Priority configuration</span>
                <span className="text-faint">{priority.data ? `${priority.data.profile.name} · v${priority.data.profile.version}` : ""}</span>
              </Link>
              <Link to={routes.schedulingConfig} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Scheduling configuration</span>
                <span className="text-faint">{scheduling.data ? `${scheduling.data.scheduling.name} · v${scheduling.data.scheduling.version}` : ""}</span>
              </Link>
              <Link to={routes.dataQuality} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Data quality</span>
              </Link>
              <Link to={routes.audit} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Audit log</span>
              </Link>
            </div>
          </Section>
          <Section title="Roles">
            <p className="text-muted text-xs">Precedence for "role or higher": executive (read-only) &lt; operator &lt; supervisor &lt; planner &lt; production manager &lt; admin. Executives may read every dashboard but never write.</p>
            <dl className="kv">
              {ROLES.map((r) => (
                <div key={r} style={{ display: "contents" }}>
                  <dt className="mono">{r}</dt>
                  <dd>{ROLE_LABELS[r]}</dd>
                </div>
              ))}
            </dl>
          </Section>
          {m ? (
            <Section title="API metrics" count="in-process">
              <dl className="kv">
                {Object.entries((m.by_status as Record<string, number> | undefined) ?? {}).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt className="mono">{k}</dt>
                    <dd className="num">{v}</dd>
                  </div>
                ))}
              </dl>
            </Section>
          ) : null}
        </div>
      </div>

      <ActionDialog
        open={dialog !== null}
        title={dialog?.kind === "create" ? "Create user" : dialog?.kind === "toggle" ? `${dialog.user.active ? "Deactivate" : "Activate"} ${dialog.user.username}` : dialog?.kind === "reset" ? `Reset password for ${dialog.user.username}` : ""}
        description={dialog?.kind === "toggle" ? (dialog.user.active ? "A deactivated account cannot sign in; existing tokens expire normally." : "The account can sign in again.") : dialog?.kind === "reset" ? "The user must use the new password from now on." : "The new account can sign in immediately with the role's permissions."}
        submitLabel={dialog?.kind === "create" ? "Create" : dialog?.kind === "toggle" ? (dialog.user.active ? "Deactivate" : "Activate") : "Reset password"}
        danger={dialog?.kind === "toggle" && dialog.user.active}
        busy={dialogBusy}
        error={dialogError}
        validate={validateUserDialog}
        minReasonLength={dialog?.kind === "create" ? 0 : 3}
        onSubmit={(reason) => void submitUserDialog(reason)}
        onClose={() => setDialog(null)}
      >
        {dialog?.kind === "create" ? <CreateUserForm value={form} onChange={setForm} /> : null}
        {dialog?.kind === "reset" ? <TextField label="New password" type="password" value={newPassword} onChange={setNewPassword} autoComplete="new-password" required help="8–72 characters" /> : null}
      </ActionDialog>
    </div>
  );
}
