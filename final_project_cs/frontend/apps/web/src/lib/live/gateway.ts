import type { Trip, TripChange, TripGateway, TripGuardian, TripMessage, TripMessageResume, TripMove, TripSafety, TripStop, TripWarning } from "../../features/trip/model";
import { translator, type Language, type Translate } from "../i18n";
import { api, currentSession, hasSession, LiveError } from "./client";
import { streamApi } from "./stream";

/** Server trip view (`GET /v1/web/trips/{id}`) — only the fields the web reads. */
interface ServerItem {
  badges?: { code: string; label: string; source: string }[];
  item_id: string;
  seq: number;
  kind: string;
  /** The server's name for `kind` (식사 · 활동 · 숙소 · 항공). */
  kind_label?: string | null;
  title: string;
  place: string | null;
  starts_at: string;
  ends_at: string | null;
  changed: boolean;
  /** `[2026-10-13]` A move that leaves from the stay of that morning (「숙소 → 그날 첫 일정」), not from the stop before it. */
  from_stay?: boolean;
  lat?: number | null;
  lon?: number | null;
  booked?: boolean;
  other_options?: { key: string; name: string }[];
  customer_pinned?: boolean;
  place_info?: ServerPlaceInfo | null;
  map_url?: string | null;
  /** `[2026-10-06]` Falls in a disaster pause. */
  paused?: boolean;
  /**
   * `[2026-10-08 웹 제안 — 서버에 아직 없음]` The place's ref for its registered photos (`GET /v1/web/places/photos?ref=`), as the plan check's candidates give it: 「tour:<관광공사 콘텐츠 ID>」.
   * The server keeps that ID on the place (`source_content_id`) but does not send it with a trip yet — asked of the backend (`wiki/external/web-screen-api.md`).
   */
  place_ref?: string | null;
}
/** ★`[2026-09-29]` 서버에 요청한 모양(요식 원장·관광공사에서 읽은 장소 사실). 서버가 아직 안 보내면 상세에 안 나온다. */
interface ServerPlaceInfo {
  address?: string | null; phone?: string | null; category?: string | null;
  hours?: { day?: string; weekday?: number; open?: string; close?: string; closed?: boolean; last_order?: string | null; last_entry?: string | null }[] | null;
  /** Opening-hours text as the source wrote it (places outside the dining ledger). */
  hours_text?: string[] | string | null;
  /** Conditions the server could not turn into a weekly table, in the source's words. */
  hours_conditions?: string[] | null;
  tags?: string[] | null; michelin?: { level?: string; year?: number } | null; source_note?: string | null;
}
interface ServerHistory { version: number; reason: string; at: string; causes?: Record<string, unknown>[] }
interface ServerWarning { code?: string; date?: string | null; reason?: string; remedy?: string | null }
/** `safety` of the trip view (rest-endpoints 「재난 시 일정 정지」): `{paused: false}` or the pause with its resume. */
interface ServerSafety {
  paused?: unknown; level?: unknown; phase?: unknown; label?: unknown; since?: unknown; until?: unknown; day?: unknown; released?: unknown;
  resume?: { label?: unknown; path?: unknown } | null;
}
interface ServerTrip {
  simulation?: { active: boolean; categories: string[]; expires_at: string } | null;
  trip_id: string; title: string; version: number; items: ServerItem[]; plan_url: string;
  /** `[2026-10-15 사용자 결정 — ST4F-494]` The 「지금 HH:MM」 label on the trip screen — the operator's switch; a server without the field shows it (the decided default). */
  now_label?: boolean;
  history?: ServerHistory[]; warnings?: ServerWarning[];
  safety?: ServerSafety | null;
  /** `guardian` of the trip view (rest-endpoints 「항로 지킴이」): `{enabled, since, via}`. */
  guardian?: { enabled?: unknown; since?: unknown; via?: unknown } | null;
  map?: { days?: { date?: string; app_route_urls?: unknown[]; legs?: { from_item_id?: unknown; to_item_id?: unknown; url?: unknown }[] }[] } | null;
}

/** ★Only Google Maps links are opened from the screen — anything else from the server is not shown as a map link. */
function mapLink(value: unknown): string | undefined {
  return typeof value === "string" && value.startsWith("https://www.google.com/maps/") ? value : undefined;
}
/** One row of `GET /v1/web/trips`. */
interface ServerTripRow { trip_id: string; title: string; version: number; created_at: string }

const MESSAGES_PREFIX = "tripilot.web.live.messages:";

/** Seoul wall-clock date and time of an ISO instant ("2026-10-15", "09:00"). */
export function seoul(iso: string): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, time: `${get("hour")}:${get("minute")}` };
}

function stop(item: ServerItem, t: Translate): TripStop {
  const start = seoul(item.starts_at);
  const coordinates = typeof item.lat === "number" && typeof item.lon === "number" ? { lat: item.lat, lng: item.lon } : null;
  const notes = [item.place && item.place !== item.title ? t(`장소: ${item.place}`, `Place: ${item.place}`) : "",
    item.changed ? t("지켜보다 바뀐 일정이에요.", "This stop was changed while we watched your trip.") : ""].filter(Boolean).join(" · ");
  return {
    id: item.item_id, date: start.date, time: start.time, endTime: item.ends_at ? seoul(item.ends_at).time : undefined,
    // `[2026-10-13]` A stay (kind lodging) runs from its check-in day to its check-out day — the screen draws it on every day between.
    ...(item.ends_at ? { endDate: seoul(item.ends_at).date } : {}),      // `kind` 는 아래 한 곳에서 싣는다
    title: item.title, booking: item.booked ? "booked" : "unknown", notes, coordinates,
    pinned: item.customer_pinned === true,
    otherOptions: (item.other_options ?? []).filter((option) => typeof option?.key === "string" && typeof option?.name === "string"),
    badges: (item.badges ?? []).filter((badge) => [badge?.code, badge?.label, badge?.source].every((value) => typeof value === "string" && value !== ""))
      .map(({ code, label, source }) => ({ code, label, source })),
    placeInfo: placeInfo(item.place_info, t),
    mapUrl: mapLink(item.map_url),
    ...(text(item.place) ? { place: text(item.place) } : {}),
    ...(item.paused === true ? { paused: true } : {}),
    ...(text(item.place_ref) ? { placeRef: text(item.place_ref) } : {}),
    ...(text(item.kind) ? { kind: text(item.kind) } : {}),
    ...(text(item.kind_label) ? { kindLabel: text(item.kind_label) } : {}),
  };
}

/** An instant of the pause in Seoul time ("2026-10-06 14:05"); something that is not an instant (a plain date) is shown as it came. */
function whenOf(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  if (!Number.isFinite(Date.parse(value)) || !/T|\d:\d/.test(value)) return value;
  const at = seoul(value);
  return `${at.date} ${at.time}`;
}

/** The server's `guardian` of a trip. No object (an older server) is null - the icon is then not drawn; a field it did not send is null, and `enabled` is only ever the server's `true`. */
export function guardianOf(raw: ServerTrip["guardian"] | undefined): TripGuardian | null {
  if (!raw || typeof raw !== "object" || typeof raw.enabled !== "boolean") return null;
  return { enabled: raw.enabled, since: typeof raw.since === "string" ? raw.since : null, via: typeof raw.via === "string" ? raw.via : null };
}

/** The server's `safety` of a trip, as the screen reads it. Nothing is made up: a field the server did not send is null, and no `safety` at all is null (an older server). */
export function safetyOf(raw: ServerSafety | null | undefined): TripSafety | null {
  if (!raw || typeof raw !== "object") return null;
  const label = typeof raw.resume?.label === "string" && raw.resume.label.trim() ? raw.resume.label.trim() : null;
  return {
    paused: raw.paused === true,
    level: raw.level === "day" || raw.level === "trip" ? raw.level : null,
    phase: raw.phase === "in_progress" || raw.phase === "upcoming" ? raw.phase : null,
    label: typeof raw.label === "string" && raw.label.trim() ? raw.label.trim() : null,
    since: whenOf(raw.since), until: whenOf(raw.until),
    day: typeof raw.day === "string" && raw.day ? raw.day : null,
    released: raw.released === true,
    resume: raw.resume && typeof raw.resume === "object" ? { label: label ?? "" } : null,
  };
}

const text = (value: unknown): string | undefined => (typeof value === "string" && value.trim() ? value.trim() : undefined);

/** The server's place facts, keeping only what it actually sent — an empty field is left out, not shown as "unknown". */
export function placeInfo(raw: ServerPlaceInfo | null | undefined, t: Translate = translator("ko")): TripStop["placeInfo"] {
  if (!raw || typeof raw !== "object") return null;
  const rules = Array.isArray(raw.hours)
    ? raw.hours.map((rule) => ({ weekday: typeof rule?.weekday === "number" && Number.isInteger(rule.weekday) ? rule.weekday : null, line: [text(rule?.day),
      rule?.closed === true ? t("휴무", "Closed") : text(rule?.open) && text(rule?.close) ? `${rule.open}–${rule.close}` : undefined,
      rule?.closed !== true && text(rule?.last_order) ? t(`(주문 마감 ${rule.last_order})`, `(last order ${rule.last_order})`) : undefined,
      rule?.closed !== true && text(rule?.last_entry) ? t(`(입장 마감 ${rule.last_entry})`, `(last entry ${rule.last_entry})`) : undefined,
    ].filter(Boolean).join(" ") })).filter((rule) => rule.line)
    : [];
  const hours = rules.map((rule) => rule.line);
  const dayHours = rules.flatMap((rule) => rule.weekday === null ? [] : [{ weekday: rule.weekday, line: rule.line }]);
  const lines = (value: unknown): string[] =>
    (Array.isArray(value) ? value : [value]).map(text).filter((line): line is string => !!line);
  // ★With a weekly table the source text only adds its conditions; without one, the source text is the hours.
  const hoursNotes = hours.length ? lines(raw.hours_conditions) : [...lines(raw.hours_text), ...lines(raw.hours_conditions)];
  const level = text(raw.michelin?.level);
  const info = {
    address: text(raw.address), phone: text(raw.phone), category: text(raw.category), hours, dayHours, hoursNotes: [...new Set(hoursNotes)],
    tags: Array.isArray(raw.tags) ? raw.tags.filter((tag): tag is string => typeof tag === "string" && !!tag) : [],
    michelin: level ? { level, year: typeof raw.michelin?.year === "number" ? raw.michelin.year : undefined } : null,
    sourceNote: text(raw.source_note),
  };
  const empty = !info.address && !info.phone && !info.category && !hours.length && !info.hoursNotes.length && !info.tags.length && !info.michelin;
  return empty ? null : info;
}

/** A cause in the server's own words: its summary/message when it has one, else what kind of thing it was. */
function causeText(cause: Record<string, unknown>): string {
  for (const field of ["summary", "message"]) if (typeof cause[field] === "string" && cause[field]) return cause[field] as string;
  return ["category", "type", "kind"].map((field) => cause[field]).filter((value): value is string => typeof value === "string").join(" · ");
}

function change(row: ServerHistory): TripChange {
  const at = seoul(row.at);
  return { version: row.version, reason: row.reason, at: `${at.date} ${at.time}`, causes: (row.causes ?? []).map(causeText).filter(Boolean) };
}

/** ★A warning with no reason is dropped, not invented: without the server's sentence there is nothing true to show. */
function warning(row: ServerWarning): TripWarning | null {
  if (typeof row.reason !== "string" || !row.reason) return null;
  return { code: row.code ?? "", date: row.date ?? null, reason: row.reason, remedy: typeof row.remedy === "string" && row.remedy ? row.remedy : null };
}

interface ServerChoice { label?: unknown; message?: unknown; item_id?: unknown }
interface ServerTurn { role?: string; text?: string; case_id?: string | null; at?: string; turn_id?: number;
  reply?: { choices?: ServerChoice[]; choices_title?: unknown; more?: unknown; answer_web?: unknown } }

/**
 * ★`[2026-09-29]` The conversation is the server's record (`GET /v1/web/trips/{id}/chat`) — it follows the customer to
 * another tab or device, including structured choices. This tab's own copy adds development-only basis and pending
 * UI state. If the record cannot be read, this tab's copy is shown.
 */
async function conversation(tripId: string, language: Language): Promise<TripMessage[]> {
  const local = readMessages(tripId);
  let turns: ServerTurn[];
  try { turns = (await api<{ turns?: ServerTurn[] }>(`/v1/web/trips/${encodeURIComponent(tripId)}/chat?limit=40`, language)).turns ?? []; }
  catch { return local; }
  const extras = new Map(local.filter((message) => message.role === "assistant").map((message) => [message.text, message]));
  const extrasByCase = new Map(local.filter((message) => message.role === "assistant" && message.caseId).map((message) => [message.caseId, message]));
  const recorded = turns.filter((turn) => typeof turn.text === "string" && turn.text).map((turn, index) => {
    const role = turn.role === "customer" ? "user" as const : "assistant" as const;
    const reply = role === "assistant" ? turn.reply : undefined;
    const text = typeof reply?.answer_web === "string" ? reply.answer_web : turn.text as string;
    const kept = role === "assistant" ? (turn.case_id ? extrasByCase.get(turn.case_id)
      ?? [extras.get(text), extras.get(turn.text as string)].find((entry) => entry && !entry.caseId)
      : extras.get(text) ?? extras.get(turn.text as string)) : undefined;
    return { id: `chat-${turn.turn_id ?? `${turn.case_id ?? "x"}-${index}`}`, role,
      ...(turn.case_id ? { caseId: turn.case_id } : {}),
      text, createdAt: turn.at ?? "",
      ...(kept?.choices ? { choices: kept.choices } : {}), ...(kept?.basis ? { basis: kept.basis } : {}),
      ...(kept?.changedTo ? { changedTo: kept.changedTo } : {}),
      ...(kept?.choicesTitle ? { choicesTitle: kept.choicesTitle } : {}), ...(kept?.more ? { more: kept.more } : {}),
      ...(kept?.needsLocation ? { needsLocation: true } : {}), ...(kept?.awaitingReply ? { awaitingReply: true } : {}),
      ...(reply?.choices ? { choices: choicesOf(reply.choices) } : {}),
      ...(typeof reply?.choices_title === "string" ? { choicesTitle: reply.choices_title } : {}),
      ...(typeof reply?.more === "string" ? { more: reply.more } : {}) };
  });
  // ★The record can lag the reply this tab just got (the server may finish writing it after answering). What this tab
  //   sent or received after the record's last turn is kept at the end — never dropped, never shown twice.
  //   ☆2026-09-29 real server: right after sending, the latest answer vanished and an older one showed in its place.
  // ★Matched by content, not by clock: the record's time is when the server wrote the answer, which can be a moment
  //   *before* this tab received it — a time rule showed every answer twice (2026-09-29 real server, user report).
  //   A question and its answer from this tab are added only when the answer is not in the record yet.
  const answered = new Set(recorded.filter((message) => message.role === "assistant").map((message) => message.text));
  const cases = new Set(recorded.filter((message) => message.role === "assistant").map((message) => message.caseId).filter(Boolean));
  const cutoff = Date.parse(recorded[0]?.createdAt ?? "");
  const eligible = (message: TripMessage) => message.caseId ? !cases.has(message.caseId)
    && (!Number.isFinite(cutoff) || Date.parse(message.createdAt) >= cutoff)
    : (!Number.isFinite(cutoff) || Date.parse(message.createdAt) >= cutoff);
  const tail: TripMessage[] = [];
  for (let index = 0; index < local.length; index += 1) {
    const message = local[index];
    const reply = message.role === "user" ? local[index + 1] : undefined;
    if (message.role === "user" && reply?.role === "assistant") {
      if (eligible(reply) && (reply.caseId || !answered.has(reply.text))) {
        if (!message.caseId || !recorded.some((entry) => entry.caseId === message.caseId && entry.role === "user")) tail.push(message);
        tail.push(reply);
      }
      index += 1;
    } else if (message.role === "assistant" && eligible(message) && !answered.has(message.text)) tail.push(message);
  }
  return [...recorded, ...tail];
}

function readMessages(tripId: string): TripMessage[] {
  try { return JSON.parse(window.sessionStorage.getItem(MESSAGES_PREFIX + tripId) ?? "[]") as TripMessage[]; }
  catch { return []; }
}

/** This tab's copy of a deleted trip's conversation goes with it. */
function forgetMessages(tripId: string) {
  try { window.sessionStorage.removeItem(MESSAGES_PREFIX + tripId); }
  catch { /* nothing kept */ }
}

function writeMessages(tripId: string, messages: TripMessage[]) {
  try { window.sessionStorage.setItem(MESSAGES_PREFIX + tripId, JSON.stringify(messages.slice(-60))); }
  catch { /* the conversation is shown for this page only */ }
}

/**
 * What the server did with a message, in fixed words. The server returns a status, not a sentence;
 * we do not invent an answer — an unknown status is shown by its own name.
 * ★`[2026-09-28]` The server now puts an `answer` on every result (chat always answers), so the screen must not
 *   fill in its own “we passed it to a person” line for `escalated`: that branch was removed on purpose.
 */
/** Rule sections behind an answer (`basis_sources`) — present only in development mode (`web.dev_mode`); a string or `{source}` each. */
function basisOf(raw: unknown[] | null | undefined): string[] {
  return (raw ?? []).map((entry) => (typeof entry === "string" ? entry : typeof (entry as { source?: unknown })?.source === "string" ? (entry as { source: string }).source : ""))
    .filter((source) => source.trim() !== "");
}

/** 「다음 중 하나인가요?」 — only choices with a sentence to send are kept. */
function choicesOf(raw: ServerChoice[] | null | undefined): NonNullable<TripMessage["choices"]> {
  return (raw ?? []).flatMap((choice) => typeof choice?.message === "string" && choice.message.trim()
    ? [{ label: typeof choice.label === "string" && choice.label.trim() ? choice.label : choice.message, message: choice.message,
      ...(typeof choice.item_id === "string" && choice.item_id.trim() ? { itemId: choice.item_id } : {}) }] : []);
}

function replyFor(result: { status?: string; case_status?: string; answer?: string }, t: Translate): string {
  if (result.answer) return result.answer;           // the server's own customer sentence (`itinerary_team.ANSWERS`)
  switch (result.status) {
    case "adjusted": return t("일정을 바꿨어요. 바뀐 일정을 확인해 주세요.", "Your itinerary was changed. Please check the updated stops.");
    case "asked": return t("바꾸기 전에 확인이 필요해요. 여행계획서에서 안을 골라 주세요.", "We need your choice before changing anything. Pick an option on your plan page.");
    case "rolled_back": return t("이전 일정으로 되돌렸어요.", "Your itinerary was rolled back.");
    case "kept": return t("일정은 그대로 두었어요.", "Your itinerary was kept as it is.");
    case "no_alternate": return t("바꿀 만한 다른 안을 찾지 못해 일정은 그대로예요.", "No suitable alternative was found, so nothing changed.");
    case "duplicate": return t("같은 요청을 이미 받았어요.", "We already received this request.");
    default: return t(`요청을 받았어요 (상태: ${result.status ?? result.case_status ?? "?"}).`, `Request received (status: ${result.status ?? result.case_status ?? "?"}).`);
  }
}

async function read(tripId: string, language: Language): Promise<Trip> {
  const t = translator(language);
  // ★`[2026-10-04]` A trip has an owner: a browser with no session (a link someone kept, or a guest session that ended) is not made a new guest just to be
  //   told "not yours". It is told so at once, with the way back — a login brings an account's trips back; a guest's trips are gone.
  if (!await hasSession(language)) {
    throw new LiveError("not_found", t("이 여행을 찾지 못했어요. 창을 닫았거나 시간이 지나 게스트 여행이 사라졌을 수 있어요. 계정에 보관한 여행이라면 메뉴의 「로그인 · 가입」으로 로그인하면 다시 열려요.", "We could not find this trip. A guest trip cannot be continued after the window is closed, and is deleted after a while. If it is kept with an account, sign in with “Sign in or sign up” in the menu to open it again."));
  }
  const server = await api<ServerTrip>(`/v1/web/trips/${encodeURIComponent(tripId)}`, language);
  // ★이동 항목(kind mobility — 출발 시각 = 다음 일정 시작 − 이동 − 여유)은 일정 목록에 섞지 않는다(☆2026-09-28 「A → B」가 일정처럼 끼어 보였다).
  //   `[2026-10-07]` 여행 화면이 일정 사이의 이동 줄로 그리므로 앞뒤 일정과 함께 `moves` 로 따로 둔다(전에는 다음 일정 메모의 「몇 시 출발」이었다). 출발 알림은 서버가 그 시각에 보낸다(D-020).
  const stops: TripStop[] = [];
  const moves: TripMove[] = [];
  for (const item of server.items) {
    if (item.kind !== "mobility") { stops.push(stop(item, t)); continue; }
    const depart = seoul(item.starts_at);
    moves.push({ id: item.item_id, fromId: stops.at(-1)?.id ?? null, toId: null, date: depart.date, departAt: depart.time,
      arriveAt: item.ends_at ? seoul(item.ends_at).time : null, title: item.title });
  }
  // The stop after each move is the next stop in the plan.
  for (const move of moves) {
    const at = server.items.findIndex((item) => item.item_id === move.id);
    move.toId = server.items.slice(at + 1).find((item) => item.kind !== "mobility")?.item_id ?? null;
    // `[2026-10-13]` A morning move 「숙소 → 그날 첫 일정」(`from_stay`) leaves from the stay the customer sleeps in that night before — not from the stop before it in the plan (the day before's last).
    if (server.items[at]?.from_stay) move.fromId = stops.find((stop) => stop.kind === "lodging" && !!stop.endDate && stop.date < move.date && move.date <= stop.endDate)?.id ?? null;
  }
  // ★The server judged the plan when it was registered (`_create_trip`) — what it found is `warnings`, not a separate check to run.
  return {
    id: server.trip_id, title: text(server.title), stops, moves,
    messages: await conversation(tripId, language),
    planUrl: server.plan_url || undefined,
    nowLabel: server.now_label !== false,
    version: typeof server.version === "number" ? server.version : undefined,
    safety: safetyOf(server.safety),
    simulation: server.simulation ?? null,
    guardian: guardianOf(server.guardian),
    history: (server.history ?? []).map(change),
    warnings: (server.warnings ?? []).map(warning).filter((item): item is TripWarning => item !== null),
    dayRoutes: Object.fromEntries((server.map?.days ?? []).flatMap((day) => {
      const urls = (day.app_route_urls ?? []).map(mapLink).filter((url): url is string => !!url);
      return typeof day.date === "string" && urls.length ? [[day.date, urls]] : [];
    })),
    legs: Object.fromEntries((server.map?.days ?? []).flatMap((day) => (day.legs ?? []).flatMap((leg) => {
      const url = mapLink(leg?.url);
      return url && typeof leg.from_item_id === "string" && typeof leg.to_item_id === "string" ? [[`${leg.from_item_id}>${leg.to_item_id}`, url]] : [];
    }))),
  };
}

/**
 * ★`[2026-10-08 정정]` 여행 삭제 — **서버에 있다**: `POST /v1/web/trips/{trip_id}/delete`(`app/domains/travel_ops/entry/trip_api.py`,
 *   `role-manager` 스냅숏 `64fd0dc` 2026-10-04). 웹이 `[2026-10-03]` 에 요청서(`wiki/records/plans/2026-10-03_1920_웹_실서버_전환_백엔드_요청.md`)의
 *   모양 그대로 연결해 둔 것이라 그대로 동작한다(본인 여행만, 이미 없으면 404 `not_found`).
 *   그 경로가 없는 옛 서버는 FastAPI 본문 `{"detail":"Not Found"}` 의 404(`HTTP_404`)를 주므로 — 여행이 없다는 서버 문장(`not_found`)과 갈라 —
 *   「지원하지 않음」으로 말하는 갈래(`delete_unsupported`)를 남겨 둔다. `POST` 로 한 것은 서버 CORS 가 GET·POST·PUT 만 열어서다.
 */
async function removeTrip(tripId: string, language: Language): Promise<void> {
  const t = translator(language);
  try { await api<unknown>(`/v1/web/trips/${encodeURIComponent(tripId)}/delete`, language, { method: "POST" }); }
  catch (error) {
    // A 2xx answer with no JSON body is still a success (the server deleted it): only `api`'s body read fails.
    if (error instanceof SyntaxError) { forgetMessages(tripId); return; }
    if (error instanceof LiveError && (error.code === "HTTP_404" || error.code === "HTTP_405")) {
      throw new LiveError("delete_unsupported", t("서버가 아직 여행 삭제를 지원하지 않아요. 지원되면 바로 쓸 수 있어요.", "The server does not support deleting trips yet. It will work as soon as the server does."));
    }
    if (error instanceof LiveError && error.code === "not_found") { forgetMessages(tripId); return; }   // already gone
    throw error;
  }
  forgetMessages(tripId);
}

/** Live adapter over `/v1/web/*`. Registration goes through the plan-check screen (`lib/live/intake*.ts`), not through here. */
export function createLiveGateway(): TripGateway {
  // Resume credentials belong to one trip and login session. Reloads deliberately leave correlation to the API.
  const pending = new Map<string, { resume: TripMessageResume; session: string | null }>();
  // Failed sends retain the same request id and resume credentials, including on a user-triggered retry.
  const sending = new Map<string, { message: string; itemId: string | null; requestId: string; resume?: TripMessageResume; summaryDay?: string }>();
  return {
    async listTrips(language) {
      // ★No session means this browser has registered nothing. Asking with the list would make a guest session — a new server user —
      //   only to list nothing, so we only ask who this browser is (`hasSession`) and stop there when nobody.
      if (!await hasSession(language)) return [];
      const { trips } = await api<{ trips: ServerTripRow[] }>("/v1/web/trips", language);
      return trips.map((row) => ({ id: row.trip_id, title: row.title, createdAt: row.created_at, version: row.version }));
    },
    getTrip: read,
    async deleteTrip(tripId, language) {
      await removeTrip(tripId, language);
      pending.delete(tripId);
      sending.delete(tripId);
    },
    async sendMessage(tripId, message, language, itemId, location, onProgress, explicitResume, summaryDay) {
      const t = translator(language);
      const now = new Date().toISOString();
      const waiting = pending.get(tripId);
      if (waiting && waiting.session !== (currentSession()?.csrf ?? null)) { pending.delete(tripId); sending.delete(tripId); }
      const earlier = sending.get(tripId);
      const retry = earlier && earlier.message === message && earlier.itemId === (itemId ?? null) && earlier.summaryDay === summaryDay
        && (!explicitResume || (earlier.resume?.case_id === explicitResume.case_id
          && earlier.resume.resume_token === explicitResume.resume_token && earlier.resume.expected_version === explicitResume.expected_version));
      const requestId = retry
        ? earlier.requestId : `web-${now}-${Math.random().toString(36).slice(2, 8)}`;
      const resume = summaryDay ? undefined : retry ? earlier.resume : explicitResume ?? pending.get(tripId)?.resume;
      sending.set(tripId, { message, itemId: itemId ?? null, requestId, resume, summaryDay });
      // ★`[2026-10-02]` Asked as a stream, the server says what it is doing while the model works (`stream.ts`); a lost line
      //   sends the same `request_id` again, which the server answers without acting twice.
      const result = await streamApi<{ status?: string; case_status?: string; case_id?: string; version?: number; resume_token?: string | null;
        required_input_schema?: Record<string, unknown> | null; answer?: string; basis_sources?: unknown[] | null; choices?: ServerChoice[] | null;
        outcome?: { status?: string; version?: unknown } | null; more?: unknown; choices_title?: unknown; needs_location?: unknown }>(`/v1/web/trips/${encodeURIComponent(tripId)}/messages`, language, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: requestId, message, ...(summaryDay ? { summary_day: summaryDay } : {}), ...(resume ?? {}), ...(itemId ? { item_id: itemId } : {}),
          // ★Only when the customer pressed 「내 위치 알려 주고 다시 묻기」 — never attached on its own.
          ...(location ? { location: { lat: location.lat, lng: location.lng, accuracy_m: location.accuracyM, at: location.at } } : {}) }),
      }, onProgress);
      sending.delete(tripId);
      const awaitingReply = result.case_status === "waiting_input";
      if (awaitingReply && typeof result.case_id === "string" && result.case_id) {
        pending.set(tripId, { session: currentSession()?.csrf ?? null, resume: { case_id: result.case_id,
          ...(typeof result.resume_token === "string" && result.resume_token ? { resume_token: result.resume_token } : {}),
          ...(typeof result.version === "number" && Number.isInteger(result.version) ? { expected_version: result.version } : {}) } });
      } else if (!summaryDay) pending.delete(tripId);
      const sent: TripMessage[] = [
        { id: `${requestId}-q`, caseId: result.case_id, role: "user" as const, text: message, createdAt: now },
        { id: `${requestId}-a`, caseId: result.case_id, role: "assistant" as const, text: replyFor(result, t), createdAt: new Date().toISOString(),
          ...(basisOf(result.basis_sources).length ? { basis: basisOf(result.basis_sources) } : {}),
          ...(choicesOf(result.choices).length ? { choices: choicesOf(result.choices) } : {}),
          ...(typeof result.choices_title === "string" && result.choices_title.trim() ? { choicesTitle: result.choices_title.trim() } : {}),
          ...(typeof result.more === "string" && result.more.trim() ? { more: result.more.trim() } : {}),
          ...(result.needs_location === true && !location ? { needsLocation: true } : {}),
          ...(awaitingReply ? { awaitingReply: true } : {}),
          // ★A change the customer asked for in chat can be undone from the answer (user decision 2026-09-29).
          ...(result.status === "adjusted" && typeof result.outcome?.version === "number" && result.outcome.version > 1
            ? { changedTo: result.outcome.version } : {}) }];
      writeMessages(tripId, [...readMessages(tripId).map((previous) => ({ ...previous, awaitingReply: summaryDay ? previous.awaitingReply : false })), ...sent]);
      // ★Sending and re-reading are two steps (2026-09-30, teammate question Q-05). The server has answered; a failed
      //   re-read must not turn that into "not sent" — the customer would send again and the change would run twice.
      //   The caller shows the reply it got (`detail.sent`) and re-reads later.
      try { return await read(tripId, language); }
      catch { throw new LiveError("reply_kept", t("답은 받았어요. 최신 일정을 다시 불러오지 못해 잠시 뒤 다시 읽고 있어요.", "The reply arrived. The latest plan did not load, so it is being read again shortly."), { sent }); }
    },
  };
}
