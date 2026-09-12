import { useEffect, useState, type ReactNode } from "react";

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

/** Modal for every audited action: mandatory reason, optional fields, API error envelope, busy state. */
export function ActionDialog({
  open,
  title,
  description,
  children,
  submitLabel = "Apply",
  danger = false,
  busy = false,
  validate,
  error,
  minReasonLength = 3,
  onSubmit,
  onClose,
  wide = false,
}: ActionDialogProps) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) {
      setReason("");
      setTouched(false);
    }
  }, [open]);

  const reasonError = reason.trim().length < minReasonLength ? `A reason of at least ${minReasonLength} characters is required (recorded in the audit log).` : null;
  const fieldError = validate ? validate() : null;
  const canSubmit = !busy && !reasonError && !fieldError;
  const api = isApiError(error) ? error : null;

  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      wide={wide}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={() => {
              setTouched(true);
              if (canSubmit) onSubmit(reason.trim());
            }}
            disabled={busy || Boolean(fieldError)}
            data-testid="action-submit"
          >
            {busy ? "Applying…" : submitLabel}
          </button>
        </>
      }
    >
      <form
        className="col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (canSubmit) onSubmit(reason.trim());
        }}
      >
        {description ? <div className="text-muted text-sm">{description}</div> : null}
        {children}
        {fieldError ? <div className="fld-msg fld-msg-error">{fieldError}</div> : null}
        <TextAreaField
          label="Reason"
          required
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
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
