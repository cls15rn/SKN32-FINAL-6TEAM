import type { RouteShape } from "@/lib/live/route-shapes";
import type { Trip, TripChange } from "./model";
import { seoulKey, toMinutes, tripDays, type SeoulNow } from "./trip-timeline";

/**
 * `[2026-10-15 사용자 결정 — ST4F-496]` What the ending credits of a trip say — only what the server sent or the screen can see: the trip's name and days, its stops, the changes the
 * server recorded (`history`), the moves it keeps and the length of the route lines it drew. ★Nothing the server does not know is said: no 「visited」 (there is no arrival record), no
 * 「time saved」 (there is no formula), no game records. The mockup (2026-10-08 C안) is only a reference.
 */
export interface CreditFacts {
  title: string;
  /** "2026. 10. 15 — 10. 16" */
  dates: string;
  days: { label: string; titles: string[] }[];
  /** The changes after the trip was registered (version 1 is the registration), in the server's words. */
  changes: TripChange[];
  stops: number;
  /** `null`: the server keeps no moves for this trip (a plan registered from typed text). */
  moves: number | null;
  /** The length of the route lines the server drew (`distance_m`, a straight guess included); `null` when it drew none. */
  distanceM: number | null;
  /** The sources the trip's places and lines came with, as the server wrote them. */
  sources: string[];
}

/** "2026. 10. 15 — 10. 16" (the year again only when it changes). */
export function datesText(first: string, last: string): string {
  const full = (date: string) => `${date.slice(0, 4)}. ${date.slice(5, 7)}. ${date.slice(8, 10)}`;
  if (first === last) return full(first);
  return `${full(first)} — ${last.slice(0, 4) === first.slice(0, 4) ? `${last.slice(5, 7)}. ${last.slice(8, 10)}` : full(last)}`;
}

/** The trip is over once its last stop has ended (its end time, or its start when it has none) in Seoul. A trip with no stops never ends. */
export function tripEnded(trip: Pick<Trip, "stops">, now: SeoulNow): boolean {
  const ends = trip.stops.map((stop) => seoulKey({ date: stop.endDate ?? stop.date, minute: toMinutes(stop.endTime ?? stop.time) ?? 0 }));
  if (ends.length === 0) return false;
  return seoulKey(now) >= ends.sort().at(-1)!;
}

export function creditFacts(trip: Trip, title: string, shapes: readonly RouteShape[], attribution: string | null, dayText: (date: string, index: number) => string): CreditFacts {
  const days = tripDays(trip);
  const lengths = shapes.flatMap((shape) => shape.distanceM === null ? [] : [shape.distanceM]);
  const notes = trip.stops.flatMap((stop) => stop.placeInfo?.sourceNote ? [stop.placeInfo.sourceNote] : []);
  return {
    title,
    dates: days.length ? datesText(days[0], days.at(-1)!) : "",
    days: days.map((date, index) => ({ label: dayText(date, index), titles: trip.stops.filter((stop) => stop.date === date).map((stop) => stop.title) })),
    changes: (trip.history ?? []).filter((change) => change.version > 1),
    stops: trip.stops.length,
    moves: trip.moves ? trip.moves.length : null,
    distanceM: lengths.length ? lengths.reduce((sum, length) => sum + length, 0) : null,
    sources: [...new Set([...notes, ...(shapes.length && attribution ? [attribution] : [])])],
  };
}

/** "약 2.3km" · "약 480m" */
export const distanceText = (metres: number, t: (ko: string, en: string) => string) => metres >= 1000
  ? t(`약 ${(metres / 1000).toFixed(1)}km`, `About ${(metres / 1000).toFixed(1)} km`)
  : t(`약 ${Math.round(metres)}m`, `About ${Math.round(metres)} m`);

/** The numbers of the trip, as the end card, the credits and the end screen show them (a number the server does not keep is left out). */
export function creditNumbers(facts: CreditFacts, t: (ko: string, en: string) => string): [string, string][] {
  return [
    [t("일정", "Stops"), t(`${facts.stops}곳`, String(facts.stops))],
    ...(facts.moves === null ? [] : [[t("이동", "Moves"), t(`${facts.moves}번`, String(facts.moves))] as [string, string]]),
    ...(facts.distanceM === null ? [] : [[t("이동 거리", "Distance"), distanceText(facts.distanceM, t)] as [string, string]]),
    [t("바꾼 일정", "Changes"), t(`${facts.changes.length}번`, String(facts.changes.length))],
  ];
}

/** What the shared picture shows: the end screen without its buttons. */
export interface EndPicture { heading: string; dates: string; kicker: string; line: string; cells: [string, string][]; brand: string }
export interface PictureLook { background: string; ink: string; accent: string; font: string }

const W = 1080, H = 1920, SIDE = 96;

/** Lines of `text` that fit `width` (by words; a word longer than the line is cut by letters). */
function wrap(context: CanvasRenderingContext2D, text: string, width: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/(\s+)/)) {
    const next = line + word;
    if (context.measureText(next).width <= width || !line.trim()) { line = next; continue; }
    lines.push(line.trim());
    line = word.trimStart();
  }
  if (line.trim()) lines.push(line.trim());
  return lines.flatMap((one) => {
    if (context.measureText(one).width <= width) return [one];
    const cut: string[] = [];
    let part = "";
    for (const letter of one) { if (context.measureText(part + letter).width > width && part) { cut.push(part); part = ""; } part += letter; }
    return part ? [...cut, part] : cut;
  });
}

/**
 * `[2026-10-15 사용자 결정 — ST4F-496]` 「공유하기」 shares a picture of the end screen without its buttons — not a video. Drawn on a canvas (1080×1920, a phone story) in the screen's own
 * colours and font, so no library is needed. `null` when the browser cannot draw it (no canvas).
 */
export async function drawEndPicture(picture: EndPicture, look: PictureLook): Promise<Blob | null> {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const context = canvas.getContext?.("2d");
  if (!context) return null;
  await document.fonts?.ready;
  const spaced = (pixels: number) => { if ("letterSpacing" in context) (context as { letterSpacing: string }).letterSpacing = `${pixels}px`; };
  context.fillStyle = look.background;
  context.fillRect(0, 0, W, H);
  context.textAlign = "center";
  context.textBaseline = "alphabetic";
  context.fillStyle = look.ink;

  context.globalAlpha = 0.92;
  context.font = `800 52px ${look.font}`;
  const heading = wrap(context, picture.heading, W - SIDE * 2).slice(0, 2);
  heading.forEach((text, at) => context.fillText(text, W / 2, 300 + at * 66));
  context.globalAlpha = 0.6;
  context.font = `600 34px ${look.font}`;
  context.fillText(picture.dates, W / 2, 300 + heading.length * 66 + 10);

  context.globalAlpha = 1;
  context.fillStyle = look.accent;
  context.font = `700 32px ${look.font}`;
  spaced(10);
  context.fillText(picture.kicker, W / 2, 760);
  spaced(0);
  context.fillStyle = look.ink;
  context.font = `750 64px ${look.font}`;
  const line = wrap(context, picture.line, W - SIDE * 2).slice(0, 3);
  line.forEach((text, at) => context.fillText(text, W / 2, 860 + at * 82));

  const top = 860 + line.length * 82 + 60, gap = 24, cellW = (W - SIDE * 2 - gap) / 2, cellH = 156;
  context.textAlign = "left";
  picture.cells.forEach(([label, value], at) => {
    const x = SIDE + (at % 2) * (cellW + gap), y = top + Math.floor(at / 2) * (cellH + gap);
    context.globalAlpha = 0.07;
    context.fillStyle = look.ink;
    context.beginPath();
    context.roundRect?.(x, y, cellW, cellH, 34);
    context.fill();
    context.globalAlpha = 0.62;
    context.font = `600 30px ${look.font}`;
    context.fillText(label, x + 34, y + 58);
    context.globalAlpha = 1;
    context.font = `800 46px ${look.font}`;
    context.fillText(value, x + 34, y + 118, cellW - 68);
  });

  context.textAlign = "center";
  context.globalAlpha = 0.5;
  context.font = `800 36px ${look.font}`;
  context.fillText(picture.brand, W / 2, H - 140);
  context.globalAlpha = 1;
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/png"));
}
