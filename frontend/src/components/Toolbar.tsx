import type { ReactNode } from "react";

import "./Layout.css";

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function ToolbarSpacer() {
  return <div className="toolbar-spacer" />;
}

export function ToolbarGroup({ children }: { children: ReactNode }) {
  return <div className="toolbar-group">{children}</div>;
}
