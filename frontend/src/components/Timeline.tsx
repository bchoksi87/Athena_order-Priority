import { useMemo } from "react";
import { addDays } from "date-fns";

import type { ScheduleEntry, TimeWindow } from "@/api/types";
import { entryTone } from "@/lib/entryTone";
import { formatMinutes } from "@/lib/formatters";
import { parseUtc, startOfLocalDay } from "@/lib/time";
import { clampSpan, createTimeScale, generateTicks } from "@/lib/timeScale";

import "./Timeline.css";

export interface TimelineProps {
  entries: ScheduleEntry[];
  /** Any instant on the day to show (local day). */
  day: Date;
  now?: Date;
  downtime?: TimeWindow[];
  /** Idle / non-working windows drawn as a flat shade behind the bars. */
  nonWorking?: TimeWindow[];
  onEntryClick?: (entry: ScheduleEntry) => void;
  selectedEntryId?: string | null;
  height?: number;
  pxPerHour?: number;
  atRiskSlackHours?: number;
  /** Number of local days to span starting at `day` (1 = day view, 7 = week view). */
  days?: number;
}

const AXIS = 22;

/** Single-machine day view: 24-hour strip with setup (hatched) + run bars and the now-line. */
export function Timeline({
  entries,
  day,
  now = new Date(),
  downtime = [],
  nonWorking = [],
  onEntryClick,
  selectedEntryId = null,
  height = 72,
  pxPerHour = 48,
  atRiskSlackHours = 8,
  days = 1,
}: TimelineProps) {
  const start = startOfLocalDay(day);
  const end = addDays(start, Math.max(1, days));
  const scale = useMemo(() => createTimeScale(start, end, pxPerHour), [start, end, pxPerHour]);
  const ticks = useMemo(() => generateTicks(scale, days > 1 ? "week" : "day"), [scale, days]);
  const barTop = AXIS + 8;
  const barH = height - barTop - 8;

  const bars = useMemo(
    () =>
      entries.flatMap((entry) => {
        const s0 = parseUtc(entry.setup_start) ?? parseUtc(entry.start);
        const s1 = parseUtc(entry.start);
        const e1 = parseUtc(entry.end);
        if (!s0 || !s1 || !e1) return [];
        const run = clampSpan(scale, s1, e1);
        const setup = s0 < s1 ? clampSpan(scale, s0, s1) : null;
        if (!run && !setup) return [];
        return [{ entry, run, setup }];
      }),
    [entries, scale],
  );

  const nowX = now >= start && now < end ? scale.x(now) : null;

  return (
    <div className="timeline" data-testid="timeline">
      <svg width={scale.width} height={height} role="img" aria-label="Machine day timeline">
        <defs>
          <pattern id="timeline-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--bg-panel-raised)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--gantt-setup)" strokeWidth="2" />
          </pattern>
        </defs>
        <g className="timeline-axis">
          {ticks
            .filter((t) => !t.major)
            .map((t, i) => (
              <g key={i}>
                <line className="timeline-grid" x1={t.x} x2={t.x} y1={AXIS} y2={height} />
                <text x={t.x + 2} y={13}>
                  {t.label}
                </text>
              </g>
            ))}
          {days > 1
            ? ticks
                .filter((t) => t.major)
                .map((t, i) => (
                  <g key={`d${i}`}>
                    <line className="timeline-grid timeline-grid-major" x1={t.x} x2={t.x} y1={0} y2={height} />
                    <text className="timeline-day" x={t.x + 3} y={13}>
                      {t.label}
                    </text>
                  </g>
                ))
            : null}
        </g>
        {nonWorking.flatMap((w, i) => {
          const a = parseUtc(w.start);
          const b = parseUtc(w.end);
          if (!a || !b) return [];
          const span = clampSpan(scale, a, b);
          return span
            ? [
                <rect key={`nw-${i}`} className="timeline-nonworking" x={span.x} y={AXIS} width={span.width} height={height - AXIS}>
                  <title>{w.reason || "Non-working"}</title>
                </rect>,
              ]
            : [];
        })}
        {downtime.flatMap((w, i) => {
          const a = parseUtc(w.start);
          const b = parseUtc(w.end);
          if (!a || !b) return [];
          const span = clampSpan(scale, a, b);
          return span ? [<rect key={i} className="timeline-downtime" x={span.x} y={AXIS} width={span.width} height={height - AXIS} />] : [];
        })}
        {bars.length === 0 ? (
          <text className="timeline-empty" x={8} y={barTop + barH / 2 + 4}>
            {days > 1 ? "No scheduled work in this period" : "No scheduled work on this day"}
          </text>
        ) : null}
        {bars.map(({ entry, run, setup }) => {
          const tone = entryTone(entry, now, atRiskSlackHours);
          return (
            <g key={entry.entry_id} data-testid="timeline-entry">
              {setup ? <rect className="timeline-setup" x={setup.x} y={barTop} width={setup.width} height={barH} /> : null}
              {run ? (
                <>
                  <rect
                    className={`timeline-bar${selectedEntryId === entry.entry_id ? " selected" : ""}`}
                    x={run.x}
                    y={barTop}
                    width={run.width}
                    height={barH}
                    rx={2}
                    fill={`var(--status-${tone})`}
                    onClick={onEntryClick ? () => onEntryClick(entry) : undefined}
                  >
                    <title>
                      {entry.order_id} · {formatMinutes(entry.run_minutes)} · {entry.placement_reason}
                    </title>
                  </rect>
                  {entry.locked && run.width > 14 ? (
                    <text className="timeline-lock" x={run.x + run.width - 12} y={barTop + 12}>
                      🔒
                    </text>
                  ) : null}
                  {run.width > 48 ? (
                    <>
                      <text className="timeline-label" x={run.x + 4} y={barTop + 12}>
                        {entry.order_id}
                      </text>
                      <text className="timeline-sub" x={run.x + 4} y={barTop + barH - 4}>
                        {formatMinutes(entry.run_minutes)} · q{entry.quantity}
                      </text>
                    </>
                  ) : null}
                </>
              ) : null}
            </g>
          );
        })}
        {nowX !== null ? <line className="timeline-now" x1={nowX} x2={nowX} y1={AXIS} y2={height} /> : null}
      </svg>
    </div>
  );
}
