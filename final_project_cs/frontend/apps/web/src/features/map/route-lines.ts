import type { RouteShape } from "@/lib/live/route-shapes";
import type { TripStop } from "../trip/model";
import { hasValidCoordinates } from "./map-points";
import type { MapLine } from "./model";
import { LINE_TONE_VARIABLE } from "./providers/lines";

export const MODE_NAMES: Record<RouteShape["mode"], string> = { walk: "도보", bike: "자전거", taxi: "택시", subway: "지하철", bus: "버스", mixed: "대중교통", unknown: "이동" };

/** The road is not known (a straight line between two places), or the server gives no ground for the line: it is drawn dashed and pale. */
export const isGuess = (shape: RouteShape) => shape.source === "straight_line" || shape.grade === "근거없음" || shape.source === "unknown";

/**
 * The lines that belong to the day on the map: a shape is drawn only when both stops around the move are shown with a place.
 * ★Nothing is made up — a move with no shape from the server has no line, and a shape without two usable ends is left out.
 */
export function visibleShapes(shapes: RouteShape[] | undefined, stops: TripStop[]): RouteShape[] {
  if (!shapes?.length) return [];
  const placed = new Set(stops.filter((stop) => hasValidCoordinates(stop.coordinates)).map((stop) => stop.id));
  return shapes.filter((shape) => placed.has(shape.fromItemId) && placed.has(shape.toItemId));
}

/**
 * `[2026-10-15 사용자 결정 — ST4F-495]` The walked stretches of a shape, as the server said them (`walks`). A server that does not say (`null`) has only a move that is all walking drawn so —
 * the walks at the two ends of a subway line are inside its one line, and the screen does not guess where they end. A line whose road is not known is never drawn as walking.
 */
export function walksOf(shape: RouteShape): [number, number][] {
  if (isGuess(shape)) return [];
  return shape.walks ?? (shape.mode === "walk" ? [[0, shape.points.length - 1]] : []);
}

export function toMapLines(shapes: RouteShape[]): MapLine[] {
  return shapes.map((shape) => ({
    id: shape.itemId,
    points: shape.points,
    dashed: isGuess(shape),
    mode: shape.mode,
    walks: walksOf(shape),
    title: `${shape.from ?? "출발"} → ${shape.to ?? "도착"} · ${MODE_NAMES[shape.mode]}${shape.distanceM !== null ? ` ${shape.distanceM >= 1000 ? `${(shape.distanceM / 1000).toFixed(1)}km` : `${Math.round(shape.distanceM)}m`}` : ""}`,
  }));
}

/** What the lines on the map do NOT mean, in one short sentence each — shown with the map, only for the kinds that are drawn. */
export function routeNotes(shapes: RouteShape[]): string[] {
  const notes: string[] = [];
  if (shapes.some(isGuess)) notes.push("점선은 길을 몰라 두 곳을 직선으로 이은 구간이에요.");
  // `[2026-10-15 ST4F-495]` Walking is drawn as round dots: when a map also has a guess, the dots are told apart from its dashes.
  if (shapes.some(isGuess) && shapes.some((shape) => walksOf(shape).length > 0)) notes.push("동그란 점은 걷는 구간이에요.");
  if (shapes.some((shape) => shape.source === "stations" || (shape.source === "transit_stops" && shape.rides.some((ride) => ride.mode !== "bus")))) notes.push("지하철 구간은 역 위치를 순서대로 이은 선이라 실제 선로 모양이 아니에요.");
  if (shapes.some((shape) => shape.source === "bus_stops" || (shape.source === "transit_stops" && shape.rides.some((ride) => ride.mode === "bus")))) notes.push("버스 구간은 정류장 위치를 순서대로 이은 선이라 실제 도로 모양이 아니에요.");
  if (shapes.some((shape) => (shape.mode === "bus" || shape.mode === "mixed") && shape.source !== "bus_stops" && shape.source !== "transit_stops")) notes.push("버스는 정류장 정보가 없어 직선으로 이어요.");
  // The server says why for each guess (`note`): the road finder was off, no stop list … — shown as it wrote it, once for each distinct sentence.
  const reasons = new Set(shapes.filter(isGuess).flatMap((shape) => shape.note ? [`${shape.from ?? "출발"} → ${shape.to ?? "도착"}: ${shape.note}`] : []));
  return [...notes, ...reasons];
}

/** One kind of route that is drawn, for the legend over the map: its name and the theme colour its lines have. */
export interface RouteTag { mode: RouteShape["mode"]; label: string; variable: string }

const TAG_ORDER: readonly RouteShape["mode"][] = ["subway", "mixed", "bus", "taxi", "walk", "bike", "unknown"];

/**
 * `[2026-10-05 사용자 지시 — 지도에 포커스가 있을 때 하단에 「(선 색) ── 지하철」처럼 태그로]` What the legend over the map says: the kinds of route that are drawn (each with the colour of its lines) and how many
 * of the routes are only a guess (a straight, dashed line) - the odd ones, said once and short; the detail is under the line when it is pressed. Nothing for a map with no lines.
 */
export function routeTags(shapes: RouteShape[]): { kinds: RouteTag[]; guessed: number } {
  const present = new Set(shapes.map((shape) => shape.mode));
  const kinds = TAG_ORDER.filter((mode) => present.has(mode)).map((mode) => ({ mode, label: MODE_NAMES[mode], variable: LINE_TONE_VARIABLE[mode] }));
  return { kinds, guessed: shapes.filter(isGuess).length };
}

/** The rides of a subway or mixed line, one sentence each: 「지하철 3호선 · 경복궁→을지로3가 · 4개 역」 (+ 「역 사이는 직선」 when the stations between could not be filled in). */
export function rideLines(shape: RouteShape, t: (ko: string, en: string) => string): string[] {
  return shape.rides.map((ride) => [
    ride.line,
    ride.service === "express" ? t("급행", "express") : ride.service === "local" ? t("일반", "local") : null,
    ride.from && ride.to ? `${ride.from}→${ride.to}` : null,
    ride.count !== null ? (ride.mode === "bus" ? t(`${ride.count}개 정류장`, `${ride.count} stops`) : t(`${ride.count}개 역`, `${ride.count} stations`)) : null,
    ride.filled ? null : (ride.mode === "bus" ? t("정류장 사이는 직선", "straight between stops") : t("역 사이는 직선", "straight between stations")),
  ].filter(Boolean).join(" · "));
}
