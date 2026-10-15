import type { Language } from "@/lib/i18n";
import type { LocationFix } from "@/lib/location";
import type { OpProgress } from "@/lib/live/stream";
import type { Coordinates } from "../map/model";

export interface TripStop {
  id: string;
  date: string;
  time: string;
  endTime?: string;
  title: string;
  booking: "booked" | "unknown";
  notes: string;
  /** WGS84 coordinates supplied by the backend; missing means no map pin. */
  coordinates?: Coordinates | null;
  /** The customer fixed this stop — the server does not move it on its own. */
  pinned?: boolean;
  /** Other options the server kept for this stop (best one is already applied). Shown as information. */
  otherOptions?: TripOption[];
  badges?: { code: string; label: string; source: string }[];
  /** Opens this place in the customer's own map app (Google Maps link from the server; no API key, no billing). */
  mapUrl?: string;
  /** `[2026-10-14]` The place's name from the server (`items[].place`) when it sent one — what Naver Map and KakaoMap search for (`map-apps.tsx`). */
  place?: string;
  /** What the server knows about the place (address, phone, hours, badges). Missing fields are not shown. */
  placeInfo?: PlaceInfo | null;
  /** `[2026-10-06]` The trip is paused for a disaster and this stop falls in the pause (a day's pause pauses only that day's stops). The plan itself is not changed. */
  paused?: boolean;
  /** `[2026-10-13]` The day the stop ends (Seoul) when the server sent an end — a stay ends on its check-out day. */
  endDate?: string;
  /** `[2026-10-08]` The place's ref for its registered photos (「tour:<ID>」) — only when the server sends one (`place_ref`, asked of the backend). */
  placeRef?: string;
  /** `[2026-10-13 사용자 결정 — 목업 A]` What kind of stop the server classes it (`items[].kind`: dining · activity · lodging · flight …) — the icon before its name.
   *  `[2026-10-13 숙소 B안]` A lodging stop is one stay: `date` is the check-in day and `endDate` the check-out day. */
  kind?: string;
  /** The server's name for that kind (`kind_label`: 식사 · 활동 · 숙소 · 항공), said for the icon as it came. */
  kindLabel?: string;
}

/**
 * `[2026-10-06 사용자 결정 — 재난 시 일정 정지]` Whether the trip is paused for a disaster (`safety` of `GET /v1/web/trips/{id}`). A day's pause (`level: "day"`) lifts by itself at midnight; a pause of the
 * whole trip (`"trip"`: war, a volcano erupting …) stays until the customer resumes. The server cannot know the customer is safe, so resuming is always the customer's press (`resume`).
 */
export interface TripSafety {
  paused: boolean;
  level: "day" | "trip" | null;
  /**
   * `[2026-10-06 사용자 결정]` `in_progress`: the trip is under way. `upcoming`: it has not started, and a serious event (war, a volcano, a strong earthquake) stopped it because nobody knows the local
   * situation - it stays paused even when the start date comes, until the customer resumes (`until` is null). A day's pause never stops a trip that has not started.
   */
  phase: "in_progress" | "upcoming" | null;
  /** The server's own words for what happened (「지진 — 오늘 남은 일정 정지」). */
  label: string | null;
  since: string | null;
  until: string | null;
  /** The day a day's pause is for ("2026-10-06"). */
  day: string | null;
  /** An official all-clear came (the pause stays until the customer resumes). */
  released: boolean;
  /** What the resume button says, in the server's words; null when the server gave no resume. */
  resume: { label: string } | null;
}

export interface TripOption { key: string; name: string }

export interface PlaceInfo {
  address?: string;
  phone?: string;
  category?: string;
  /** Opening hours in the server's words, one line per rule ("월 11:00–21:00"). */
  hours: string[];
  /** `[2026-10-13]` The same lines with the day each is for (ISO weekday, 월 = 1 … 일 = 7) — the folded card shows only its visit day's. Rules the server sent no day for are left out. */
  dayHours?: { weekday: number; line: string }[];
  /** Hours the source gave only as text, or conditions the weekly table cannot hold ("공휴일 휴무"). */
  hoursNotes: string[];
  /** Facility badges as the server named them ("card_payment", "parking", …). */
  tags: string[];
  michelin?: { level: string; year?: number } | null;
  /** Credit line the source asks for (e.g. "ⓒ한국관광공사"). */
  sourceNote?: string;
}

/** One saved version of the trip (server `history`): why it changed and what caused it, in the server's words. */
export interface TripChange {
  version: number;
  reason: string;
  /** Seoul wall clock, "2026-09-28 12:23". */
  at: string;
  causes: string[];
}

/** A finding the server made about the plan (server `warnings`), with what to do about it when it says so. */
export interface TripWarning {
  code: string;
  date: string | null;
  reason: string;
  remedy: string | null;
}

export interface TripMessage {
  id: string;
  /** Stable request identity for reconciling the recent record with this tab's replies. */
  caseId?: string;
  role: "assistant" | "user";
  text: string;
  createdAt: string;
  /** Development mode only: which rule sections the answer came from (「t_doc_07#c5」). The server sends it only when an
   *  operator turned on `web.dev_mode`; customers never see it. */
  basis?: string[];
  /** The server did not understand and asks 「다음 중 하나인가요?」: each choice's `message` is a sentence it will understand. */
  choices?: { label: string; message: string; itemId?: string }[];
  /** The server's heading for `choices` — 「혹시 이런 뜻이었나요?」 when it answered and offers other readings. */
  choicesTitle?: string;
  /** The rest of a long answer, folded under 「더 보기」 (the answer itself keeps only the core). */
  more?: string;
  /** This answer changed the plan to `version`: an 「되돌리기」 button can take it back to `version - 1` while it is still the latest. */
  changedTo?: number;
  /** The answer needs where the customer is now (「여기서 어떻게 가?」): the screen offers to ask the browser and send again. */
  needsLocation?: boolean;
  /** The current request is waiting for the customer's answer; no resume token is kept in the conversation. */
  awaitingReply?: boolean;
}

/** An explicit reply to an open request. Kept in memory; never persisted with chat messages. */
export interface TripMessageResume { case_id: string; resume_token?: string; expected_version?: number }

/**
 * `[2026-10-07]` A move between two stops as the server keeps it (an item of kind `mobility`): when to leave and when to arrive, in Seoul time. How it is travelled (mode · distance ·
 * rides) is not in the trip view — it comes with the route lines (`GET …/route-shapes`, keyed by this id). A trip registered from a typed plan may have no moves at all.
 */
export interface TripMove {
  id: string;
  /** The stops it goes between (the stop before and the stop after it in the plan); null at the start or the end of the plan. */
  fromId: string | null;
  toId: string | null;
  date: string;
  /** "HH:MM" */
  departAt: string;
  arriveAt: string | null;
  /** The server's own words (「경복궁 관람 → 점심 식당」). */
  title: string;
}

/** Web view model of one trip, read from `GET /v1/web/trips/{id}` (not a claim that the Case API returns this shape). */
export interface Trip {
  simulation?: { active: boolean; categories: string[]; expires_at: string } | null;
  id: string;
  /** `[2026-10-07]` The trip's name as the server keeps it (「내 여행」); absent on a server that does not send one. */
  title?: string;
  stops: TripStop[];
  /** `[2026-10-07]` The moves between the stops, in plan order (see `TripMove`). */
  moves?: TripMove[];
  messages: TripMessage[];
  /** What the server did to the trip and what it found. */
  history?: TripChange[];
  warnings?: TripWarning[];
  /** The server's per-trip plan page (a link that needs no login). */
  planUrl?: string;
  /** `[2026-10-15 사용자 결정 — ST4F-494]` Whether the 「지금 HH:MM」 label stands beside the 「지금」 dot (`now_label` — an operator switch, on by default). */
  nowLabel?: boolean;
  /** The itinerary version on the server now (an undo is offered only for the change that made it). */
  version?: number;
  /** The day's route to open in the customer's map app, by date. Several links when a day has more stops than one link holds. */
  dayRoutes?: Record<string, string[]>;
  /** Directions between two consecutive stops in the customer's map app, keyed `${fromStopId}>${toStopId}`. */
  legs?: Record<string, string>;
  /** `[2026-10-06]` Paused for a disaster? (absent on a server that does not say). */
  safety?: TripSafety | null;
  /** `[2026-10-06]` The Course Keeper (항로 지킴이) of this trip: on = the server changes a plan that went wrong by itself and says so, off = it asks first. Absent on a server that does not say. */
  guardian?: TripGuardian | null;
}

/** `guardian` of the trip view (`{enabled, since, via}`); `since` · `via` are when and where it was LAST changed (null when it never was). */
export interface TripGuardian { enabled: boolean; since: string | null; via: string | null }

/** One row of "My trips" — only what the server list (`GET /v1/web/trips`) gives: no trip dates, status or open proposals. */
export interface TripSummary {
  id: string;
  title: string;
  /** When the trip was registered (ISO instant). */
  createdAt: string | null;
  /** Itinerary version; above 1 means the itinerary changed after registration. */
  version: number | null;
}

/** Every call names the reader's language; generated text comes back in that language. */
export interface TripGateway {
  /** This customer's trips (the server's list for the stored user key), newest first. */
  listTrips(language: Language): Promise<TripSummary[]>;
  getTrip(tripId: string, language: Language): Promise<Trip>;
  /** `itemId` — the stop the customer picked on screen; the server uses it when the sentence does not name one. */
  /** `location`: where the customer is, from the browser, sent only after the server said the answer needs it. */
  /** `onProgress`: what the server is doing while it works on the message. */
  sendMessage(tripId: string, message: string, language: Language, itemId?: string | null, location?: LocationFix | null,
    onProgress?: (progress: OpProgress) => void, resume?: TripMessageResume, summaryDay?: string): Promise<Trip>;
  /**
   * Delete one trip on the server, or reject with why it was not deleted. A server that has no delete call yet
   * rejects with a `LiveError` whose code is `delete_unsupported` (backend request 2026-10-03).
   */
  deleteTrip(tripId: string, language: Language): Promise<void>;
}
