import { describe, expect, it } from "vitest";
import type { RouteShape } from "@/lib/live/route-shapes";
import { creditFacts, creditNumbers, datesText, distanceText, drawEndPicture, tripEnded } from "./ending-credits";
import type { Trip, TripStop } from "./model";

const t = (ko: string) => ko;
const stop = (id: string, date: string, time: string, patch: Partial<TripStop> = {}): TripStop => ({ id, date, time, title: id, booking: "unknown", notes: "", ...patch });
const shape = (itemId: string, distanceM: number | null): RouteShape => ({ itemId, fromItemId: "a", toItemId: "b", from: null, to: null, mode: "walk", source: "local_road_graph", grade: "추정",
  distanceM, note: null, points: [], rides: [], walks: null });
const trip = (patch: Partial<Trip> = {}): Trip => ({ id: "t1", messages: [], stops: [
  stop("경복궁", "2026-10-15", "10:00", { endTime: "12:00", placeInfo: { hours: [], hoursNotes: [], tags: [], sourceNote: "ⓒ한국관광공사" } }),
  stop("광장시장", "2026-10-15", "12:30", { endTime: "13:30", placeInfo: { hours: [], hoursNotes: [], tags: [], sourceNote: "ⓒ한국관광공사" } }),
  stop("남산", "2026-10-16", "10:00"),
], ...patch });

describe("`[2026-10-15 ST4F-496]` when a trip is over", () => {
  it("is over once its last stop has ended in Seoul — its end time, or its start when it has none", () => {
    expect(tripEnded(trip(), { date: "2026-10-16", minute: 9 * 60 + 59 })).toBe(false);
    expect(tripEnded(trip(), { date: "2026-10-16", minute: 10 * 60 })).toBe(true);
    expect(tripEnded(trip(), { date: "2026-10-17", minute: 0 })).toBe(true);
    const withEnd = trip({ stops: [stop("남산", "2026-10-16", "10:00", { endTime: "12:00" })] });
    expect(tripEnded(withEnd, { date: "2026-10-16", minute: 11 * 60 })).toBe(false);
  });

  it("counts a stay as over only on its check-out day, and never a trip with no stops", () => {
    const stay = trip({ stops: [stop("호텔", "2026-10-15", "15:00", { endDate: "2026-10-17", endTime: "11:00", kind: "lodging" })] });
    expect(tripEnded(stay, { date: "2026-10-16", minute: 12 * 60 })).toBe(false);
    expect(tripEnded(stay, { date: "2026-10-17", minute: 11 * 60 })).toBe(true);
    expect(tripEnded(trip({ stops: [] }), { date: "2030-01-01", minute: 0 })).toBe(false);
  });
});

describe("what the credits say — only what the server sent or the screen sees", () => {
  it("writes the days once, with the year again only when it changes", () => {
    expect(datesText("2026-10-15", "2026-10-16")).toBe("2026. 10. 15 — 10. 16");
    expect(datesText("2026-10-15", "2026-10-15")).toBe("2026. 10. 15");
    expect(datesText("2026-12-31", "2027-01-01")).toBe("2026. 12. 31 — 2027. 01. 01");
  });

  it("lists each day's stops, the changes after the registration, the moves, the drawn length and the sources", () => {
    const facts = creditFacts(trip({
      moves: [{ id: "m1", fromId: "경복궁", toId: "광장시장", date: "2026-10-15", departAt: "12:00", arriveAt: "12:25", title: "경복궁 → 광장시장" }],
      history: [{ version: 1, reason: "등록", at: "2026-10-14 21:00", causes: [] }, { version: 2, reason: "자동 변경", at: "2026-10-15 09:10", causes: ["비 예보"] }],
    }), "서울 이틀", [shape("m1", 1830), shape("m2", null), shape("m3", 470)], "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)", (date, index) => `${index + 1}일차 ${date}`);
    expect(facts.title).toBe("서울 이틀");
    expect(facts.dates).toBe("2026. 10. 15 — 10. 16");
    expect(facts.days).toEqual([{ label: "1일차 2026-10-15", titles: ["경복궁", "광장시장"] }, { label: "2일차 2026-10-16", titles: ["남산"] }]);
    expect(facts.changes.map((change) => change.version)).toEqual([2]);                      // version 1 is the registration, not a change
    expect([facts.stops, facts.moves, facts.distanceM]).toEqual([3, 1, 2300]);
    expect(facts.sources).toEqual(["ⓒ한국관광공사", "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)"]);
  });

  it("leaves out what the server does not keep: no moves, no lines, no sources", () => {
    const facts = creditFacts(trip({ stops: [stop("남산", "2026-10-16", "10:00")] }), "t", [], "경로선 출처", () => "");
    expect([facts.moves, facts.distanceM, facts.sources, facts.changes]).toEqual([null, null, [], []]);
    expect(creditNumbers(facts, t)).toEqual([["일정", "1곳"], ["바꾼 일정", "0번"]]);
  });

  it("says the numbers in a fixed order, the length rounded", () => {
    const facts = creditFacts(trip({ moves: [] }), "t", [shape("m1", 2349)], null, () => "");
    expect(creditNumbers(facts, t)).toEqual([["일정", "3곳"], ["이동", "0번"], ["이동 거리", "약 2.3km"], ["바꾼 일정", "0번"]]);
    expect(distanceText(480.4, t)).toBe("약 480m");
  });

  it("makes no picture where it cannot draw (no page)", async () => {
    expect(await drawEndPicture({ heading: "t", dates: "", kicker: "COOKIE", line: "l", cells: [], brand: "triPilot" }, { background: "", ink: "", accent: "", font: "" })).toBeNull();
  });
});
