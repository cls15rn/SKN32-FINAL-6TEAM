import type { Language } from "../i18n";
import { api, LiveError } from "./client";

/**
 * `[2026-10-04]` The lines to draw on the trip's map (`GET /v1/web/trips/{id}/route-shapes`, mobility session — report 「이동 파이썬 길찾기 서버없이」 §10).
 *   One shape for each move that has a placed stop before and after it. ★The server says how sure it is, and the screen must show that:
 *   - `source: "straight_line"` or `grade: "근거없음"` = the road was not known, the two places were joined by a straight line → dashed and pale;
 *   - `source: "stations"`, `"bus_stops"` or `"transit_stops"` = the chosen station/stop coordinates joined in order, not the real track/road — said with the map;
 *   - the line's own `attribution` (ODbL) is shown whenever a line is drawn.
 */
export type RouteMode = "walk" | "bike" | "taxi" | "subway" | "bus" | "mixed" | "unknown";
export type RouteSource = "local_road_graph" | "stations" | "bus_stops" | "transit_stops" | "straight_line";

/** `[2026-10-05 이동 세션 계약]` One ride of a subway or mixed leg (`rides[]`, only where the line follows the stations): 「지하철 3호선 · 경복궁→을지로3가 · 4개 역」; `filled: false` = the stations between could not be filled in (a straight stretch). */
export interface RouteRide { mode?: "bus" | "subway"; service?: "local" | "express"; line: string; from: string | null; to: string | null; stations: string[]; count: number | null; filled: boolean }

export interface RouteShape {
  /** The move item itself; the two ends are the stops around it. */
  itemId: string;
  fromItemId: string;
  toItemId: string;
  from: string | null;
  to: string | null;
  mode: RouteMode;
  source: RouteSource | "unknown";
  grade: "추정" | "근거없음" | "unknown";
  distanceM: number | null;
  note: string | null;
  /** WGS84, in the order of the line (the server sends GeoJSON `[lng, lat]`). */
  points: { lat: number; lng: number }[];
  /** The rides a bus, subway or mixed line is made of, said when the line is pressed. Empty for a taxi or walk. */
  rides: RouteRide[];
  /**
   * `[2026-10-15 사용자 결정 — ST4F-495]` The stretches of `points` that are walked (`walks`: `[first, last]` indexes, ends included) — the whole of a walk, and the walks to, between
   * and from the stations of a subway or bus line — drawn as walking. `null` = the server did not say (one before this field): only a move that is all walking is drawn so.
   */
  walks: [number, number][] | null;
}

export interface RouteShapes { attribution: string; shapes: RouteShape[] }

const text = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value : null);
const MODES: readonly RouteMode[] = ["walk", "bike", "taxi", "subway", "bus", "mixed", "unknown"];
const SOURCES: readonly RouteShape["source"][] = ["local_road_graph", "stations", "bus_stops", "transit_stops", "straight_line"];

/** `[[lng, lat], …]` → points. A point that is not a pair of finite, in-range numbers is dropped; fewer than two left means no line. */
export function pointsOf(coordinates: unknown): RouteShape["points"] {
  if (!Array.isArray(coordinates)) return [];
  const points = coordinates.flatMap((pair): RouteShape["points"] => {
    if (!Array.isArray(pair) || pair.length < 2) return [];
    const [lng, lat] = [Number(pair[0]), Number(pair[1])];
    return Number.isFinite(lng) && Number.isFinite(lat) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180 ? [{ lat, lng }] : [];
  });
  return points.length >= 2 ? points : [];
}

function ridesOf(raw: unknown): RouteRide[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry): RouteRide[] => {
    const row = (entry ?? {}) as Record<string, unknown>;
    const line = text(row.line);
    if (!line) return [];                                                   // a ride with no line name has nothing true to say
    const count = Number(row.count);
    return [{
      ...(row.mode === "bus" || row.mode === "subway" ? { mode: row.mode } : {}),
      ...(row.service === "local" || row.service === "express" ? { service: row.service } : {}),
      line, from: text(row.from), to: text(row.to),
      stations: (Array.isArray(row.stations) ? row.stations : []).flatMap((station) => { const name = text(station); return name ? [name] : []; }),
      count: Number.isFinite(count) && count >= 0 ? Math.floor(count) : null,
      filled: row.filled !== false,                                         // only the server's explicit `false` says the middle stations are missing
    }];
  });
}

/** The server's walking stretches, kept only when they point into the line it sent whole (a dropped point would shift every index after it) and run in order without overlapping. */
function walksOf(raw: unknown, coordinates: unknown, points: number): RouteShape["walks"] {
  if (!Array.isArray(raw)) return null;
  if (!Array.isArray(coordinates) || coordinates.length !== points) return null;
  const walks: [number, number][] = [];
  for (const span of raw) {
    if (!Array.isArray(span) || span.length !== 2) return null;
    const [first, last] = span;
    if (!Number.isInteger(first) || !Number.isInteger(last) || first < (walks.at(-1)?.[1] ?? 0) || last <= first || last >= points) return null;
    walks.push([first, last]);
  }
  return walks;
}

function shapeOf(entry: unknown): RouteShape | null {
  const row = (entry ?? {}) as Record<string, unknown>;
  const line = (row.line ?? {}) as { type?: unknown; coordinates?: unknown };
  const points = line.type === "LineString" ? pointsOf(line.coordinates) : [];
  const fromItemId = text(row.from_item_id), toItemId = text(row.to_item_id);
  // A registered trip's shape names its move item; one for a plan not registered yet (`trip-intakes/{id}/route-shapes`) has none — the pair of stops is its name.
  const itemId = text(row.item_id) ?? (fromItemId && toItemId ? `${fromItemId}:${toItemId}` : null);
  if (!itemId || !fromItemId || !toItemId || points.length < 2) return null;
  const mode = MODES.find((value) => value === row.mode) ?? "unknown";
  const source = SOURCES.find((value) => value === row.source) ?? "unknown";
  const distance = Number(row.distance_m);
  return {
    itemId, fromItemId, toItemId, from: text(row.from), to: text(row.to), mode, source,
    grade: row.grade === "추정" || row.grade === "근거없음" ? row.grade : "unknown",
    distanceM: Number.isFinite(distance) && distance >= 0 ? distance : null, note: text(row.note), points, rides: ridesOf(row.rides),
    walks: walksOf(row.walks, line.coordinates, points.length),
  };
}

/**
 * `[2026-10-05 사용자 지시 · 이동 세션 계약]` `?detail=true` asks for the DETAILED lines (error 0.5 m, up to 5,000 points a line) for a map zoomed in; without it the lines are the short ones
 * (error 2 m, up to 240 points) that are enough zoomed out. The server's answer says which it is (`detail`).
 */
const detailQuery = (options: { detail?: boolean }) => options.detail ? "?detail=true" : "";

/** The server says nothing about this route yet (an older server: 404/405) — that is not an error, there are simply no lines. */
const unsupported = (error: unknown) => error instanceof LiveError && ["not_found", "method_not_allowed", "HTTP_404", "HTTP_405"].includes(error.code);

export function readRouteShapes(body: unknown): RouteShapes {
  const data = (body ?? {}) as { attribution?: unknown; shapes?: unknown };
  const shapes = (Array.isArray(data.shapes) ? data.shapes : []).map(shapeOf).filter((shape): shape is RouteShape => shape !== null);
  return { attribution: text(data.attribution) ?? "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)", shapes };
}

/**
 * `[2026-10-04]` The same lines for a plan the server has taken but not registered (`GET /v1/web/trip-intakes/{id}/route-shapes`, mobility session): drawn on the plan check's map.
 * ★The stops are the check screen's (`review.items[].id`); it reads only the check the server stored, so a plan it has not checked has none (`shapes: []`).
 * `null` = the server has no such route (an older one).
 */
export async function getIntakeRouteShapes(intakeId: string, language: Language, options: { detail?: boolean } = {}): Promise<RouteShapes | null> {
  try {
    return readRouteShapes(await api<unknown>(`/v1/web/trip-intakes/${encodeURIComponent(intakeId)}/route-shapes${detailQuery(options)}`, language));
  } catch (error) {
    if (unsupported(error)) return null;
    throw error;
  }
}

/** `null` = the server has no such route. The first call after a server restart can wait several seconds while it loads its road data. */
export async function getRouteShapes(tripId: string, language: Language, options: { detail?: boolean } = {}): Promise<RouteShapes | null> {
  try {
    return readRouteShapes(await api<unknown>(`/v1/web/trips/${encodeURIComponent(tripId)}/route-shapes${detailQuery(options)}`, language));
  } catch (error) {
    if (unsupported(error)) return null;
    throw error;
  }
}
