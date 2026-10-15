import { describe, expect, it } from "vitest";
import type { RouteShape } from "@/lib/live/route-shapes";
import type { Trip, TripStop } from "./model";
import { between, dayTimeline, distanceText, entryTimes, tripDays } from "./trip-timeline";

const stop = (id: string, date: string, time: string, endTime?: string): TripStop => ({ id, date, time, endTime, title: id, booking: "unknown", notes: "" });
const shape = (itemId: string, from: string, to: string): RouteShape => ({ itemId, fromItemId: from, toItemId: to, from, to, mode: "subway", source: "stations", grade: "추정", distanceM: 2100, note: null, points: [], rides: [], walks: null });

const trip: Pick<Trip, "stops" | "moves" | "legs"> = {
  stops: [stop("a", "2026-10-14", "10:00", "11:30"), stop("b", "2026-10-14", "12:30", "13:40"), stop("c", "2026-10-14", "15:00"), stop("d", "2026-10-15", "09:30")],
  moves: [{ id: "m1", fromId: "a", toId: "b", date: "2026-10-14", departAt: "11:30", arriveAt: "11:45", title: "a → b" }],
  legs: { "b>c": "https://www.google.com/maps/dir/?api=1&origin=b&destination=c" },
};

describe("trip timeline", () => {
  // `[2026-10-13 사용자 결정 — 숙소 항목 B안]` One stay (kind lodging) from its check-in day to its check-out day.
  const hotel: TripStop = { ...stop("h", "2026-11-06", "15:00", "11:00"), kind: "lodging", endDate: "2026-11-09", title: "롯데호텔 서울" };
  const stayTrip: Pick<Trip, "stops" | "moves" | "legs"> = {
    stops: [stop("lunch", "2026-11-06", "12:30", "13:30"), hotel, stop("dinner", "2026-11-06", "18:00", "19:00"),
      stop("tower", "2026-11-07", "10:00", "12:00"), stop("market", "2026-11-09", "09:30", "10:30"), stop("palace", "2026-11-09", "13:00", "15:00")],
    moves: [], legs: {},
  };
  const ids = (date: string) => dayTimeline(stayTrip, date).map((entry) => entry.type === "stop" ? entry.stop.id : entry.type === "leg" ? "leg" : `${entry.stay.phase}:${entry.stay.night}/${entry.stay.nights}`);

  it("puts the server's morning move from the hotel right under the band", () => {
    const withMove = { ...stayTrip, moves: [{ id: "m-am", fromId: "h", toId: "tower", date: "2026-11-07", departAt: "09:30", arriveAt: "09:50", title: "롯데호텔 → 남산" }] };
    const entries = dayTimeline(withMove, "2026-11-07");
    expect(entries.map((entry) => entry.type === "stop" ? entry.stop.id : entry.type === "leg" ? entry.leg.id : entry.stay.phase)).toEqual(["from", "m-am", "tower"]);
    const leg = entries[1];
    expect(leg.type === "leg" && leg.leg).toMatchObject({ minutes: 20, slack: 10, from: { id: "h" }, to: { id: "tower" } });
  });

  it("lists every day a stay runs over, even a day with nothing else", () => {
    expect(tripDays(stayTrip)).toEqual(["2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"]);
  });

  it("draws the stay as a stop on its check-in day, a band on the days after, and the check-out at its time on the last day", () => {
    expect(ids("2026-11-06")).toEqual(["lunch", "leg", "h", "leg", "dinner"]);
    expect(ids("2026-11-07")).toEqual(["from:2/3", "tower"]);
    expect(ids("2026-11-08")).toEqual(["from:3/3"]);
    expect(ids("2026-11-09")).toEqual(["from:4/3", "market", "leg", "checkout:4/3", "palace"]);
  });

  // `[2026-10-13 develop 병합]` The stay marks meet the 「now」 line (10-12 B): a band or check-out is a moment, not a span, and the check-in stop does not run to the check-out day.
  it("times a day with stay marks: the band and the check-out are moments, the check-in stop ends with the day", () => {
    const last = dayTimeline(stayTrip, "2026-11-09");
    const times = entryTimes(last, "2026-11-09", { date: "2026-11-09", minute: 12 * 60 });
    expect(times.map((time) => time.state)).toEqual(["past", "past", "now", "past", "next"]);
    expect(times[3]).toMatchObject({ from: 11 * 60, to: 11 * 60 });
    const first = entryTimes(dayTimeline(stayTrip, "2026-11-06"), "2026-11-06", { date: "2026-11-06", minute: 16 * 60 });
    expect(first[2]).toMatchObject({ state: "now", from: 15 * 60, to: 18 * 60 });
  });

  it("lists the trip's days in order", () => {
    expect(tripDays({ stops: [stop("x", "2026-10-15", "09:00"), stop("y", "2026-10-14", "09:00"), stop("z", "2026-10-15", "10:00")] })).toEqual(["2026-10-14", "2026-10-15"]);
  });

  it("puts a leg between each two stops of the day: the server's move when it keeps one, else the two stops and their directions", () => {
    const entries = dayTimeline(trip, "2026-10-14", [shape("m1", "a", "b")]);
    expect(entries.map((entry) => entry.type === "stop" ? entry.stop.id : entry.type === "leg" ? entry.leg.id : entry.stay.phase)).toEqual(["a", "m1", "b", "b>c", "c"]);
    const [, first, , second] = entries;
    expect(first.type === "leg" && first.leg).toMatchObject({ minutes: 15, slack: 45, directions: null, shape: { itemId: "m1" } });
    expect(second.type === "leg" && second.leg).toMatchObject({ move: null, minutes: null, slack: null, shape: null, directions: "https://www.google.com/maps/dir/?api=1&origin=b&destination=c" });
  });

  it("finds a route line by its two stops when it is not keyed by a move, and says a late arrival as negative slack", () => {
    const late = { ...trip, moves: [{ id: "m1", fromId: "a", toId: "b", date: "2026-10-14", departAt: "11:30", arriveAt: "12:40", title: "a → b" }] };
    const [, leg, , other] = dayTimeline(late, "2026-10-14", [shape("s-bc", "b", "c")]);
    expect(leg.type === "leg" && leg.leg.slack).toBe(-10);
    expect(other.type === "leg" && other.leg.shape?.itemId).toBe("s-bc");
  });

  it("reads minutes and distances only from what the server gave", () => {
    expect(between("11:30", "12:05")).toBe(35);
    expect(between("11:30", null)).toBeNull();
    expect(between("오전", "12:00")).toBeNull();
    expect(distanceText(850)).toBe("850m");
    expect(distanceText(2140)).toBe("2.1km");
    expect(distanceText(null)).toBeNull();
    expect(distanceText(0)).toBeNull();
  });
});

describe("now on the trip (2026-10-12 사용자 결정)", () => {
  const at = (date: string, hm: string) => ({ date, minute: Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3)) });
  const states = (date: string, now: ReturnType<typeof at>) => entryTimes(dayTimeline(trip, date), date, now).map((time) => time.state);

  it("a stop is over when its end passes, a way when the next stop starts; days before today are over, days after are to come", () => {
    // a 10:00–11:30, m1 11:30→11:45, b 12:30–13:40, b>c (no move), c 15:00–(no end)
    expect(states("2026-10-14", at("2026-10-14", "11:40"))).toEqual(["past", "now", "next", "next", "next"]);
    expect(states("2026-10-14", at("2026-10-14", "13:40"))).toEqual(["past", "past", "past", "now", "next"]);
    expect(states("2026-10-14", at("2026-10-15", "08:00"))).toEqual(["past", "past", "past", "past", "past"]);
    expect(states("2026-10-15", at("2026-10-14", "23:00"))).toEqual(["next"]);
  });

  it("a last stop with no end lasts until the end of the day", () => {
    expect(states("2026-10-14", at("2026-10-14", "23:59"))).toEqual(["past", "past", "past", "past", "now"]);
  });

});
