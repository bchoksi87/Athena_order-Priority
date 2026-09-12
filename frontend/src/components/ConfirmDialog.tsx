import { useState, type ReactNode } from "react";

import { Modal } from "./Modal";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  /** When set, a reason textarea is shown and required (audit trail). */
  requireReason?: boolean;
  busy?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

/** Confirmation dialog with an optional mandatory reason (every override is audited). */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  requireReason = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const canConfirm = !busy && (!requireReason || reason.trim().length >= 3);
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={() => onConfirm(reason.trim())}
            disabled={!canConfirm}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      {message ? <div className="mb-2">{message}</div> : null}
      {requireReason ? (
        <label className="field">
          <span className="label">Reason (recorded in the audit log)</span>
          <textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why?" />
        </label>
      ) : null}
    </Modal>
  );
}
