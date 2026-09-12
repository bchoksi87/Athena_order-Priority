import { useState, type ReactNode } from "react";

import { describeError, isApiError } from "@/api/client";

import { Modal } from "./Modal";
import { TextAreaField } from "./Field";

export interface ActionDialogProps {
  open: boolean;
  title: string;
  /** Explanation of what the action does and that it is audited. */
  description?: ReactNode;
  /** Extra form controls rendered above the reason. */
  children?: ReactNode;
  submitLabel?: string;
  danger?: boolean;
  busy?: boolean;
  /** Extra validation of the child fields; a string blocks submit and is shown. */
  validate?: () => string | null;
  /** Error thrown by the last submit (rendered with the API error envelope). */
  error?: unknown;
  /** Minimum reason length (backend requires a non-blank reason). */
  minReasonLength?: number;
  onSubmit: (reason: string) => void;
  onClose: () => void;
  wide?: boolean;
}

interface ActionFormProps extends Omit<ActionDialogProps, "open" | "title" | "onClose" | "wide"> {
  onCancel: () => void;
}

/** The form itself; mounted only while the dialog is open so the reason resets on every open. */
function ActionForm({ description, children, submitLabel = "Apply", danger = false, busy = false, validate, error, minReasonLength = 3, onSubmit, onCancel }: ActionFormProps) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  const reasonError = reason.trim().length < minReasonLength ? `A reason of at least ${minReasonLength} characters is required (recorded in the audit log).` : null;
  const fieldError = validate ? validate() : null;
  const canSubmit = !busy && !reasonError && !fieldError;
  const api = isApiError(error) ? error : null;

  const submit = () => {
    setTouched(true);
    if (canSubmit) onSubmit(reason.trim());
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="modal-body col gap-3">
      {description ? <div className="text-muted text-sm">{description}</div> : null}
      {children}
      {fieldError ? <div className="fld-msg fld-msg-error">{fieldError}</div> : null}
      <TextAreaField
        label="Reason"
        required={minReasonLength > 0}
        value={reason}
        onChange={(v) => {
          setReason(v);
          setTouched(true);
        }}
        placeholder="Why is this action needed? (visible in the audit log)"
        error={touched ? reasonError : null}
        help="Recorded with your user, the timestamp, the previous and the new value."
      />
      {error ? (
        <div className="state state-error state-compact" role="alert">
          <div className="state-title">{api?.isForbidden ? "Not authorised" : "Action failed"}</div>
          <div className="state-message">{describeError(error)}</div>
          {api ? (
            <div className="state-code">
              {api.code} · HTTP {api.status}
              {Object.keys(api.details).length > 0 ? ` · ${JSON.stringify(api.details).slice(0, 200)}` : ""}
            </div>
          ) : null}
        </div>
      ) : null}
      </div>
      <div className="modal-footer">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={busy || Boolean(fieldError)} data-testid="action-submit">
          {busy ? "Applying…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

/** Modal for every audited action: mandatory reason, optional fields, API error envelope, busy state. */
export function ActionDialog({ open, title, onClose, wide = false, ...form }: ActionDialogProps) {
  return (
    <Modal open={open} title={title} onClose={onClose} wide={wide} flush>
      {open ? <ActionForm {...form} onCancel={onClose} /> : null}
    </Modal>
  );
}
