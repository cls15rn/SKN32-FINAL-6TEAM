import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetSessionState } from "./client";
import { getRouteShapes, pointsOf, readRouteShapes } from "./route-shapes";
import { answeringSession } from "./session-kit";

const shape = (patch: Record<string, unknown> = {}) => ({
  item_id: "m1", from_item_id: "a", to_item_id: "b", from: "경복궁", to: "광장시장", mode: "walk",
  line: { type: "LineString", coordinates: [[126.977, 37.5796], [126.99, 37.57], [127.0, 37.57]] },
  source: "local_road_graph", grade: "추정", distance_m: 1830, note: null, ...patch,
});

describe("reading the route lines the server gives", () => {
  it("turns GeoJSON [lng, lat] into {lat, lng} in the order of the line, and keeps what the server said about it", () => {
    const { attribution, shapes } = readRouteShapes({ trip_id: "t1", attribution: "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)", shapes: [shape()] });
    expect(attribution).toContain("ODbL");
    expect(shapes).toHaveLength(1);
    expect(shapes[0]).toMatchObject({ itemId: "m1", fromItemId: "a", toItemId: "b", from: "경복궁", to: "광장시장", mode: "walk", source: "local_road_graph", grade: "추정", distanceM: 1830, note: null });
    expect(shapes[0].points[0]).toEqual({ lat: 37.5796, lng: 126.977 });          // ★the first number is the longitude
    expect(shapes[0].points).toHaveLength(3);
  });

  it("drops what it cannot draw: a point that is not a finite pair in range, a line with fewer than two points, a shape without its two ends", () => {
    expect(pointsOf([[126.9, 37.5], ["x", 1], [200, 10], [127.0, 91], [127.0, 37.6]])).toEqual([{ lat: 37.5, lng: 126.9 }, { lat: 37.6, lng: 127.0 }]);
    expect(pointsOf([[126.9, 37.5]])).toEqual([]);                                  // one point is not a line
    expect(pointsOf("nope")).toEqual([]);
    const { shapes } = readRouteShapes({ shapes: [
      shape({ line: { type: "Point", coordinates: [126.9, 37.5] } }),               // not a LineString
      shape({ from_item_id: null }),                                                // no start stop
      shape({ item_id: "ok" }),
      null,
    ] });
    expect(shapes.map((entry) => entry.itemId)).toEqual(["ok"]);
  });

  it("`[2026-10-15 ST4F-495]` keeps the walked stretches the server said, and none it cannot trust: off the line, out of order, or on a line it had to drop a point of", () => {
    const walks = (patch: Record<string, unknown>) => readRouteShapes({ shapes: [shape(patch)] }).shapes[0].walks;
    expect(walks({ walks: [[0, 2]] })).toEqual([[0, 2]]);
    expect(walks({ walks: [] })).toEqual([]);
    expect(walks({})).toBeNull();                                                    // an older server says nothing
    expect(walks({ walks: [[0, 3]] })).toBeNull();                                   // past the end of the line
    expect(walks({ walks: [[1, 1]] })).toBeNull();                                   // not a stretch
    expect(walks({ walks: [[1, 2], [0, 1]] })).toBeNull();                           // out of order
    expect(walks({ walks: [[0, 1.5]] })).toBeNull();
    expect(walks({ walks: [[0, 1]], line: { type: "LineString", coordinates: [[126.977, 37.5796], ["x", 1], [126.99, 37.57], [127.0, 37.57]] } })).toBeNull();
  });

  it("names a shape of a plan not registered yet (no move item) by the two stops around it", () => {
    const { shapes } = readRouteShapes({ shapes: [shape({ item_id: undefined }), shape({ item_id: "", from_item_id: "0-1", to_item_id: "0-2" }), shape({ item_id: undefined, to_item_id: "" })] });
    expect(shapes.map((entry) => entry.itemId)).toEqual(["a:b", "0-1:0-2"]);        // the third has no second stop: nothing to draw
  });

  it("reads a kind it does not know as 'unknown'' — never as a good road — and keeps the attribution text, with a fallback when the server leaves it out", () => {
    const { attribution, shapes } = readRouteShapes({ shapes: [shape({ mode: "hover", source: "magic", grade: "great", distance_m: -5, note: "  " })] });
    expect(shapes[0]).toMatchObject({ mode: "unknown", source: "unknown", grade: "unknown", distanceM: null, note: null });
    expect(attribution).toContain("OpenStreetMap");
    expect(readRouteShapes(null)).toMatchObject({ shapes: [] });
  });
});

describe("asking for the lines of a trip", () => {
  let replies: Response[];
  let calls: string[];

  beforeEach(() => {
    replies = [];
    calls = [];
    vi.stubGlobal("window", { localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, dispatchEvent: () => true });
    vi.stubGlobal("fetch", answeringSession(async (url) => { calls.push(url); return replies.shift() ?? new Response("{}", { status: 200 }); }));
  });
  afterEach(() => { vi.unstubAllGlobals(); resetSessionState(); });

  it("reads the lines from the trip's own route", async () => {
    replies.push(new Response(JSON.stringify({ trip_id: "t 1", attribution: "경로선: …", shapes: [shape()] }), { status: 200 }));
    const result = await getRouteShapes("t 1", "ko");
    expect(result?.shapes).toHaveLength(1);
    expect(calls[0]).toMatch(/\/v1\/web\/trips\/t%201\/route-shapes$/);
  });

  it("takes 404 and 405 as 'the server has no such route yet' (null), not as a failure; any other refusal is a failure", async () => {
    replies.push(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    expect(await getRouteShapes("t1", "ko")).toBeNull();
    replies.push(new Response(JSON.stringify({ error: { code: "not_found", message: "resource not found" } }), { status: 404 }));
    expect(await getRouteShapes("t1", "ko")).toBeNull();
    replies.push(new Response("{}", { status: 405 }));
    expect(await getRouteShapes("t1", "ko")).toBeNull();
    replies.push(new Response(JSON.stringify({ error: { code: "internal_error", message: "서버 오류" } }), { status: 500 }));
    await expect(getRouteShapes("t1", "ko")).rejects.toMatchObject({ code: "internal_error" });
  });
});

describe("rides of a subway line", () => {
  const line = { type: "LineString", coordinates: [[126.977, 37.5796], [127.0, 37.57]] };
  const read = (rides: unknown) => readRouteShapes({ shapes: [{ item_id: "m1", from_item_id: "a", to_item_id: "b", mode: "subway", source: "stations", grade: "추정", line, rides }] }).shapes[0].rides;

  it("keeps an explicit train service and leaves missing or unknown kinds unlabeled", () => {
    const rides = read([{line:"9호선",service:"express"},{line:"9호선",service:"local"},{line:"1호선"},{line:"9호선",service:"maybe"}]);
    expect(rides.map(ride=>ride.service)).toEqual(["express","local",undefined,undefined]);
  });

  it("reads each ride, and says the stations between are missing only when the server says `filled: false`", () => {
    expect(read([{ line: "지하철 3호선", from: "경복궁", to: "을지로3가", stations: ["경복궁", "", "을지로3가"], count: 4, filled: true }, { line: "지하철 1호선", from: "A", to: "B", filled: false }]))
      .toEqual([{ line: "지하철 3호선", from: "경복궁", to: "을지로3가", stations: ["경복궁", "을지로3가"], count: 4, filled: true }, { line: "지하철 1호선", from: "A", to: "B", stations: [], count: null, filled: false }]);
  });

  it("drops a ride with no line name, and reads no rides from anything that is not a list (a bus, a taxi, an older server)", () => {
    expect(read([{ from: "A", to: "B" }, "x", null])).toEqual([]);
    expect(read(undefined)).toEqual([]);
    expect(read("지하철")).toEqual([]);
  });
});
