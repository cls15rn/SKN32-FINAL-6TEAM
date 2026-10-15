/**
 * `[2026-10-12 사용자 결정 — 멘토링 코멘트 「줄 길이에 실제 시간을 반영」]` The time each row of a day's timeline stands for: a stop from its start to its end, the way to the next stop
 * from the end of the stop before to the start of the next one. Pure (no DOM): the screen measures the rows and draws the line from these spans (`rail-list.tsx`), and the 「지금」 mark
 * moves along the drawn line by them.
 */

/**
 * `[2026-10-13 사용자 결정 — 줄 간격 ② 일정한 간격 · 목업 Tripilot_personal/travel-careops/mockups/2026-10-13_타임라인_일정간격_접힌카드_목업_v2.html]` The line is no longer to scale:
 * a stop's solid line runs to its card's end, and the way to the next stop takes these fixed lengths (px) whatever its minutes — waiting to leave, travelling (dashed), the free time
 * after arriving, and the whole gap when the way is not known. Its minutes are said in words (「여유 45분」). Replaced ④ (0.8 px a minute, long free time folded).
 */
export const RAIL_GAP = { wait: 8, travel: 36, slack: 12, none: 56 } as const;

/** `[2026-10-15 사용자 결정 — ST4F-494 · ①]` The plan check (and the edit of a registered trip) draws its line to scale again: a minute is this long. 90 minutes = 108 px. */
export const RAIL_PX_PER_MIN = 1.2;

/** One row as the line reads it: a stop (start and end) or the way after it (when it leaves and arrives). Minutes since midnight; null = not known. */
export type RailRow =
  | { kind: "stay"; start: number | null; end: number | null }
  | { kind: "move"; depart: number | null; arrive: number | null };

/** The minutes a row stands for (`from` to `to`); null where it is not known. */
export interface RailSpan { from: number | null; to: number | null }

/**
 * A stop ends at its end time. With no end it lasts until it is left: the way after it leaves, else the next stop starts — the last stop of a day with no end stays open (null).
 * A way lasts from the end of the stop before to the start of the next stop (waiting to leave, travelling and the free time after arriving are all in it).
 */
export function railSpans(rows: readonly RailRow[]): RailSpan[] {
  const spans: RailSpan[] = [];
  let lastStayTo: number | null = null;
  rows.forEach((row, index) => {
    const after = rows.slice(index + 1);
    const nextStart = (after.find((other) => other.kind === "stay") as Extract<RailRow, { kind: "stay" }> | undefined)?.start ?? null;
    if (row.kind === "move") {
      spans.push({ from: lastStayTo, to: nextStart });
      return;
    }
    let to = row.end;
    if (to === null) {
      const leave = after[0]?.kind === "move" ? after[0].depart : null;
      to = leave ?? nextStart;
    }
    // A time that runs back (past midnight, or typed wrong) cannot be drawn to scale: it is not known here.
    if (row.start !== null && to !== null && to < row.start) to = null;
    spans.push({ from: row.start, to });
    lastStayTo = to;
  });
  return spans;
}

/** The time → height pairs of a drawn day, in order. */
export interface RailPoint { minute: number; y: number }

/** Where a minute stands on a drawn day: between the two pairs around it, in proportion (space that stands for no time — a long card — is stepped over). Null past the last pair. */
export function railY(points: readonly RailPoint[], minute: number): number | null {
  for (let index = 0; index < points.length - 1; index++) {
    const a = points[index], b = points[index + 1];
    if (b.minute > a.minute && minute >= a.minute && minute < b.minute) return a.y + ((minute - a.minute) / (b.minute - a.minute)) * (b.y - a.y);
  }
  return null;
}
