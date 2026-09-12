/** User accounts (admin): list, create, activate/deactivate and reset password — every change audited with a reason. */
import { useMemo, useState } from "react";

import { describeError } from "@/api/client";
import type { Role, UserAccount } from "@/api/types";
import { useCreateUser, useResetUserPassword, useSetUserActive, useUsers } from "@/api/users";
import { useAuth } from "@/app/auth";
import { ActionDialog } from "@/components/ActionDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SelectField, TextField } from "@/components/Field";
import { LoadingState } from "@/components/LoadingState";
import { Section } from "@/components/Section";
import { StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { ROLES, ROLE_LABELS } from "@/lib/constants";
import { formatDateTime, formatRelative } from "@/lib/time";

type UserDialog = { kind: "create" } | { kind: "toggle"; user: UserAccount } | { kind: "reset"; user: UserAccount } | null;

interface CreateForm {
  username: string;
  password: string;
  role: Role;
  display_name: string;
  email: string;
}

const EMPTY_FORM: CreateForm = { username: "", password: "", role: "operator", display_name: "", email: "" };

function CreateUserForm({ value, onChange }: { value: CreateForm; onChange: (v: CreateForm) => void }) {
  return (
    <div className="grid grid-2">
      <TextField label="Username" value={value.username} onChange={(v) => onChange({ ...value, username: v })} autoComplete="off" required mono help="1–64 characters, unique" />
      <TextField label="Display name" value={value.display_name} onChange={(v) => onChange({ ...value, display_name: v })} required />
      <TextField label="Password" type="password" value={value.password} onChange={(v) => onChange({ ...value, password: v })} autoComplete="new-password" required help="8–72 characters" />
      <SelectField label="Role" value={value.role} options={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))} onChange={(v) => onChange({ ...value, role: v })} help="executive < operator < supervisor < planner < production manager < admin" />
      <TextField label="Email (optional)" type="email" value={value.email} onChange={(v) => onChange({ ...value, email: v })} />
    </div>
  );
}

const ROLE_TONE: Record<Role, "late" | "blocked" | "running" | "ready" | "neutral" | "hold"> = {
  admin: "late",
  production_manager: "blocked",
  planner: "running",
  supervisor: "ready",
  operator: "neutral",
  executive: "hold",
};

export function UsersPanel({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const { user } = useAuth();
  const now = useMemo(() => new Date(), []);
  const users = useUsers(false, enabled);
  const createUser = useCreateUser();
  const setActive = useSetUserActive();
  const resetPassword = useResetUserPassword();
  const [dialog, setDialog] = useState<UserDialog>(null);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [newPassword, setNewPassword] = useState("");

  const columns = useMemo<Column<UserAccount>[]>(
    () => [
      { key: "username", header: "Username", cell: (u) => <span className="mono strong">{u.username}</span>, sortValue: (u) => u.username, filterValue: (u) => u.username },
      { key: "name", header: "Display name", cell: (u) => u.display_name, sortValue: (u) => u.display_name, filterValue: (u) => u.display_name },
      { key: "role", header: "Role", cell: (u) => <StatusPill tone={ROLE_TONE[u.role]} size="sm" dot={false}>{ROLE_LABELS[u.role]}</StatusPill>, sortValue: (u) => u.role, filterValue: (u) => u.role },
      { key: "email", header: "Email", cell: (u) => u.email ?? <span className="text-faint">—</span>, filterValue: (u) => u.email ?? "" },
      { key: "active", header: "State", cell: (u) => (u.active ? <StatusPill tone="ready" size="sm">active</StatusPill> : <StatusPill tone="neutral" size="sm">deactivated</StatusPill>), sortValue: (u) => (u.active ? 1 : 0) },
      { key: "login", header: "Last login", cell: (u) => <span className="num text-nowrap" title={u.last_login_at ? formatDateTime(u.last_login_at) : undefined}>{u.last_login_at ? formatRelative(u.last_login_at, now) : "never"}</span>, sortValue: (u) => u.last_login_at ?? "" },
      { key: "created", header: "Created", cell: (u) => <span className="num">{formatDateTime(u.created_at, "dd MMM yyyy")}</span>, sortValue: (u) => u.created_at ?? "" },
      {
        key: "act",
        header: "",
        align: "right",
        cell: (u) => {
          const self = u.user_id === user?.user_id;
          return (
            <span className="row gap-1" style={{ justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  setNewPassword("");
                  setDialog({ kind: "reset", user: u });
                }}
              >
                Reset password
              </button>
              <button type="button" className={`btn btn-sm${u.active ? " btn-ghost" : ""}`} onClick={() => setDialog({ kind: "toggle", user: u })} disabled={self && u.active} title={self ? "You cannot deactivate your own account" : undefined}>
                {u.active ? "Deactivate" : "Activate"}
              </button>
            </span>
          );
        },
      },
    ],
    [now, user?.user_id],
  );

  const submit = async (reason: string) => {
    if (!dialog) return;
    try {
      if (dialog.kind === "create") {
        const created = await createUser.mutateAsync({ username: form.username.trim(), password: form.password, role: form.role, display_name: form.display_name.trim(), email: form.email.trim() || null, reason: reason || null });
        toast.push({ tone: "success", title: `User ${created.username} created`, message: ROLE_LABELS[created.role] });
        setForm(EMPTY_FORM);
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

  const validate = (): string | null => {
    if (!dialog) return null;
    if (dialog.kind === "create") {
      if (!form.username.trim()) return "Username is required.";
      if (form.password.length < 8 || form.password.length > 72) return "Password must be 8–72 characters.";
      if (!form.display_name.trim()) return "Display name is required.";
    }
    if (dialog.kind === "reset" && (newPassword.length < 8 || newPassword.length > 72)) return "Password must be 8–72 characters.";
    return null;
  };

  const busy = createUser.isPending || setActive.isPending || resetPassword.isPending;
  const error = dialog?.kind === "create" ? createUser.error : dialog?.kind === "toggle" ? setActive.error : resetPassword.error;
  const activeCount = users.data?.filter((u) => u.active).length ?? 0;

  return (
    <>
      <Section
        title="Users"
        count={users.data ? `${activeCount} active · ${users.data.length - activeCount} deactivated` : undefined}
        actions={
          enabled ? (
            <button type="button" className="btn btn-sm btn-primary" onClick={() => setDialog({ kind: "create" })}>
              New user
            </button>
          ) : null
        }
        flush
      >
        {!enabled ? (
          <EmptyState compact title="Admin only" message="User management requires the administrator role." />
        ) : users.isPending ? (
          <LoadingState compact label="Loading users" />
        ) : users.isError ? (
          <ErrorState compact error={users.error} onRetry={() => void users.refetch()} />
        ) : (
          <DataTable rows={users.data} columns={columns} rowKey={(u) => u.user_id} initialSort={{ key: "username", direction: "asc" }} rowClassName={(u) => (u.active ? undefined : "row-done")} filters dense pageSize={100} ariaLabel="Users" />
        )}
      </Section>

      <ActionDialog
        open={dialog !== null}
        title={dialog?.kind === "create" ? "Create user" : dialog?.kind === "toggle" ? `${dialog.user.active ? "Deactivate" : "Activate"} ${dialog.user.username}` : dialog?.kind === "reset" ? `Reset password for ${dialog.user.username}` : ""}
        description={dialog?.kind === "toggle" ? (dialog.user.active ? "A deactivated account cannot sign in; existing tokens expire normally." : "The account can sign in again.") : dialog?.kind === "reset" ? "The user must use the new password from now on." : "The new account can sign in immediately with the role's permissions. The reason is optional for user creation."}
        submitLabel={dialog?.kind === "create" ? "Create" : dialog?.kind === "toggle" ? (dialog.user.active ? "Deactivate" : "Activate") : "Reset password"}
        danger={dialog?.kind === "toggle" && dialog.user.active}
        busy={busy}
        error={error}
        validate={validate}
        minReasonLength={dialog?.kind === "create" ? 0 : 3}
        onSubmit={(reason) => void submit(reason)}
        onClose={() => setDialog(null)}
      >
        {dialog?.kind === "create" ? <CreateUserForm value={form} onChange={setForm} /> : null}
        {dialog?.kind === "reset" ? <TextField label="New password" type="password" value={newPassword} onChange={setNewPassword} autoComplete="new-password" required help="8–72 characters" /> : null}
      </ActionDialog>
    </>
  );
}
