import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";

import type { ScheduleEntry, TimeWindow } from "@/api/types";
import type { Tone } from "@/lib/constants";
import { humanize } from "@/lib/constants";
import { formatHours, formatMinutes } from "@/lib/formatters";
import { entryTone } from "@/lib/entryTone";
import { formatDateTime, parseUtc } from "@/lib/time";
import { clampSpan, createTimeScale, generateTicks, ZOOM_PX_PER_HOUR, type GanttZoom } from "@/lib/timeScale";

import "./GanttChart.css";

export interface GanttRow {
  id: string;
  label: string;
  sublabel?: string;
  /** Rows sharing a group get a group header band (machine group). */
  group?: string;
}

/** Extra facts per entry id shown in the tooltip (customer / part come from the Gantt view endpoint). */
export interface GanttEntryMeta {
  customer_name?: string | null;
  part_id?: string | null;
  part_name?: string | null;
  order_status?: string | null;
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
  /** Click on an aggregated cluster of tiny bars (low zoom); receives its entries. */
  onClusterClick?: (entries: ScheduleEntry[]) => void;
  onRowClick?: (row: GanttRow) => void;
  selectedEntryId?: string | null;
  rowHeight?: number;
  labelWidth?: number;
  maxHeight?: number | string;
  /** Slack (hours) below which an on-time entry is flagged "at risk"; comes from SchedulingConfig. */
  atRiskSlackHours?: number;
  /** Optional downtime windows per row id, drawn hatched behind the bars. */
  downtime?: Record<string, TimeWindow[]>;
  /** Non-working / idle windows per row id, drawn as a flat shade behind the bars. */
  nonWorking?: Record<string, TimeWindow[]>;
  showLegend?: boolean;
  /** Compare mode: entries of the baseline version; moved/removed ones are drawn as ghost bars. */
  baselineEntries?: ScheduleEntry[];
  baselineLabel?: string;
  /** Tooltip enrichment keyed by entry id. */
  meta?: Record<string, GanttEntryMeta>;
  /** Group header bands when rows carry `group` (default: on). */
  groupRows?: boolean;
  /** Render only the rows inside the scroll viewport (default: on; off renders everything). */
  virtualize?: boolean;
  /** Bars narrower than this (px) that touch each other are merged into one cluster bar. */
  minBarPx?: number;
}

const HEADER_HEIGHT = 36;
const CATEGORY_COUNT = 8;
const OVERSCAN_ROWS = 6;

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

type DisplayItem = { kind: "group"; key: string; label: string; count: number } | { kind: "row"; key: string; row: GanttRow };

interface Placed {
  entry: ScheduleEntry;
  rowIndex: number;
  run: { x: number; width: number } | null;
  setup: { x: number; width: number } | null;
}

interface Cluster {
  key: string;
  rowIndex: number;
  x: number;
  width: number;
  entries: ScheduleEntry[];
  late: number;
  locked: number;
}

type Drawn = { kind: "bar"; placed: Placed; change: "moved" | "added" | null } | { kind: "cluster"; cluster: Cluster };

interface Ghost {
  key: string;
  entry: ScheduleEntry;
  rowIndex: number;
  x: number;
  width: number;
  removed: boolean;
  /** Where the entry went (current row label / start) for the tooltip. */
  target: string | null;
}

interface TooltipState {
  x: number;
  y: number;
  entry?: ScheduleEntry;
  cluster?: Cluster;
  ghost?: Ghost;
}

/** Baseline entries are matched to the current ones by operation (entry ids are derived from it). */
function entryKey(e: ScheduleEntry): string {
  return e.operation_id || e.entry_id;
}

/**
 * SVG Gantt: one row per machine (grouped by machine group), time axis with day/week zoom,
 * entries as bars coloured by status or customer, hatched setup blocks, downtime and idle
 * shading, now-line, hover tooltip and click handler. Rows are virtualised against the scroll
 * viewport and tiny bars are clustered at low zoom so 100+ machines / 10k entries stay smooth.
 * Compare mode draws the baseline version's moved/removed entries as ghost bars.
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
  onClusterClick,
  onRowClick,
  selectedEntryId = null,
  rowHeight = 28,
  labelWidth = 180,
  maxHeight = 640,
  atRiskSlackHours = 8,
  downtime,
  nonWorking,
  showLegend = true,
  baselineEntries,
  baselineLabel = "previous version",
  meta,
  groupRows = true,
  virtualize = true,
  minBarPx = 3,
}: GanttChartProps) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const [viewport, setViewport] = useState<{ top: number; height: number }>({ top: 0, height: 0 });
  const scrollRef = useRef<HTMLDivElement>(null);
  const scale = useMemo(() => createTimeScale(start, end, ZOOM_PX_PER_HOUR[zoom]), [start, end, zoom]);
  const ticks = useMemo(() => generateTicks(scale, zoom), [scale, zoom]);

  // ---------------------------------------------------------------- rows
  const items = useMemo<DisplayItem[]>(() => {
    const useGroups = groupRows && rows.some((r) => r.group);
    if (!useGroups) return rows.map((r) => ({ kind: "row", key: r.id, row: r }));
    const order: string[] = [];
    const byGroup = new Map<string, GanttRow[]>();
    for (const r of rows) {
      const g = r.group ?? "";
      if (!byGroup.has(g)) {
        byGroup.set(g, []);
        order.push(g);
      }
      byGroup.get(g)?.push(r);
    }
    const out: DisplayItem[] = [];
    for (const g of order) {
      const members = byGroup.get(g) ?? [];
      out.push({ kind: "group", key: `group:${g}`, label: g || "Ungrouped", count: members.length });
      for (const r of members) out.push({ kind: "row", key: r.id, row: r });
    }
    return out;
  }, [rows, groupRows]);

  const rowIndex = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach((it, i) => {
      if (it.kind === "row") m.set(it.row.id, i);
    });
    return m;
  }, [items]);
  const rowLabel = useCallback((id: string) => rows.find((r) => r.id === id)?.label ?? id, [rows]);

  const bodyHeight = items.length * rowHeight;
  const barHeight = Math.max(8, rowHeight - 10);
  const barY = (index: number) => index * rowHeight + (rowHeight - barHeight) / 2;

  // ------------------------------------------------------------ viewport
  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setViewport((prev) => (prev.top === el.scrollTop && prev.height === el.clientHeight ? prev : { top: el.scrollTop, height: el.clientHeight }));
  }, []);
  useEffect(() => {
    measure();
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);

  const [firstVisible, lastVisible] = useMemo(() => {
    if (!virtualize || viewport.height <= 0) return [0, items.length - 1];
    const top = Math.max(0, viewport.top - HEADER_HEIGHT);
    const first = Math.max(0, Math.floor(top / rowHeight) - OVERSCAN_ROWS);
    const last = Math.min(items.length - 1, Math.ceil((top + viewport.height) / rowHeight) + OVERSCAN_ROWS);
    return [first, last];
  }, [virtualize, viewport, items.length, rowHeight]);
  const inView = (index: number) => index >= firstVisible && index <= lastVisible;

  // ---------------------------------------------------------------- bars
  const placed = useMemo<Placed[]>(
    () =>
      entries.flatMap((entry) => {
        const index = rowIndex.get(entry.machine_id);
        if (index === undefined) return [];
        const setupStart = parseUtc(entry.setup_start) ?? parseUtc(entry.start);
        const runStart = parseUtc(entry.start);
        const runEnd = parseUtc(entry.end);
        if (!setupStart || !runStart || !runEnd) return [];
        const run = clampSpan(scale, runStart, runEnd);
        const setup = setupStart < runStart ? clampSpan(scale, setupStart, runStart) : null;
        if (!run && !setup) return [];
        return [{ entry, rowIndex: index, run, setup }];
      }),
    [entries, rowIndex, scale],
  );

  const baselineByKey = useMemo(() => {
    if (!baselineEntries) return null;
    return new Map(baselineEntries.map((e) => [entryKey(e), e]));
  }, [baselineEntries]);
  const currentByKey = useMemo(() => new Map(entries.map((e) => [entryKey(e), e])), [entries]);

  const changeOf = useCallback(
    (entry: ScheduleEntry): "moved" | "added" | null => {
      if (!baselineByKey) return null;
      const b = baselineByKey.get(entryKey(entry));
      if (!b) return "added";
      const shifted = Math.abs((parseUtc(b.start)?.getTime() ?? 0) - (parseUtc(entry.start)?.getTime() ?? 0)) > 60_000;
      return b.machine_id !== entry.machine_id || shifted ? "moved" : null;
    },
    [baselineByKey],
  );

  /** Merge runs of touching tiny bars (per row) into cluster bars; everything else is drawn as-is. */
  const drawn = useMemo<Drawn[]>(() => {
    const byRow = new Map<number, Placed[]>();
    for (const p of placed) {
      const list = byRow.get(p.rowIndex) ?? [];
      list.push(p);
      byRow.set(p.rowIndex, list);
    }
    const out: Drawn[] = [];
    for (const [index, list] of byRow) {
      list.sort((a, b) => (a.run?.x ?? a.setup?.x ?? 0) - (b.run?.x ?? b.setup?.x ?? 0));
      let cluster: Cluster | null = null;
      const flush = () => {
        if (!cluster) return;
        if (cluster.entries.length === 1) {
          const only = list.find((p) => p.entry === cluster?.entries[0]);
          if (only) out.push({ kind: "bar", placed: only, change: changeOf(only.entry) });
        } else {
          out.push({ kind: "cluster", cluster });
        }
        cluster = null;
      };
      for (const p of list) {
        const x = p.run?.x ?? p.setup?.x ?? 0;
        const width = (p.run?.width ?? 0) + (p.setup?.width ?? 0);
        const tiny = width < minBarPx;
        if (tiny) {
          if (cluster && x <= cluster.x + cluster.width + 1) {
            cluster.width = Math.max(cluster.width, x + width - cluster.x);
            cluster.entries.push(p.entry);
            if ((p.entry.expected_lateness_hours ?? 0) > 0) cluster.late += 1;
            if (p.entry.locked) cluster.locked += 1;
          } else {
            flush();
            cluster = { key: `c:${index}:${p.entry.entry_id}`, rowIndex: index, x, width: Math.max(width, 1), entries: [p.entry], late: (p.entry.expected_lateness_hours ?? 0) > 0 ? 1 : 0, locked: p.entry.locked ? 1 : 0 };
          }
        } else {
          flush();
          out.push({ kind: "bar", placed: p, change: changeOf(p.entry) });
        }
      }
      flush();
    }
    return out;
  }, [placed, minBarPx, changeOf]);

  const ghosts = useMemo<Ghost[]>(() => {
    if (!baselineEntries) return [];
    return baselineEntries.flatMap((b) => {
      const index = rowIndex.get(b.machine_id);
      if (index === undefined) return [];
      const current = currentByKey.get(entryKey(b));
      const shifted = current ? Math.abs((parseUtc(b.start)?.getTime() ?? 0) - (parseUtc(current.start)?.getTime() ?? 0)) > 60_000 : false;
      const moved = current ? current.machine_id !== b.machine_id || shifted : false;
      if (current && !moved) return [];
      const s = parseUtc(b.setup_start) ?? parseUtc(b.start);
      const e = parseUtc(b.end);
      if (!s || !e) return [];
      const span = clampSpan(scale, s, e);
      if (!span) return [];
      const target = current ? `${current.machine_id === b.machine_id ? "same machine" : rowLabel(current.machine_id)} · ${formatDateTime(current.start, "dd MMM HH:mm")}` : null;
      return [{ key: `g:${b.entry_id}`, entry: b, rowIndex: index, x: span.x, width: span.width, removed: !current, target }];
    });
  }, [baselineEntries, rowIndex, currentByKey, scale, rowLabel]);

  const nowX = now >= scale.start && now <= scale.end ? scale.x(now) : null;
  const compare = Boolean(baselineEntries);

  const at = (e: MouseEvent<SVGElement>) => ({ x: e.clientX + 12, y: e.clientY + 12 });
  const onMove = (e: MouseEvent<SVGElement>) => setTooltip((prev) => (prev ? { ...prev, ...at(e) } : prev));

  const visibleItems = items.map((it, i) => ({ it, i })).filter(({ i }) => inView(i));

  return (
    <div className="col gap-1">
      <div className="gantt" style={{ maxHeight }} data-testid="gantt" ref={scrollRef} onScroll={measure}>
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
            <div className="gantt-labels" style={{ width: labelWidth, height: Math.max(bodyHeight, 1) }}>
              {visibleItems.map(({ it, i }) =>
                it.kind === "group" ? (
                  <div key={it.key} className="gantt-group" style={{ height: rowHeight, top: i * rowHeight }} title={it.label}>
                    <span className="gantt-group-label">{it.label}</span>
                    <span className="gantt-group-count">{it.count}</span>
                  </div>
                ) : (
                  <div
                    key={it.key}
                    className={`gantt-label${onRowClick ? " clickable" : ""}`}
                    style={{ height: rowHeight, top: i * rowHeight }}
                    onClick={onRowClick ? () => onRowClick(it.row) : undefined}
                    title={it.row.label}
                  >
                    <span className="gantt-label-main">{it.row.label}</span>
                    {it.row.sublabel && rowHeight >= 26 ? <span className="gantt-label-sub">{it.row.sublabel}</span> : null}
                  </div>
                ),
              )}
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
              {visibleItems.map(({ it, i }) =>
                it.kind === "group" ? (
                  <rect key={it.key} className="gantt-group-band" x={0} y={i * rowHeight} width={scale.width} height={rowHeight} />
                ) : (
                  <line key={it.key} className="gantt-row-line" x1={0} x2={scale.width} y1={(i + 1) * rowHeight} y2={(i + 1) * rowHeight} />
                ),
              )}
              {nonWorking
                ? visibleItems.flatMap(({ it, i }) =>
                    it.kind !== "row"
                      ? []
                      : (nonWorking[it.row.id] ?? []).flatMap((w, j) => {
                          const a = parseUtc(w.start);
                          const b = parseUtc(w.end);
                          if (!a || !b) return [];
                          const span = clampSpan(scale, a, b);
                          if (!span) return [];
                          return [
                            <rect key={`${it.key}-nw-${j}`} className="gantt-nonworking" x={span.x} y={i * rowHeight} width={span.width} height={rowHeight}>
                              <title>{w.reason || "Non-working"}</title>
                            </rect>,
                          ];
                        }),
                  )
                : null}
              {downtime
                ? visibleItems.flatMap(({ it, i }) =>
                    it.kind !== "row"
                      ? []
                      : (downtime[it.row.id] ?? []).flatMap((w, j) => {
                          const a = parseUtc(w.start);
                          const b = parseUtc(w.end);
                          if (!a || !b) return [];
                          const span = clampSpan(scale, a, b);
                          if (!span) return [];
                          return [
                            <rect key={`${it.key}-dt-${j}`} className="gantt-downtime" x={span.x} y={i * rowHeight} width={span.width} height={rowHeight}>
                              <title>{w.reason || "Downtime"}</title>
                            </rect>,
                          ];
                        }),
                  )
                : null}
              {ghosts
                .filter((g) => inView(g.rowIndex))
                .map((g) => (
                  <rect
                    key={g.key}
                    className={`gantt-ghost${g.removed ? " gantt-ghost-removed" : ""}`}
                    data-testid="gantt-ghost"
                    data-entry-id={g.entry.entry_id}
                    x={g.x}
                    y={barY(g.rowIndex)}
                    width={g.width}
                    height={barHeight}
                    rx={2}
                    onMouseEnter={(e) => setTooltip({ ghost: g, ...at(e) })}
                    onMouseMove={onMove}
                    onMouseLeave={() => setTooltip(null)}
                  />
                ))}
              {drawn.map((d) => {
                if (d.kind === "cluster") {
                  const c = d.cluster;
                  if (!inView(c.rowIndex)) return null;
                  const y = barY(c.rowIndex);
                  const tone: Tone = c.late > 0 ? "late" : c.locked > 0 ? "hold" : "running";
                  return (
                    <g key={c.key} data-testid="gantt-cluster" data-count={c.entries.length}>
                      <rect
                        className="gantt-cluster"
                        x={c.x}
                        y={y}
                        width={Math.max(c.width, 2)}
                        height={barHeight}
                        rx={1}
                        fill={toneFill(tone)}
                        onClick={onClusterClick ? () => onClusterClick(c.entries) : onEntryClick ? () => onEntryClick(c.entries[0] as ScheduleEntry) : undefined}
                        onMouseEnter={(e) => setTooltip({ cluster: c, ...at(e) })}
                        onMouseMove={onMove}
                        onMouseLeave={() => setTooltip(null)}
                      />
                      {c.width > 18 ? (
                        <text className="gantt-bar-label" x={c.x + 3} y={y + barHeight / 2 + 3.5}>
                          ×{c.entries.length}
                        </text>
                      ) : null}
                    </g>
                  );
                }
                const { entry, rowIndex: index, run, setup } = d.placed;
                if (!inView(index)) return null;
                const tone = entryTone(entry, now, atRiskSlackHours);
                const fill = colorBy === "customer" ? customerFill(entry.customer_id) : toneFill(tone);
                const y = barY(index);
                const cls = ["gantt-bar", selectedEntryId === entry.entry_id ? "selected" : "", d.change ? `gantt-bar-${d.change}` : ""].filter(Boolean).join(" ");
                return (
                  <g key={entry.entry_id} data-testid="gantt-entry" data-entry-id={entry.entry_id} data-tone={tone} data-change={d.change ?? undefined}>
                    {setup ? <rect className="gantt-setup" x={setup.x} y={y} width={setup.width} height={barHeight} rx={1} /> : null}
                    {run ? (
                      <>
                        <rect
                          className={cls}
                          x={run.x}
                          y={y}
                          width={run.width}
                          height={barHeight}
                          rx={2}
                          fill={fill}
                          onClick={onEntryClick ? () => onEntryClick(entry) : undefined}
                          onMouseEnter={(e) => setTooltip({ entry, ...at(e) })}
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
        {tooltip ? <GanttTooltip state={tooltip} meta={meta} baselineLabel={baselineLabel} rowLabel={rowLabel} /> : null}
      </div>
      {showLegend ? <GanttLegend colorBy={colorBy} compare={compare} baselineLabel={baselineLabel} /> : null}
    </div>
  );
}

function GanttTooltip({ state, meta, baselineLabel, rowLabel }: { state: TooltipState; meta?: Record<string, GanttEntryMeta>; baselineLabel: string; rowLabel: (id: string) => string }) {
  if (state.cluster) {
    const c = state.cluster;
    const first = c.entries[0];
    const last = c.entries[c.entries.length - 1];
    return (
      <div className="gantt-tooltip" style={{ left: state.x, top: state.y }} role="tooltip">
        <strong>{c.entries.length} jobs</strong> · {rowLabel(first?.machine_id ?? "")}
        <dl className="kv">
          <dt>Window</dt>
          <dd>
            {formatDateTime(first?.setup_start, "dd MMM HH:mm")} → {formatDateTime(last?.end, "dd MMM HH:mm")}
          </dd>
          <dt>Late</dt>
          <dd className="num">{c.late}</dd>
          <dt>Locked</dt>
          <dd className="num">{c.locked}</dd>
          <dt>Orders</dt>
          <dd>{c.entries.slice(0, 6).map((e) => e.order_id).join(", ")}{c.entries.length > 6 ? ", …" : ""}</dd>
        </dl>
        <div className="text-faint">Zoom to day to see each job</div>
      </div>
    );
  }
  if (state.ghost) {
    const g = state.ghost;
    const e = g.entry;
    return (
      <div className="gantt-tooltip" style={{ left: state.x, top: state.y }} role="tooltip">
        <strong>{e.order_id}</strong> · {g.removed ? "removed" : "moved"} vs {baselineLabel}
        <dl className="kv">
          <dt>Was</dt>
          <dd>
            {rowLabel(e.machine_id)} · {formatDateTime(e.start, "dd MMM HH:mm")} → {formatDateTime(e.end, "dd MMM HH:mm")}
          </dd>
          <dt>Now</dt>
          <dd>{g.target ?? "not in this version"}</dd>
        </dl>
      </div>
    );
  }
  const e = state.entry;
  if (!e) return null;
  const m = meta?.[e.entry_id];
  return (
    <div className="gantt-tooltip" style={{ left: state.x, top: state.y }} role="tooltip">
      <strong>{e.order_id}</strong> · op {e.operation_id} · seq {e.sequence_on_machine}
      <dl className="kv">
        {m?.customer_name || e.customer_id ? (
          <>
            <dt>Customer</dt>
            <dd>{m?.customer_name ?? e.customer_id}</dd>
          </>
        ) : null}
        {m?.part_name || m?.part_id ? (
          <>
            <dt>Part</dt>
            <dd>{m.part_name ?? m.part_id}</dd>
          </>
        ) : null}
        {m?.order_status ? (
          <>
            <dt>Status</dt>
            <dd>{humanize(m.order_status)}</dd>
          </>
        ) : null}
        <dt>Machine</dt>
        <dd>{rowLabel(e.machine_id)}</dd>
        <dt>Setup</dt>
        <dd>
          {formatDateTime(e.setup_start, "dd MMM HH:mm")} · {formatMinutes(e.setup_minutes)}
          {e.setup_family ? ` · family ${e.setup_family}` : ""}
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
            <dd className={e.expected_lateness_hours !== null && e.expected_lateness_hours > 0 ? "tone-late" : ""}>
              {formatDateTime(e.due_date, "dd MMM HH:mm")}
              {e.expected_lateness_hours !== null && e.expected_lateness_hours > 0 ? ` · late ${formatHours(e.expected_lateness_hours)}` : " · on time"}
            </dd>
          </>
        ) : null}
        {e.locked ? (
          <>
            <dt>Lock</dt>
            <dd>locked — kept across replans</dd>
          </>
        ) : null}
        <dt>Why here</dt>
        <dd>{e.placement_reason}</dd>
      </dl>
    </div>
  );
}

function GanttLegend({ colorBy, compare, baselineLabel }: { colorBy: GanttColorMode; compare: boolean; baselineLabel: string }) {
  const compareItems = compare ? (
    <>
      <span>
        <span className="gantt-legend-swatch gantt-legend-ghost" />
        {baselineLabel} (moved)
      </span>
      <span>
        <span className="gantt-legend-swatch gantt-legend-ghost-removed" />
        {baselineLabel} (removed)
      </span>
      <span>
        <span className="gantt-legend-swatch gantt-legend-moved" />
        moved / added in this version
      </span>
    </>
  ) : null;
  if (colorBy === "customer") {
    return (
      <div className="gantt-legend">
        <span>Colour: one hue per customer · hatched = setup / changeover · dashed red = now</span>
        {compareItems}
      </div>
    );
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
      <span>
        <span className="gantt-legend-swatch" style={{ background: "repeating-linear-gradient(-45deg, var(--status-late) 0 1px, var(--gantt-nonworking) 1px 5px)" }} />
        Downtime
      </span>
      <span>
        <span className="gantt-legend-swatch" style={{ background: "var(--gantt-nonworking)", border: "1px solid var(--border)" }} />
        Idle / non-working
      </span>
      <span>×n = clustered jobs (zoom in)</span>
      <span>dashed red = now</span>
      {compareItems}
    </div>
  );
}
