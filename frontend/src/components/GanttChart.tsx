import { useMemo, useState, type MouseEvent } from "react";

import type { ScheduleEntry, TimeWindow } from "@/api/types";
import type { Tone } from "@/lib/constants";
import { formatHours, formatMinutes } from "@/lib/formatters";
import { entryTone } from "@/lib/entryTone";
import { formatDateTime, parseUtc } from "@/lib/time";
import { clampSpan, createTimeScale, generateTicks, ZOOM_PX_PER_HOUR, type GanttZoom } from "@/lib/timeScale";

import "./GanttChart.css";

export interface GanttRow {
  id: string;
  label: string;
  sublabel?: string;
}

export type GanttColorMode = "status" | "customer";

export interface GanttChartProps {
  rows: GanttRow[];
  entries: ScheduleEntry[];
  start: Date;
  end: Date;
  zoom?: GanttZoom;
  now?: Date;
  colorBy?: GanttColorMode;
  onEntryClick?: (entry: ScheduleEntry) => void;
  onRowClick?: (row: GanttRow) => void;
  selectedEntryId?: string | null;
  rowHeight?: number;
  labelWidth?: number;
  maxHeight?: number | string;
  /** Slack (hours) below which an on-time entry is flagged "at risk"; comes from SchedulingConfig. */
  atRiskSlackHours?: number;
  /** Optional downtime windows per row id, drawn hatched behind the bars. */
  downtime?: Record<string, TimeWindow[]>;
  showLegend?: boolean;
}

const HEADER_HEIGHT = 36;
const CATEGORY_COUNT = 8;

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function toneFill(tone: Tone): string {
  return `var(--status-${tone})`;
}

function customerFill(customerId: string | null): string {
  const idx = customerId ? (hashString(customerId) % CATEGORY_COUNT) + 1 : 1;
  return `var(--cat-${idx})`;
}

interface TooltipState {
  entry: ScheduleEntry;
  x: number;
  y: number;
}

/**
 * SVG Gantt: one row per machine, time axis with day/week zoom, entries as
 * bars coloured by status or customer, hatched setup blocks, downtime,
 * now-line, hover tooltip and click handler. Sticky labels + header inside a
 * scroll container keep 100+ rows navigable.
 */
export function GanttChart({
  rows,
  entries,
  start,
  end,
  zoom = "week",
  now = new Date(),
  colorBy = "status",
  onEntryClick,
  onRowClick,
  selectedEntryId = null,
  rowHeight = 28,
  labelWidth = 180,
  maxHeight = 640,
  atRiskSlackHours = 8,
  downtime,
  showLegend = true,
}: GanttChartProps) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const scale = useMemo(() => createTimeScale(start, end, ZOOM_PX_PER_HOUR[zoom]), [start, end, zoom]);
  const ticks = useMemo(() => generateTicks(scale, zoom), [scale, zoom]);
  const rowIndex = useMemo(() => new Map(rows.map((r, i) => [r.id, i])), [rows]);
  const bodyHeight = rows.length * rowHeight;
  const barHeight = Math.max(8, rowHeight - 10);
  const barY = (row: number) => row * rowHeight + (rowHeight - barHeight) / 2;

  const placed = useMemo(
    () =>
      entries.flatMap((entry) => {
        const row = rowIndex.get(entry.machine_id);
        if (row === undefined) return [];
        const setupStart = parseUtc(entry.setup_start) ?? parseUtc(entry.start);
        const runStart = parseUtc(entry.start);
        const runEnd = parseUtc(entry.end);
        if (!setupStart || !runStart || !runEnd) return [];
        const run = clampSpan(scale, runStart, runEnd);
        const setup = setupStart < runStart ? clampSpan(scale, setupStart, runStart) : null;
        if (!run && !setup) return [];
        return [{ entry, row, run, setup }];
      }),
    [entries, rowIndex, scale],
  );

  const nowX = now >= scale.start && now <= scale.end ? scale.x(now) : null;

  const onEnter = (entry: ScheduleEntry) => (e: MouseEvent<SVGRectElement>) => {
    setTooltip({ entry, x: e.clientX + 12, y: e.clientY + 12 });
  };
  const onMove = (e: MouseEvent<SVGRectElement>) => {
    setTooltip((prev) => (prev ? { ...prev, x: e.clientX + 12, y: e.clientY + 12 } : prev));
  };

  return (
    <div className="col gap-1">
      <div className="gantt" style={{ maxHeight }} data-testid="gantt">
        <div className="gantt-inner" style={{ width: labelWidth + scale.width }}>
          <div className="gantt-header" style={{ height: HEADER_HEIGHT }}>
            <div className="gantt-corner" style={{ width: labelWidth }}>
              {rows.length} machines
            </div>
            <svg className="gantt-axis" width={scale.width} height={HEADER_HEIGHT} role="presentation">
              {ticks.map((t, i) => (
                <g key={i} className={t.major ? "major" : "minor"} transform={`translate(${t.x},0)`}>
                  <line x1={0} x2={0} y1={t.major ? 0 : 20} y2={HEADER_HEIGHT} />
                  <text x={3} y={t.major ? 13 : 31}>
                    {t.label}
                  </text>
                </g>
              ))}
            </svg>
          </div>
          <div className="gantt-body">
            <div className="gantt-labels" style={{ width: labelWidth }}>
              {rows.map((r) => (
                <div
                  key={r.id}
                  className={`gantt-label${onRowClick ? " clickable" : ""}`}
                  style={{ height: rowHeight }}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  title={r.label}
                >
                  <span className="gantt-label-main">{r.label}</span>
                  {r.sublabel && rowHeight >= 26 ? <span className="gantt-label-sub">{r.sublabel}</span> : null}
                </div>
              ))}
            </div>
            <svg width={scale.width} height={Math.max(bodyHeight, 1)} role="img" aria-label="Schedule Gantt chart">
              <defs>
                <pattern id="gantt-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="6" height="6" fill="var(--bg-panel-raised)" />
                  <line x1="0" y1="0" x2="0" y2="6" stroke="var(--gantt-setup)" strokeWidth="2" />
                </pattern>
                <pattern id="gantt-downtime-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
                  <rect width="8" height="8" fill="var(--gantt-nonworking)" />
                  <line x1="0" y1="0" x2="0" y2="8" stroke="var(--status-late)" strokeWidth="1" opacity="0.5" />
                </pattern>
              </defs>
              {ticks.map((t, i) => (
                <line key={i} className={t.major ? "gantt-grid-major" : "gantt-grid-minor"} x1={t.x} x2={t.x} y1={0} y2={bodyHeight} />
              ))}
              {rows.map((r, i) => (
                <line key={r.id} className="gantt-row-line" x1={0} x2={scale.width} y1={(i + 1) * rowHeight} y2={(i + 1) * rowHeight} />
              ))}
              {downtime
                ? rows.flatMap((r, i) =>
                    (downtime[r.id] ?? []).flatMap((w, j) => {
                      const a = parseUtc(w.start);
                      const b = parseUtc(w.end);
                      if (!a || !b) return [];
                      const span = clampSpan(scale, a, b);
                      if (!span) return [];
                      return [
                        <rect key={`${r.id}-dt-${j}`} className="gantt-downtime" x={span.x} y={i * rowHeight} width={span.width} height={rowHeight}>
                          <title>{w.reason || "Downtime"}</title>
                        </rect>,
                      ];
                    }),
                  )
                : null}
              {placed.map(({ entry, row, run, setup }) => {
                const tone = entryTone(entry, now, atRiskSlackHours);
                const fill = colorBy === "customer" ? customerFill(entry.customer_id) : toneFill(tone);
                const y = barY(row);
                return (
                  <g key={entry.entry_id} data-testid="gantt-entry" data-entry-id={entry.entry_id} data-tone={tone}>
                    {setup ? <rect className="gantt-setup" x={setup.x} y={y} width={setup.width} height={barHeight} rx={1} /> : null}
                    {run ? (
                      <>
                        <rect
                          className={`gantt-bar${selectedEntryId === entry.entry_id ? " selected" : ""}`}
                          x={run.x}
                          y={y}
                          width={run.width}
                          height={barHeight}
                          rx={2}
                          fill={fill}
                          onClick={onEntryClick ? () => onEntryClick(entry) : undefined}
                          onMouseEnter={onEnter(entry)}
                          onMouseMove={onMove}
                          onMouseLeave={() => setTooltip(null)}
                        />
                        {run.width > 40 ? (
                          <text className="gantt-bar-label" x={run.x + 4} y={y + barHeight / 2 + 3.5}>
                            {entry.order_id.length * 6 > run.width - 8 ? entry.order_id.slice(0, Math.max(1, Math.floor((run.width - 8) / 6))) : entry.order_id}
                          </text>
                        ) : null}
                        {entry.locked && run.width > 14 ? (
                          <text className="gantt-lock" x={run.x + run.width - 10} y={y + barHeight / 2 + 3}>
                            🔒
                          </text>
                        ) : null}
                      </>
                    ) : null}
                  </g>
                );
              })}
              {nowX !== null ? (
                <g data-testid="gantt-now">
                  <line className="gantt-now" x1={nowX} x2={nowX} y1={0} y2={bodyHeight} />
                  <text className="gantt-now-label" x={nowX + 3} y={10}>
                    NOW
                  </text>
                </g>
              ) : null}
            </svg>
          </div>
        </div>
        {tooltip ? <GanttTooltip state={tooltip} /> : null}
      </div>
      {showLegend ? <GanttLegend colorBy={colorBy} /> : null}
    </div>
  );
}

function GanttTooltip({ state }: { state: TooltipState }) {
  const e = state.entry;
  return (
    <div className="gantt-tooltip" style={{ left: state.x, top: state.y }} role="tooltip">
      <strong>{e.order_id}</strong> · op {e.operation_id} · seq {e.sequence_on_machine}
      <dl className="kv">
        <dt>Machine</dt>
        <dd>{e.machine_id}</dd>
        <dt>Setup</dt>
        <dd>
          {formatDateTime(e.setup_start, "dd MMM HH:mm")} · {formatMinutes(e.setup_minutes)}
        </dd>
        <dt>Run</dt>
        <dd>
          {formatDateTime(e.start, "dd MMM HH:mm")} → {formatDateTime(e.end, "dd MMM HH:mm")} · {formatMinutes(e.run_minutes)}
        </dd>
        <dt>Qty</dt>
        <dd className="num">{e.quantity}</dd>
        <dt>Priority</dt>
        <dd className="num">{Math.round(e.priority_score)}</dd>
        {e.due_date ? (
          <>
            <dt>Due</dt>
            <dd>
              {formatDateTime(e.due_date, "dd MMM HH:mm")}
              {e.expected_lateness_hours !== null && e.expected_lateness_hours > 0 ? ` · late ${formatHours(e.expected_lateness_hours)}` : ""}
            </dd>
          </>
        ) : null}
        {e.customer_id ? (
          <>
            <dt>Customer</dt>
            <dd>{e.customer_id}</dd>
          </>
        ) : null}
        <dt>Why here</dt>
        <dd>{e.placement_reason}</dd>
      </dl>
    </div>
  );
}

function GanttLegend({ colorBy }: { colorBy: GanttColorMode }) {
  if (colorBy === "customer") {
    return <div className="gantt-legend">Colour: one hue per customer · hatched = setup / changeover · dashed red = now</div>;
  }
  const items: Array<[Tone, string]> = [
    ["running", "Scheduled"],
    ["at-risk", "At risk"],
    ["late", "Late"],
    ["hold", "Locked"],
    ["done", "Completed / past"],
  ];
  return (
    <div className="gantt-legend">
      {items.map(([tone, label]) => (
        <span key={tone}>
          <span className="gantt-legend-swatch" style={{ background: toneFill(tone) }} />
          {label}
        </span>
      ))}
      <span>
        <span className="gantt-legend-swatch" style={{ background: "repeating-linear-gradient(45deg, var(--gantt-setup) 0 2px, transparent 2px 5px)" }} />
        Setup
      </span>
      <span>dashed red = now</span>
    </div>
  );
}
