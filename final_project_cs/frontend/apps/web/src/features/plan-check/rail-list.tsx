"use client";

import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { parseHm } from "./time-plan";
import { RAIL_GAP, RAIL_PX_PER_MIN, railSpans, railY, type RailPoint, type RailRow } from "./time-rail";
import styles from "./plan-check.module.css";

/**
 * `[2026-10-12 사용자 결정 — 목업 Tripilot_personal/travel-careops/mockups/2026-10-12_타임라인_지금시각_목업.html]` The line at the left of a day's timeline, drawn in one layer
 * over the rows: solid = the time spent at a stop (its dot to its end, closed by ⊖), dashed = only the time travelling (leaving → arriving), thin = waiting to leave, the free time
 * after arriving and any space a line of text takes. `[2026-10-13 사용자 결정 — ② 일정한 간격]` Not to scale: the solid line runs to the card's end, a way takes `RAIL_GAP`; an opened
 * way carries its dashes to its box's end.
 *
 * The rows say what they are with data attributes (the rest of the row is the screen's own): a stop `data-rail="stay"` with `data-start`/`data-end` ("HH:MM"), a way `data-rail="move"` with
 * `data-dep`/`data-arr`; `data-rail-open` when opened (its `article`, or its `[data-rail-box]`, is measured), `data-past` for a past row drawn small (not to scale).
 */

/** The centre of a stop's dot (and of its time and name) from the top of its row; a past row's small dot. */
const DOT_Y = 27.5, PAST_DOT_Y = 13;
/** The way's line of text is centred on when it leaves; it needs this much room above (half its 45 px box). */
const DEP_ROOM = 23;
/** The bottom of a stop's two-line time (start, and 「~end」 under it). */
const TIME_BOTTOM = 56;
/** ① The last stop of a day with no end: a short line that fades out (its end is not known). */
const OPEN_END_PX = 60;

/** Where 「now」 stands on this day: today (at `minute`), a day that is over (the line filled to its end), or none. */
export type RailNow = { kind: "today"; minute: number; label?: string } | { kind: "over" };

type Segment = { kind: "stay" | "travel" | "free"; y1: number; y2: number; past?: boolean; fade?: boolean };

const minuteOf = (value: string | undefined) => (value ? parseHm(value) : null);

function readRow(row: HTMLElement): RailRow {
  return row.dataset.rail === "move"
    ? { kind: "move", depart: minuteOf(row.dataset.dep), arrive: minuteOf(row.dataset.arr) }
    : { kind: "stay", start: minuteOf(row.dataset.start), end: minuteOf(row.dataset.end) };
}

/** The height a card's own content takes, whatever height it was given (a `min-height` only adds empty room at its bottom). Offsets, not boxes: a card moving in is not measured mid-move. */
function contentHeight(card: HTMLElement): number {
  const parts = Array.from(card.children).filter((child): child is HTMLElement => child instanceof HTMLElement && child.offsetParent !== null);
  if (parts.length === 0) return card.offsetHeight;
  const style = getComputedStyle(card);
  const bottom = Math.max(...parts.map((part) => (part.offsetParent === card ? part.offsetTop : part.offsetTop - card.offsetTop) + part.offsetHeight));
  return bottom + parseFloat(style.paddingBottom) + parseFloat(style.borderBottomWidth);
}

/**
 * `[2026-10-13 사용자 결정 — 카드 크기 통일]` Every folded card of the day is as tall as the tallest of them, so the cards line up. `[2026-10-13 ② · A]` No longer as tall as its time: at
 * least `--rail-card-min` (a screen sets it on its list — the registered trip's 100 px puts two stops in the first view), else its content. An opened card, a past one and a past one
 * opened again keep their own height.
 */
function evenCards(list: HTMLElement, rows: readonly HTMLElement[]) {
  const folded: { card: HTMLElement; need: number }[] = [];
  // Measure with every foot back in the flow: with `data-even` a foot sits at the bottom of the room it was given, and measuring that would feed the room back into the
  // height — the card grew a little on every layout and never settled (2026-10-13 CI: card-status-group 「element is not stable」). The min-heights stay meanwhile, so
  // nothing around moves.
  for (const card of rows.map((row) => row.querySelector<HTMLElement>(":scope > article"))) if (card) delete card.dataset.even;
  rows.forEach((row) => {
    const card = row.dataset.rail === "move" ? null : row.querySelector<HTMLElement>(":scope > article");
    if (!card) return;
    if (row.hasAttribute("data-past") || row.hasAttribute("data-rail-open") || row.hasAttribute("data-past-open")) { card.style.removeProperty("min-height"); return; }
    folded.push({ card, need: contentHeight(card) });
  });
  // A list with no `--rail-card-min` (the plan check) evens its cards to their content.
  const floor = parseFloat(getComputedStyle(list).getPropertyValue("--rail-card-min")) || 0;
  const tallest = Math.ceil(Math.max(floor, ...folded.map((entry) => entry.need)));
  // `data-even`: the card was made taller than its content — its foot (확인 필요 · 되돌리기) then stays at its bottom (plan-check.module.css).
  folded.forEach(({ card }) => { card.style.minHeight = `${tallest}px`; card.dataset.even = ""; });
}

/** Lowest point a stop row's own content reaches (its card, or its past line) — the time column is fixed (`TIME_BOTTOM`) and may be stuck to the top while scrolling. */
function contentBottom(row: HTMLElement): number {
  const parts = Array.from(row.children).filter((child): child is HTMLElement => child instanceof HTMLElement && (child.tagName === "ARTICLE" || child.tagName === "DIV"));
  return Math.max(TIME_BOTTOM, ...parts.map((part) => part.offsetTop + part.offsetHeight + 4));
}

/**
 * Gives each row the height its time takes and draws the line. The rows are set top to bottom (a row's top depends on the rows above), and only ever to the height they need — a row is
 * never emptied first, so the list does not shrink for a moment and the scroll position is not clamped.
 */
function layoutRail(list: HTMLElement, toScale: boolean): RailPoint[] {
  const rows = Array.from(list.querySelectorAll<HTMLElement>(":scope > [data-rail]"));
  const spans = railSpans(rows.map(readRow));
  // ① To scale the cards keep their own height (a card taller than its time leaves a thin line under its solid one).
  if (!toScale) evenCards(list, rows);
  const segments: Segment[] = [];
  const caps: { y: number; past: boolean }[] = [];
  const points: RailPoint[] = [];
  let cursor: number | null = null;
  const free = (y1: number, y2: number) => { if (y2 - y1 > .5) segments.push({ kind: "free", y1, y2 }); };
  const px = (minutes: number) => Math.max(0, minutes) * RAIL_PX_PER_MIN;

  rows.forEach((row, index) => {
    const { from, to } = spans[index];
    const past = row.hasAttribute("data-past");
    const top = row.offsetTop;
    if (row.dataset.rail !== "move") {
      const dotY = top + (past ? PAST_DOT_Y : DOT_Y);
      if (cursor !== null) free(cursor, dotY);
      if (from !== null) points.push({ minute: from, y: dotY });
      if (past || from === null) {
        // A past stop is a thin line, not to scale; a stop with no time has nothing to scale by.
        row.style.minHeight = "";
        const end = top + row.offsetHeight;
        segments.push(past ? { kind: "stay", y1: dotY, y2: end, past: true } : { kind: "free", y1: dotY, y2: end });
        if (past && to !== null) points.push({ minute: to, y: end });
        cursor = end;
        return;
      }
      let length: number;
      if (toScale) {
        // ① The solid line is the stop's time (an opened card carries it to the card's end); the row is at least that tall. With no end the day's last stop gets a short fading line.
        const lastStop = !rows.slice(index + 1).some((other) => other.dataset.rail !== "move");
        length = to === null ? (lastStop ? OPEN_END_PX : 0) : px(to - from);
        const opened = row.hasAttribute("data-rail-open") ? row.querySelector<HTMLElement>(":scope > article") : null;
        if (opened) length = Math.max(length, opened.offsetTop + opened.offsetHeight - DOT_Y);
        row.style.minHeight = `${DOT_Y + length}px`;
      } else {
        // ② The solid line runs to the card's end (folded cards are all one height, so every stop's line is as long); with no end it fades out there.
        row.style.minHeight = "";
        const card = row.querySelector<HTMLElement>(":scope > article");
        length = card ? card.offsetTop + card.offsetHeight - DOT_Y : 0;
      }
      const pastOpen = row.hasAttribute("data-past-open");
      segments.push({ kind: "stay", y1: dotY, y2: dotY + length, past: pastOpen, fade: to === null });
      if (to !== null) caps.push({ y: dotY + length, past: pastOpen });
      if (to !== null) points.push({ minute: to, y: dotY + length });
      cursor = dotY + length;
      return;
    }
    if (cursor !== null) free(cursor, top);
    if (past) {
      row.style.minHeight = "";
      row.style.removeProperty("--dep");
      const end = top + row.offsetHeight;
      segments.push({ kind: "free", y1: top, y2: end, past: true });
      if (from !== null) points.push({ minute: from, y: top });
      if (to !== null) points.push({ minute: to, y: end });
      cursor = end;
      return;
    }
    const { depart, arrive } = readRow(row) as Extract<RailRow, { kind: "move" }>;
    // The room the line of text may take above it, inside the stop row before (under its card and time). ① To scale that room is the stop's own time:
    // the way's text does not climb into it, so the seam between them keeps the ＋ of the edit clear (2026-10-15 사용자 지적 — the ＋ sat on the way's head).
    const before = rows[index - 1];
    const room = !toScale && before && before.dataset.rail !== "move" && !before.hasAttribute("data-past") ? Math.max(0, before.offsetHeight - contentBottom(before)) : 0;
    if (depart === null || arrive === null || from === null || to === null || arrive < depart) {
      // Not known when it leaves and arrives: no dashes, a thin line between the two stops (① the time between them).
      const length = toScale ? (from !== null && to !== null ? px(to - from) : 0) : RAIL_GAP.none;
      row.style.setProperty("--dep", `${Math.max(0, DEP_ROOM - room)}px`);
      row.style.minHeight = `${Math.max(0, length - DOT_Y)}px`;
      free(top, top + length);
      if (from !== null && to !== null) points.push({ minute: from, y: top }, { minute: to, y: top + length });
      cursor = top + length;
      return;
    }
    // ① Each part to scale. ② Fixed lengths whatever the minutes: waiting to leave and the free time after arriving only when there is some.
    const wait = toScale ? px(depart - from) : depart > from ? RAIL_GAP.wait : 0;
    const after = toScale ? px(to - arrive) : to > arrive ? RAIL_GAP.slack : 0;
    const pad = Math.max(0, DEP_ROOM - wait - room);
    const departY = top + pad + wait;
    row.style.setProperty("--dep", `${pad + wait}px`);
    let travel: number = toScale ? px(arrive - depart) : RAIL_GAP.travel;
    const box = row.hasAttribute("data-rail-open") ? row.querySelector<HTMLElement>("[data-rail-box]") : null;
    if (box) travel = Math.max(travel, box.offsetTop + box.offsetHeight - (pad + wait));
    row.style.minHeight = `${Math.max(0, pad + wait + travel + after - DOT_Y)}px`;
    free(top, departY);
    segments.push({ kind: "travel", y1: departY, y2: departY + travel });
    free(departY + travel, departY + travel + after);
    points.push({ minute: from, y: top + pad }, { minute: depart, y: departY }, { minute: arrive, y: departY + travel }, { minute: to, y: departY + travel + after });
    cursor = departY + travel + after;
  });

  const lines = list.querySelector<HTMLElement>(":scope > [data-rail-lines]");
  lines?.replaceChildren(...segments.map((segment) => {
    const line = document.createElement("i");
    line.className = styles.railSeg;
    line.dataset.kind = segment.kind;
    if (segment.past) line.dataset.past = "";
    if (segment.fade) line.dataset.fade = "";
    line.style.top = `${segment.y1}px`;
    line.style.height = `${segment.y2 - segment.y1}px`;
    return line;
  }));
  const ends = list.querySelector<HTMLElement>(":scope > [data-rail-caps]");
  ends?.replaceChildren(...caps.map((cap) => {
    const mark = document.createElement("i");
    mark.className = styles.railCap;
    if (cap.past) mark.dataset.past = "";
    mark.style.top = `${cap.y}px`;
    return mark;
  }));
  return points;
}

/**
 * 「지금」: the dot at now's height, and the line filled from the day's first dot to it (to the end on a day that is over). Only moved — nothing is laid out again.
 * `[2026-10-13 사용자 결정]` The dot alone flows down the line. `[2026-10-15 사용자 결정 — ST4F-494]` With `label` (the trip screen, when the operator's switch is on) its
 * 「지금 HH:MM」 label stands beside it again.
 */
function placeNow(list: HTMLElement, points: readonly RailPoint[], now: RailNow | null, smooth: boolean) {
  const fill = list.querySelector<HTMLElement>(":scope > [data-now-fill]");
  const mark = list.querySelector<HTMLElement>(":scope > [data-now-mark]");
  if (!fill || !mark) return;
  list.style.paddingBottom = "";
  if (!now || points.length === 0) { fill.style.display = "none"; mark.style.display = "none"; return; }
  const start = points[0].y;
  const last = Math.max(...points.map((point) => point.y));
  let y = last;
  if (now.kind === "today") {
    const at = railY(points, now.minute);
    // Before the first stop the mark stands at the top of its row, under the day's heading; past the day's last stop just under the line's end.
    y = Math.round(now.minute < points[0].minute ? start - DOT_Y + 4 : at ?? last + 6);
    // Past the day's last stop the label stands under the list: room for it, so it does not sit on what follows.
    if (now.label && now.minute >= points[0].minute && at === null) list.style.paddingBottom = "28px";
    mark.style.display = "";
    mark.style.transition = smooth ? "" : "none";
    mark.style.transform = `translateY(${y}px)`;
    const label = mark.querySelector<HTMLElement>("[data-now-label]");
    if (label) label.textContent = now.label ?? "";
  } else {
    mark.style.display = "none";
  }
  fill.style.display = "";
  fill.style.transition = smooth ? "" : "none";
  fill.style.top = `${start}px`;
  fill.style.height = `${Math.max(0, y - start)}px`;
}

/**
 * A day's `<ol>` with its line drawn. It is laid out after every render and when its size changes (a card opened, the width, a font); `now` only moves the 「지금」 mark —
 * a minute passing does not rebuild or re-measure the list, so scrolling is not interrupted (2026-10-12 사용자 지적 — the mockup rebuilt its list every minute and the scroll stuck).
 * `[2026-10-15 사용자 결정 — ST4F-494 · ①]` `toScale`: the line is the time again (1 minute = `RAIL_PX_PER_MIN`) — the plan check and the edit of a registered trip (`DayList`).
 * The registered trip's own screen keeps ② (fixed gaps, folded cards one height).
 */
export function RailList({ className, now = null, toScale = false, children }: { className: string; now?: RailNow | null; toScale?: boolean; children: ReactNode }) {
  const list = useRef<HTMLOListElement>(null);
  const points = useRef<RailPoint[]>([]);
  const nowRef = useRef(now);
  const placed = useRef(false);
  useLayoutEffect(() => {
    nowRef.current = now;
    const element = list.current;
    if (!element) return;
    points.current = layoutRail(element, toScale);
    placeNow(element, points.current, nowRef.current, placed.current);
    placed.current = true;
  });
  useEffect(() => {
    const element = list.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const watcher = new ResizeObserver(() => { points.current = layoutRail(element, toScale); placeNow(element, points.current, nowRef.current, true); });
    watcher.observe(element);
    return () => watcher.disconnect();
  }, [toScale]);
  return <ol ref={list} className={className} data-rail-list>
    {children}
    <li className={styles.railLines} aria-hidden="true" data-rail-lines />
    <li className={styles.railCaps} aria-hidden="true" data-rail-caps />
    {now && <li className={styles.nowFill} aria-hidden="true" data-now-fill />}
    {now && <li className={styles.nowMark} aria-hidden="true" data-now-mark><span className={styles.nowDot} />{now.kind === "today" && now.label && <span className={styles.nowLabel} data-now-label />}</li>}
  </ol>;
}
