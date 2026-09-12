import { useEffect, type ReactNode } from "react";

import "./Drawer.css";

export interface DrawerProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Extra controls rendered in the header next to the close button. */
  actions?: ReactNode;
  width?: number;
  footer?: ReactNode;
}

/** Non-modal right-hand slide-over: keeps the underlying table visible; Escape closes. */
export function Drawer({ open, title, onClose, children, actions, width = 520, footer }: DrawerProps) {
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
    <aside className="drawer" style={{ width: `min(${width}px, calc(100vw - 48px))` }} role="complementary" aria-label={typeof title === "string" ? title : "Details"} data-testid="drawer">
      <div className="drawer-header">
        <div className="drawer-title">{title}</div>
        <div className="row">
          {actions}
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close panel">
            ×
          </button>
        </div>
      </div>
      <div className="drawer-body">{children}</div>
      {footer ? <div className="drawer-footer">{footer}</div> : null}
    </aside>
  );
}
