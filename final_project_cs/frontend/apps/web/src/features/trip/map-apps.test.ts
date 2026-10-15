import { describe, expect, it } from "vitest";
import type { TripStop } from "./model";
import { placeLinks, routeLinks } from "./map-apps";
import type { TripLeg } from "./trip-timeline";

const stop = (id: string, extra: Partial<TripStop> = {}): TripStop => ({ id, date: "2026-10-14", time: "10:00", title: id, booking: "unknown", notes: "", ...extra });
const palace = stop("경복궁 관람", { place: "경복궁", coordinates: { lat: 37.5796, lng: 126.977 }, mapUrl: "https://www.google.com/maps/search/?api=1&query=%EA%B2%BD%EB%B3%B5%EA%B6%81" });
const street = stop("인사동, 쌈지길", { coordinates: { lat: 37.5718, lng: 126.985 }, mapUrl: "https://www.google.com/maps/search/?api=1&query=x" });
const leg = (from: TripStop, to: TripStop, directions: string | null): TripLeg => ({ id: `${from.id}>${to.id}`, from, to, move: null, shape: null, minutes: null, slack: null, directions });

describe("map apps (ST4F-492)", () => {
  it("searches a place by the server's place name in Naver Map and KakaoMap, and keeps the server's Google link", () => {
    expect(placeLinks(palace)).toEqual([
      { app: "naver", url: "https://map.naver.com/p/search/%EA%B2%BD%EB%B3%B5%EA%B6%81" },
      { app: "kakao", url: "https://map.kakao.com/link/search/%EA%B2%BD%EB%B3%B5%EA%B6%81" },
      { app: "google", url: palace.mapUrl },
    ]);
  });

  it("searches the stop's own name when the server sent no place name", () => {
    expect(placeLinks(street)?.[1].url).toBe(`https://map.kakao.com/link/search/${encodeURIComponent("인사동, 쌈지길")}`);
  });

  it("has no links without the server's map link (no coordinates)", () => {
    expect(placeLinks(stop("메모만"))).toBeNull();
  });

  it("builds the comparison sheet's transit addresses — Naver lng,lat · Kakao lat,lng — with / and , out of the names", () => {
    const google = "https://www.google.com/maps/dir/?api=1&origin=a&destination=b&travelmode=transit";
    expect(routeLinks(leg(palace, street, google))).toEqual([
      { app: "naver", url: `https://map.naver.com/p/directions/126.977,37.5796,${encodeURIComponent("경복궁")},,/126.985,37.5718,${encodeURIComponent("인사동  쌈지길")},,/-/transit` },
      { app: "kakao", url: `https://map.kakao.com/link/by/traffic/${encodeURIComponent("경복궁")},37.5796,126.977/${encodeURIComponent("인사동  쌈지길")},37.5718,126.985` },
      { app: "google", url: google },
    ]);
  });

  it("has no route links without the server's directions or a stop's coordinates", () => {
    expect(routeLinks(leg(palace, street, null))).toBeNull();
    expect(routeLinks(leg(palace, stop("좌표 없음"), "https://www.google.com/maps/dir/?api=1"))).toBeNull();
  });
});
