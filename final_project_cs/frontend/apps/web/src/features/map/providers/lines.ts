import type { MapLine } from "../model";

/**
 * How a route line looks, for the three providers alike. A line whose road is not known (`dashed`) is paler and dashed, so it never
 * reads as a real road. The colour is the theme's (`--color-primary`), read where the map is drawn — an SDK paints its own lines and
 * cannot take a CSS variable.
 */
export interface LineStyle { color: string; weight: number; opacity: number; dash: [number, number] | null }

/** The theme's primary colour as the browser resolves it; a closed-form fallback when the page has none (a test double). */
function themeColor(container: HTMLElement): string {
  try { return getComputedStyle(container).getPropertyValue("--color-primary").trim() || "#2f6f4f"; }
  catch { return "#2f6f4f"; }
}

/** The theme colour each kind of route is drawn in (`[2026-10-06 사용자 지시]` 지하철은 파랑 …). Shared with the legend over the map, which reads the same names. */
export const LINE_TONE_VARIABLE: Record<NonNullable<MapLine["mode"]>, string> = {
  subway: "--color-adjusted", mixed: "--color-adjusted", bus: "--color-success", taxi: "--color-warning", walk: "--color-muted", bike: "--color-muted", unknown: "--color-primary",
};

function toneColor(container: HTMLElement, mode: MapLine["mode"]): string {
  if (!mode) return themeColor(container);
  try { return getComputedStyle(container).getPropertyValue(LINE_TONE_VARIABLE[mode]).trim() || themeColor(container); }
  catch { return themeColor(container); }
}

export function lineStyle(container: HTMLElement, line: MapLine): LineStyle {
  const color = toneColor(container, line.mode);
  return line.dashed ? { color, weight: 4, opacity: 0.55, dash: [8, 10] } : { color, weight: 5, opacity: 0.85, dash: null };
}

/**
 * `[2026-10-15 사용자 결정 — ST4F-495]` A walked stretch: round dots in the walking colour (grey) — never the pale long dash of a line whose road is not known.
 * `dots` = one dot every `dots` px (each provider draws a round dot of the line's weight).
 */
export interface WalkStyle { color: string; weight: number; opacity: number; dots: number }

export function walkStyle(container: HTMLElement): WalkStyle {
  return { color: toneColor(container, "walk"), weight: 5, opacity: 0.9, dots: 10 };
}

/** One piece of a line to draw: a walked stretch or the rest of it (`walk: false`, drawn as the line's own mode). Pieces share their end points, so the line is not broken. */
export interface LinePiece { points: MapLine["points"]; walk: boolean }

/** A line cut at its walked stretches (`walks`); a line with none is one piece. A line whose road is not known (`dashed`) is never cut — it is one pale dash. */
export function linePieces(line: MapLine): LinePiece[] {
  const last = line.points.length - 1;
  const walks = line.dashed ? [] : (line.walks ?? []).filter(([first, end]) => first >= 0 && end <= last && end > first);
  if (walks.length === 0) return [{ points: line.points, walk: false }];
  const pieces: LinePiece[] = [];
  let at = 0;
  for (const [first, end] of walks) {
    if (first > at) pieces.push({ points: line.points.slice(at, first + 1), walk: false });
    pieces.push({ points: line.points.slice(first, end + 1), walk: true });
    at = end;
  }
  if (at < last) pieces.push({ points: line.points.slice(at), walk: false });
  return pieces;
}

/** Presentation-only updates must not redraw the lines. */
export function linesKey(lines: MapLine[]): string {
  return JSON.stringify(lines.map(({ id, points, dashed, mode, walks }) => [id, dashed, mode ?? null, walks ?? [], points.map(({ lat, lng }) => [lat, lng])]));
}
