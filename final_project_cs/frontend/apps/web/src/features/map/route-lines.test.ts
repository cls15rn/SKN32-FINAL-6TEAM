import { describe, expect, it, vi } from "vitest";
import type { RouteShape } from "@/lib/live/route-shapes";
import type { TripStop } from "../trip/model";
import { isGuess, rideLines, routeNotes, routeTags, toMapLines, visibleShapes } from "./route-lines";
import { linePieces, lineStyle, linesKey, walkStyle } from "./providers/lines";

const stop = (id: string, coordinates: TripStop["coordinates"]): TripStop => ({ id, title: id, date: "2026-10-01", time: "09:00", coordinates } as TripStop);
const shape = (patch: Partial<RouteShape> = {}): RouteShape => ({
  itemId: "m1", fromItemId: "a", toItemId: "b", from: "경복궁", to: "광장시장", mode: "walk", source: "local_road_graph", grade: "추정", distanceM: 1830, note: null, rides: [], walks: null,
  points: [{ lat: 37.5796, lng: 126.977 }, { lat: 37.57, lng: 127.0 }], ...patch,
});

describe("which route lines go on the map", () => {
  const stops = [stop("a", { lat: 37.5796, lng: 126.977 }), stop("b", { lat: 37.57, lng: 127.0 }), stop("c", null)];

  it("draws a line only when both stops around the move are on the day shown and have a place", () => {
    const lines = visibleShapes([shape(), shape({ itemId: "m2", fromItemId: "b", toItemId: "c" }), shape({ itemId: "m3", fromItemId: "x", toItemId: "a" })], stops);
    expect(lines.map((entry) => entry.itemId)).toEqual(["m1"]);              // c has no place, x is another day
    expect(visibleShapes(undefined, stops)).toEqual([]);
    expect(visibleShapes([], stops)).toEqual([]);
  });

  it("draws a guess dashed: a straight line, no ground for it, or a kind it does not know — a real road is solid", () => {
    expect(isGuess(shape())).toBe(false);
    expect(isGuess(shape({ source: "stations", mode: "subway" }))).toBe(false);   // stations are joined in order: solid, but the caption says what it is
    expect(isGuess(shape({ source: "straight_line" }))).toBe(true);
    expect(isGuess(shape({ grade: "근거없음" }))).toBe(true);
    expect(isGuess(shape({ source: "unknown" }))).toBe(true);
    expect(toMapLines([shape(), shape({ itemId: "m2", source: "straight_line" })]).map((line) => [line.id, line.dashed])).toEqual([["m1", false], ["m2", true]]);
  });

  it("names a line for the pointer and the screen reader: where from, where to, how, how far", () => {
    expect(toMapLines([shape()])[0].title).toBe("경복궁 → 광장시장 · 도보 1.8km");
    expect(toMapLines([shape({ distanceM: 420, mode: "taxi" })])[0].title).toBe("경복궁 → 광장시장 · 택시 420m");
    expect(toMapLines([shape({ from: null, to: null, distanceM: null, mode: "unknown" })])[0].title).toBe("출발 → 도착 · 이동");
  });

  it("says what the drawn lines do NOT mean — only for the kinds that are on the map", () => {
    expect(routeNotes([shape()])).toEqual([]);
    expect(routeNotes([shape({ source: "straight_line" })])).toEqual(["점선은 길을 몰라 두 곳을 직선으로 이은 구간이에요."]);
    expect(routeNotes([shape({ source: "stations", mode: "subway" })])).toEqual(["지하철 구간은 역 위치를 순서대로 이은 선이라 실제 선로 모양이 아니에요."]);
    expect(routeNotes([shape({ source: "straight_line", mode: "bus" })])).toEqual(["점선은 길을 몰라 두 곳을 직선으로 이은 구간이에요.", "버스는 정류장 정보가 없어 직선으로 이어요."]);
  });

  it("adds the server's own reason for each guess, once for each distinct sentence — and none for a line that is not a guess", () => {
    const reasons = routeNotes([
      shape({ source: "straight_line", mode: "taxi", note: "길찾기가 꺼져 있어 직선으로 이었어요" }),
      shape({ itemId: "m2", source: "straight_line", mode: "taxi", note: "길찾기가 꺼져 있어 직선으로 이었어요" }),     // the same sentence for the same places: once
      shape({ itemId: "m3", from: "광장시장", to: "N서울타워", grade: "근거없음", note: "좌표가 근사값이에요" }),
      shape({ itemId: "m4", source: "stations", mode: "subway", note: "역 사이는 역 좌표를 순서대로 잇는다" }),            // not a guess: the subway sentence above says it
    ]);
    expect(reasons.filter((line) => line.includes(": "))).toEqual(["경복궁 → 광장시장: 길찾기가 꺼져 있어 직선으로 이었어요", "광장시장 → N서울타워: 좌표가 근사값이에요"]);
  });
});

describe("the legend over the map and the colour of each kind of route", () => {
  const t = (ko: string) => ko;

  it("labels the saved express service without guessing a service for an older ride", () => {
    const ride = {line:"9호선",from:"노량진",to:"고속터미널",stations:["노량진","동작","고속터미널"],count:3,filled:true};
    expect(rideLines(shape({mode:"subway",rides:[{...ride,service:"express"},ride]}),(ko)=>ko))
      .toEqual(["9호선 · 급행 · 노량진→고속터미널 · 3개 역","9호선 · 노량진→고속터미널 · 3개 역"]);
  });

  it("names each kind that is drawn once, in a fixed order, and counts the routes that are only a guess", () => {
    const tags = routeTags([shape({ mode: "walk" }), shape({ itemId: "m2", mode: "subway", source: "straight_line", grade: "근거없음" }), shape({ itemId: "m3", mode: "subway" }), shape({ itemId: "m4", mode: "bus", source: "straight_line" })]);
    expect(tags.kinds.map((kind) => kind.label)).toEqual(["지하철", "버스", "도보"]);
    expect(tags.guessed).toBe(2);
    expect(routeTags([])).toEqual({ kinds: [], guessed: 0 });
  });

  it("gives a line the colour of its kind (a subway is blue) - and the legend reads the same colour variable", () => {
    expect(toMapLines([shape({ mode: "subway" })])[0].mode).toBe("subway");
    // The tests run without a page: the colours the browser would resolve are given by a stand-in of `getComputedStyle`.
    const vars: Record<string, string> = { "--color-adjusted": "#3f6a8a", "--color-success": "#3d7a56", "--color-primary": "#2f6f4f" };
    vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (name: string) => vars[name] ?? "" }));
    const container = {} as HTMLElement;
    expect(lineStyle(container, { id: "a", points: [], dashed: false, mode: "subway", title: "" }).color).toBe("#3f6a8a");
    expect(lineStyle(container, { id: "b", points: [], dashed: false, mode: "bus", title: "" }).color).toBe("#3d7a56");
    expect(lineStyle(container, { id: "c", points: [], dashed: false, title: "" }).color).toBe("#2f6f4f");          // no kind: the theme's colour as before
    expect(routeTags([shape({ mode: "subway" })]).kinds[0].variable).toBe("--color-adjusted");
    vi.unstubAllGlobals();
  });

  it("redraws the lines when only their kind changed", () => {
    const line = { id: "a", points: [{ lat: 1, lng: 2 }], dashed: false, title: "" };
    expect(linesKey([{ ...line, mode: "subway" }])).not.toBe(linesKey([{ ...line, mode: "bus" }]));
  });

  it("says what a subway line rides, one sentence for each ride, and that the stations between are a straight stretch when the server could not fill them in", () => {
    const line = shape({ mode: "subway", rides: [
      { line: "지하철 3호선", from: "경복궁", to: "을지로3가", stations: ["경복궁", "안국", "충무로", "을지로3가"], count: 4, filled: true },
      { line: "지하철 1호선", from: "을지로3가", to: "종각", stations: [], count: null, filled: false },
    ] });
    expect(rideLines(line, t)).toEqual(["지하철 3호선 · 경복궁→을지로3가 · 4개 역", "지하철 1호선 · 을지로3가→종각 · 역 사이는 직선"]);
    expect(rideLines(shape(), t)).toEqual([]);
  });
});


describe("`[2026-10-15 사용자 결정 — ST4F-495]` walked stretches are round grey dots, the rides keep their look", () => {
  const five = [{ lat: 1, lng: 1 }, { lat: 1, lng: 2 }, { lat: 1, lng: 3 }, { lat: 1, lng: 4 }, { lat: 1, lng: 5 }];

  it("takes the walks the server said; without them only a move that is all walking is walked — a subway line is not cut by guess", () => {
    expect(toMapLines([shape({ mode: "subway", source: "stations", points: five, walks: [[0, 1], [3, 4]] })])[0].walks).toEqual([[0, 1], [3, 4]]);
    expect(toMapLines([shape({ points: five })])[0].walks).toEqual([[0, 4]]);                                  // an older server: a walk is all walking
    expect(toMapLines([shape({ mode: "subway", source: "stations", points: five })])[0].walks).toEqual([]);    // an older server: the subway line stays whole
    expect(toMapLines([shape({ source: "straight_line", points: five, walks: [[0, 4]] })])[0].walks).toEqual([]);   // a guess stays the pale long dash
  });

  it("cuts a line at its walks into pieces that share their ends, so the line is not broken", () => {
    const line = { id: "m1", points: five, dashed: false, mode: "subway" as const, title: "", walks: [[0, 1], [3, 4]] as [number, number][] };
    expect(linePieces(line).map(({ points, walk }) => [points.map((point) => point.lng), walk])).toEqual([[[1, 2], true], [[2, 3, 4], false], [[4, 5], true]]);
    expect(linePieces({ ...line, walks: [[0, 4]] }).map(({ walk }) => walk)).toEqual([true]);
    expect(linePieces({ ...line, walks: [] })).toEqual([{ points: five, walk: false }]);
    expect(linePieces({ ...line, dashed: true })).toEqual([{ points: five, walk: false }]);                        // never cut a guess
    expect(linePieces({ ...line, walks: [[2, 9]] })).toEqual([{ points: five, walk: false }]);                     // a stretch off the line is not drawn
  });

  it("draws walking in the walking colour as dots — not the pale long dash of a guess — and redraws when only the walks changed", () => {
    vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (name: string) => ({ "--color-muted": "#8a8f98", "--color-primary": "#2f6f4f" } as Record<string, string>)[name] ?? "" }));
    const walk = walkStyle({} as HTMLElement);
    expect(walk.color).toBe("#8a8f98");
    expect(walk.dots).toBeGreaterThan(0);
    expect(lineStyle({} as HTMLElement, { id: "g", points: [], dashed: true, mode: "walk", title: "" }).dash).toEqual([8, 10]);
    vi.unstubAllGlobals();
    const line = { id: "a", points: five, dashed: false, mode: "subway" as const, title: "" };
    expect(linesKey([{ ...line, walks: [[0, 1]] }])).not.toBe(linesKey([{ ...line, walks: [] }]));
  });

  it("says which dashes are the guesses when walking dots are on the map too", () => {
    expect(routeNotes([shape(), shape({ itemId: "m2", source: "straight_line", mode: "taxi" })]).slice(0, 2)).toEqual(["점선은 길을 몰라 두 곳을 직선으로 이은 구간이에요.", "동그란 점은 걷는 구간이에요."]);
    expect(routeNotes([shape({ source: "straight_line", mode: "taxi" })])).not.toContain("동그란 점은 걷는 구간이에요.");
    expect(routeNotes([shape()])).toEqual([]);                                                               // walking alone needs no word
  });
});
