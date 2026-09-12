import { useEffect, type ReactNode } from "react";

import "./Modal.css";

export interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  /** Render children without the body padding (the child supplies its own .modal-body / .modal-footer, e.g. a form). */
  flush?: boolean;
}

/** Lightweight dialog: closes on Escape and backdrop click. */
export function Modal({ open, title, onClose, children, footer, wide = false, flush = false }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={onClose} role="presentation">
      <div
        className={`modal${wide ? " modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {flush ? children : <div className="modal-body">{children}</div>}
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>
  );
}
