import type { CSSProperties, ReactNode } from "react";

import "./Layout.css";

export interface SectionProps {
  title?: ReactNode;
  count?: number | string;
  actions?: ReactNode;
  children: ReactNode;
  /** Remove body padding (for tables and charts that fill the panel). */
  flush?: boolean;
  scroll?: boolean;
  style?: CSSProperties;
  className?: string;
}

/** Bordered panel with an optional title row; the workhorse layout container. */
export function Section({ title, count, actions, children, flush = false, scroll = false, style, className }: SectionProps) {
  return (
    <section className={`section${className ? ` ${className}` : ""}`} style={style}>
      {title || actions ? (
        <div className="section-header">
          <div className="section-title">
            {typeof title === "string" ? <h3>{title}</h3> : title}
            {count !== undefined ? <span className="section-count">{count}</span> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      <div className={`section-body${flush ? " section-body-flush" : ""}${scroll ? " section-scroll" : ""}`}>{children}</div>
    </section>
  );
}
