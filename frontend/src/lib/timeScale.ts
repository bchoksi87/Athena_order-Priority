/** Linear time → pixel scale and tick generation shared by GanttChart and Timeline. */
import { addDays, addHours, differenceInMinutes, format, startOfDay, startOfHour } from "date-fns";

export type GanttZoom = "day" | "week" | "fortnight";

/** Pixels per hour for each zoom level. */
export const ZOOM_PX_PER_HOUR: Record<GanttZoom, number> = { day: 56, week: 12, fortnight: 6 };

export interface TimeScale {
  start: Date;
  end: Date;
  pxPerHour: number;
  width: number;
  x: (d: Date) => number;
  /** Inverse mapping used by hit-testing and tests. */
  invert: (px: number) => Date;
}

export function createTimeScale(start: Date, end: Date, pxPerHour: number): TimeScale {
  const totalHours = Math.max(1, differenceInMinutes(end, start) / 60);
  const width = Math.ceil(totalHours * pxPerHour);
  return {
    start,
    end,
    pxPerHour,
    width,
    x: (d) => ((d.getTime() - start.getTime()) / 3_600_000) * pxPerHour,
    invert: (px) => new Date(start.getTime() + (px / pxPerHour) * 3_600_000),
  };
}

export interface Tick {
  x: number;
  label: string;
  major: boolean;
}

/** Day boundaries as major ticks; hours (or 6-hour blocks) as minor ticks depending on zoom. */
export function generateTicks(scale: TimeScale, zoom: GanttZoom): Tick[] {
  const ticks: Tick[] = [];
  const minorHours = zoom === "day" ? (scale.pxPerHour >= 40 ? 1 : 2) : zoom === "week" ? 6 : 12;
  let day = startOfDay(scale.start);
  while (day <= scale.end) {
    if (day >= scale.start) ticks.push({ x: scale.x(day), label: format(day, "EEE dd MMM"), major: true });
    day = addDays(day, 1);
  }
  let t = startOfHour(scale.start);
  while (t <= scale.end) {
    if (t >= scale.start && t.getHours() % minorHours === 0 && t.getHours() !== 0) {
      ticks.push({ x: scale.x(t), label: format(t, "HH:mm"), major: false });
    }
    t = addHours(t, 1);
  }
  return ticks.sort((a, b) => a.x - b.x);
}

/** Clamps an interval to the scale and returns pixel bounds, or null when fully outside. */
export function clampSpan(scale: TimeScale, from: Date, to: Date): { x: number; width: number } | null {
  const a = Math.max(from.getTime(), scale.start.getTime());
  const b = Math.min(to.getTime(), scale.end.getTime());
  if (b <= a) return null;
  const x = scale.x(new Date(a));
  const width = Math.max(1, scale.x(new Date(b)) - x);
  return { x, width };
}
