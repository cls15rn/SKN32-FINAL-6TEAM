import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CSRF, answeringSession, sessionCalls } from "./session-kit";
import { LiveError, resetSessionState } from "./client";
import { createLiveGateway } from "./gateway";

function memory() {
  const items = new Map<string, string>();
  return { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => { items.set(key, value); }, removeItem: (key: string) => { items.delete(key); } };
}

const TRIP = {
  trip_id: "c287a5e9-7707-408c-9ea9-456cde20cbec", title: "서울 가족여행", version: 1, plan_url: "http://x/plan",
  items: [
    { item_id: "a", seq: 1, kind: "activity", title: "경복궁 관람", place: "경복궁", starts_at: "2026-10-15T00:00:00+00:00", ends_at: "2026-10-15T01:30:00+00:00", changed: false, lat: 37.5796, lon: 126.977, booked: false },
    { item_id: "b", seq: 2, kind: "activity", title: "명동난타극장", place: "명동난타극장", starts_at: "2026-10-16T10:00:00+00:00", ends_at: null, changed: true, lat: null, lon: null, booked: true },
  ],
};

describe("live trip gateway", () => {
  let calls: { url: string; init: RequestInit }[];
  let replies: unknown[];
  /** The server's conversation record (`GET …/chat`); `null` = that call fails, so the tab's own copy is shown. */
  let chat: unknown[] | null;

  beforeEach(() => {
    calls = [];
    replies = [];
    chat = null;
    vi.stubGlobal("window", { localStorage: memory(), sessionStorage: memory() });
    useBrowser(true);                                                 // the usual visitor: a returning one (a first visit is `useBrowser(false)`)
  });
  afterEach(() => { vi.unstubAllGlobals(); resetSessionState(); });

  /** `has`: this browser already has a session cookie; false = a first visit (asking who this is finds no one). */
  function useBrowser(has: boolean) {
    vi.stubGlobal("fetch", answeringSession(async (url, init) => {
      calls.push({ url, init });
      if (url.includes("/chat?")) return chat === null ? new Response("{}", { status: 404 }) : new Response(JSON.stringify({ turns: chat }), { status: 200 });
      const next = replies.shift();
      return next instanceof Response ? next : new Response(JSON.stringify(next ?? TRIP), { status: 200 });
    }, { has }));
  }

  it("maps server items to stops in Seoul time with pins and booking marks", async () => {
    useBrowser(true);
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.id).toBe(TRIP.trip_id);
    expect(trip.stops.map((stop) => [stop.date, stop.time, stop.endTime, stop.booking])).toEqual([
      ["2026-10-15", "09:00", "10:30", "unknown"],
      ["2026-10-16", "19:00", undefined, "booked"],
    ]);
    expect(trip.stops[0].coordinates).toEqual({ lat: 37.5796, lng: 126.977 });
    expect(trip.stops[1].coordinates).toBeNull();                 // ★no coordinates → no pin, never guessed
    expect(trip.stops[1].notes).toContain("바뀐");
    // ★the cookie goes with every call — never a key kept in the browser, never a server scope key
    expect(sessionCalls.map((call) => new URL(call.url).pathname)).toEqual(["/v1/web/auth/me"]);
    expect(calls[0].init.credentials).toBe("include");
    expect((calls[0].init.headers as Record<string, string>)["X-User-Key"]).toBeUndefined();
  });

  it("keeps a structured choice target and restores it from the API record in a fresh gateway", async () => {
    const gateway = createLiveGateway();
    replies.push({ status: "answered", answer: "경복궁 주소", choices: [{ label: "자세히 알려줘", message: "자세히 알려줘", item_id: "a" }] });
    const first = await gateway.sendMessage(TRIP.trip_id, "경복궁 어디야", "ko");
    expect(first.messages.at(-1)?.choices?.[0]).toEqual({ label: "자세히 알려줘", message: "자세히 알려줘", itemId: "a" });
    window.sessionStorage.clear?.();
    // A different tab has no local extras: the server record must carry the choice itself.
    window.sessionStorage.removeItem(`tripilot.web.live.messages:${TRIP.trip_id}`);
    chat = [{ role: "customer", text: "경복궁 어디야", case_id: "case-a" },
      { role: "assistant", text: "경복궁 주소 · 혹시 이런 뜻이었나요?", case_id: "case-a",
        reply: { answer_web: "경복궁 주소", choices_title: "혹시 이런 뜻이었나요?", more: "예약 기록",
          choices: [{ label: "자세히 알려줘", message: "자세히 알려줘", item_id: "a" }] } }];
    const restored = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(restored.messages).toHaveLength(2);
    expect(restored.messages.at(-1)).toMatchObject({ text: "경복궁 주소", more: "예약 기록",
      choices: [{ label: "자세히 알려줘", message: "자세히 알려줘", itemId: "a" }] });
    replies.push({ answer: "상세" });
    await gateway.sendMessage(TRIP.trip_id, "자세히 알려줘", "ko", restored.messages.at(-1)?.choices?.[0].itemId);
    expect(JSON.parse(calls.filter(c => c.init.method === "POST").at(-1)!.init.body as string)).toMatchObject({ item_id: "a" });
  });

  it("does not make a guest just to open a trip: a browser with no session is told at once, with the way back", async () => {
    useBrowser(false);
    const failure = await createLiveGateway().getTrip(TRIP.trip_id, "ko").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(LiveError);
    expect((failure as LiveError).code).toBe("not_found");
    expect((failure as LiveError).message).toContain("로그인");
    expect(calls).toHaveLength(0);
    expect(sessionCalls.some((call) => new URL(call.url).pathname === "/v1/web/auth/session")).toBe(false);
  });

  it("lists nothing without asking for trips when this browser has no session", async () => {
    useBrowser(false);
    expect(await createLiveGateway().listTrips("ko")).toEqual([]);
    expect(calls).toHaveLength(0);                                 // ★no session made — viewing the list creates no user
    expect(sessionCalls.map((call) => new URL(call.url).pathname)).toEqual(["/v1/web/auth/me"]);   // it only asked who this is
  });

  it("lists the server's trips with the session cookie", async () => {
    useBrowser(true);
    const gateway = createLiveGateway();
    await gateway.getTrip(TRIP.trip_id, "ko");
    replies.push({ trips: [{ trip_id: TRIP.trip_id, title: "서울 가족여행", version: 3, created_at: "2026-09-28T03:12:00+00:00" }] });
    expect(await gateway.listTrips("ko")).toEqual([{ id: TRIP.trip_id, title: "서울 가족여행", createdAt: "2026-09-28T03:12:00+00:00", version: 3 }]);
    expect(calls.at(-1)!.url).toMatch(/\/v1\/web\/trips$/);
    expect(calls.at(-1)!.init.credentials).toBe("include");
  });

  describe("deleting a trip (backend request 2026-10-03: POST /v1/web/trips/{id}/delete)", () => {
    const remove = (gateway = createLiveGateway()) => gateway.deleteTrip(TRIP.trip_id, "ko");
    const deleteCalls = () => calls.filter((call) => call.url.endsWith("/delete"));

    it("asks the server with the session (cookie and CSRF token) and forgets this tab's copy of the conversation", async () => {
      useBrowser(true);
      const gateway = createLiveGateway();
      await gateway.getTrip(TRIP.trip_id, "ko");
      (globalThis as unknown as { window: { sessionStorage: ReturnType<typeof memory> } }).window.sessionStorage.setItem("tripilot.web.live.messages:" + TRIP.trip_id, "[{}]");
      replies.push({ trip_id: TRIP.trip_id, status: "deleted" });
      await remove(gateway);
      expect(deleteCalls()).toHaveLength(1);
      expect(deleteCalls()[0].url).toContain(`/v1/web/trips/${TRIP.trip_id}/delete`);
      expect(deleteCalls()[0].init.method).toBe("POST");
      expect((deleteCalls()[0].init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(CSRF);
      expect(deleteCalls()[0].init.credentials).toBe("include");
      expect((globalThis as unknown as { window: { sessionStorage: ReturnType<typeof memory> } }).window.sessionStorage.getItem("tripilot.web.live.messages:" + TRIP.trip_id)).toBeNull();
    });

    it("takes a successful answer without a JSON body as deleted", async () => {
      replies.push(new Response(null, { status: 204 }));
      await expect(remove()).resolves.toBeUndefined();
    });

    it("says the server cannot delete yet when the route is not there — the real server's answer today is 404 {detail: Not Found} — and nothing is pretended", async () => {
      replies.push(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
      await expect(remove()).rejects.toMatchObject({ name: "LiveError", code: "delete_unsupported" });
      replies.push(new Response(JSON.stringify({ detail: "Method Not Allowed" }), { status: 405 }));
      await expect(remove()).rejects.toMatchObject({ code: "delete_unsupported" });
    });

    it("takes the server's own “no such trip of yours” (404 not_found) as already gone, but passes any other refusal on as it was", async () => {
      replies.push(new Response(JSON.stringify({ error: { code: "not_found", message: "resource not found" } }), { status: 404 }));
      await expect(remove()).resolves.toBeUndefined();
      replies.push(new Response(JSON.stringify({ error: { code: "internal_error", message: "서버 오류" } }), { status: 500 }));
      await expect(remove()).rejects.toMatchObject({ code: "internal_error", message: "서버 오류" });
    });
  });

  it("keeps move items out of the stops, as moves between the stop before and the stop after them (Seoul time)", async () => {
    replies.push({ ...TRIP, items: [TRIP.items[0],
      { item_id: "m", seq: 2, kind: "mobility", title: "경복궁 → 명동난타극장", place: null, starts_at: "2026-10-16T09:25:00+00:00", ends_at: "2026-10-16T09:50:00+00:00", changed: false, lat: null, lon: null, booked: false },
      TRIP.items[1]] });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops.map((stop) => stop.title)).toEqual(["경복궁 관람", "명동난타극장"]);
    expect(trip.stops[1].notes).not.toContain("출발");
    expect(trip.moves).toEqual([{ id: "m", fromId: TRIP.items[0].item_id, toId: TRIP.items[1].item_id, date: "2026-10-16", departAt: "18:25", arriveAt: "18:50", title: "경복궁 → 명동난타극장" }]);
  });

  it("reads a stop's photo ref when the server sends one (place_ref), and has none when it is empty or missing", async () => {
    replies.push({ ...TRIP, items: [{ ...TRIP.items[0], place_ref: " tour:126508 " }, { ...TRIP.items[1], place_ref: "  " }] });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].placeRef).toBe("tour:126508");
    expect(trip.stops[1]).not.toHaveProperty("placeRef");
  });

  it("reads the trip's name the server keeps", async () => {
    replies.push({ ...TRIP, title: " 내 여행 " });
    expect((await createLiveGateway().getTrip(TRIP.trip_id, "ko")).title).toBe("내 여행");
  });

  it("shows the server's own answer and falls back to fixed words, never inventing one", async () => {
    replies.push({ status: "no_meal", answer: "그 시각 뒤에는 식사 일정이 없어요." }, TRIP,
      { status: "mystery_status" }, TRIP);
    const gateway = createLiveGateway();
    const first = await gateway.sendMessage(TRIP.trip_id, "식당이 휴무예요", "ko");
    expect(first.messages.map((message) => [message.role, message.text])).toEqual([
      ["user", "식당이 휴무예요"], ["assistant", "그 시각 뒤에는 식사 일정이 없어요."]]);
    const second = await gateway.sendMessage(TRIP.trip_id, "?", "ko");
    expect(second.messages.at(-1)?.text).toContain("mystery_status");
  });

  it("never fills in its own “passed to a person” sentence: an escalated result without an answer shows its status, one with an answer shows the server's words", async () => {
    replies.push({ status: "escalated", reason: "not_understood" }, TRIP,
      { status: "escalated", reason: "question_needs_policy_answer", answer: "규정에서 그 내용을 찾지 못했어요." }, TRIP);
    const gateway = createLiveGateway();
    const bare = await gateway.sendMessage(TRIP.trip_id, "???", "ko");
    expect(bare.messages.at(-1)?.text).not.toContain("담당자");
    expect(bare.messages.at(-1)?.text).toContain("escalated");
    const answered = await gateway.sendMessage(TRIP.trip_id, "위약금이 있나요?", "ko");
    expect(answered.messages.at(-1)?.text).toBe("규정에서 그 내용을 찾지 못했어요.");
  });

  it("carries what the server did and found: history, warnings, pinned stops and the other options it kept", async () => {
    replies.push({
      ...TRIP,
      items: [{ ...TRIP.items[0], customer_pinned: true, other_options: [{ key: "a", name: "대체 식당" }, { key: 1, name: "깨진 것" }] }, TRIP.items[1]],
      history: [{ version: 2, reason: "closed", at: "2026-10-15T01:30:00+00:00", causes: [{ category: "place", summary: "식당이 문을 닫았어요." }, { kind: "closed" }] }],
      warnings: [{ code: "density_exceeded", date: "2026-10-15", reason: "하루가 빡빡해요", remedy: "일정을 줄이세요" }, { code: "no_reason" }],
    });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.planUrl).toBe("http://x/plan");
    expect(trip.stops[0]).toMatchObject({ pinned: true, otherOptions: [{ key: "a", name: "대체 식당" }] });
    expect(trip.stops[1]).toMatchObject({ pinned: false, otherOptions: [] });
    // Seoul wall clock, and a cause without a sentence still says what kind of thing it was
    expect(trip.history).toEqual([{ version: 2, reason: "closed", at: "2026-10-15 10:30", causes: ["식당이 문을 닫았어요.", "closed"] }]);
    // ★a warning without the server's reason is dropped — it is not replaced by a made-up one
    expect(trip.warnings).toEqual([{ code: "density_exceeded", date: "2026-10-15", reason: "하루가 빡빡해요", remedy: "일정을 줄이세요" }]);
  });

  it("carries restaurant marks with their source and drops a mark it cannot credit", async () => {
    replies.push({
      ...TRIP,
      items: [{ ...TRIP.items[0], badges: [
        { code: "michelin", label: "미쉐린 빕 구르망 (2026)", source: "미쉐린 가이드 서울" },
        { code: "nopo", label: "노포" },
        { code: "nopo", label: "노포", source: "카카오맵 노포 지도", extra: 1 },
      ] }, TRIP.items[1]],
    });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].badges).toEqual([
      { code: "michelin", label: "미쉐린 빕 구르망 (2026)", source: "미쉐린 가이드 서울" },
      { code: "nopo", label: "노포", source: "카카오맵 노포 지도" },
    ]);
    expect(trip.stops[1].badges).toEqual([]);
  });

  it("carries the stop's kind and the server's name for it (2026-10-13 — the icon before the name)", async () => {
    replies.push({ ...TRIP, items: [{ ...TRIP.items[0], kind: "dining", kind_label: "식사" }, TRIP.items[1]] });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect([trip.stops[0].kind, trip.stops[0].kindLabel]).toEqual(["dining", "식사"]);
    // no `kind_label` from the server: the screen names the kind itself
    expect([trip.stops[1].kind, trip.stops[1].kindLabel]).toEqual(["activity", undefined]);
  });

  it("carries the place facts the server sent (dining ledger) and leaves out what it did not", async () => {
    replies.push({
      ...TRIP,
      items: [
        { ...TRIP.items[0], place_info: { address: "서울특별시 종로구 율곡로1길 7", phone: "02-000-0000", category: "한식", hours: [{ day: "월", weekday: 1, open: "11:30", close: "21:00", last_order: "20:30" }, { day: "화" }],
          tags: ["michelin_selected", "card_payment", 3], michelin: { level: "셀렉티드", year: 2026 }, source_note: "" } },
        { ...TRIP.items[1], place_info: { address: "", hours: null, tags: [] } },
      ],
    });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].placeInfo).toEqual({ address: "서울특별시 종로구 율곡로1길 7", phone: "02-000-0000", category: "한식", hours: ["월 11:30–21:00 (주문 마감 20:30)", "화"],
      // `[2026-10-13]` the line kept with its day for the folded card; a rule the server sent no day for is left out
      dayHours: [{ weekday: 1, line: "월 11:30–21:00 (주문 마감 20:30)" }],
      hoursNotes: [], tags: ["michelin_selected", "card_payment"], michelin: { level: "셀렉티드", year: 2026 }, sourceNote: undefined });
    // ★nothing real was sent — no block at all, not a row of "unknown"
    expect(trip.stops[1].placeInfo).toBeNull();
  });

  it("shows closed days, last entry, and the source's hours text for places outside the dining ledger", async () => {
    replies.push({ ...TRIP, items: [
      { ...TRIP.items[0], place_info: { address: "서울특별시 종로구 사직로 161 (세종로)", hours: [{ day: "월", closed: true, open: "09:00", close: "18:00" }, { day: "화", open: "09:00", close: "18:00", last_entry: "17:00", closed: false }],
        hours_text: ["09:00~18:00", "매주 화요일 휴무"], hours_conditions: ["공휴일은 개방"], tags: [], source_note: "ⓒ한국관광공사" } },
      { ...TRIP.items[1], place_info: { hours: null, hours_text: ["09:00~18:00", "매주 화요일 휴무"], hours_conditions: ["매주 화요일 휴무"], tags: [] } },
    ] });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].placeInfo).toMatchObject({ hours: ["월 휴무", "화 09:00–18:00 (입장 마감 17:00)"], hoursNotes: ["공휴일은 개방"], sourceNote: "ⓒ한국관광공사" });
    // no weekly table: the source text is the hours (a repeated line is shown once)
    expect(trip.stops[1].placeInfo).toMatchObject({ hours: [], hoursNotes: ["09:00~18:00", "매주 화요일 휴무"] });
  });

  // `[2026-10-15 사용자 결정 — ST4F-494]` The operator's 「지금 HH:MM」 switch: off only when the server says false; a server without the field shows it (the decided default).
  it("reads the trip screen's now-label switch", async () => {
    replies.push({ ...TRIP, now_label: false }, { ...TRIP, now_label: true }, { ...TRIP });
    const gateway = createLiveGateway();
    expect((await gateway.getTrip(TRIP.trip_id, "ko")).nowLabel).toBe(false);
    expect((await gateway.getTrip(TRIP.trip_id, "ko")).nowLabel).toBe(true);
    expect((await gateway.getTrip(TRIP.trip_id, "ko")).nowLabel).toBe(true);
  });

  it("keeps only Google Maps links for the map app: each stop and each day's route", async () => {
    replies.push({ ...TRIP,
      items: [{ ...TRIP.items[0], map_url: "https://www.google.com/maps/search/?api=1&query=%EA%B2%BD%EB%B3%B5%EA%B6%81" },
        { ...TRIP.items[1], map_url: "javascript:alert(1)" }],
      map: { days: [{ date: "2026-10-15", app_route_urls: ["https://www.google.com/maps/dir/?api=1&origin=a&destination=b", "https://evil.example/x"] },
        { date: "2026-10-16", app_route_urls: [] }] } });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].mapUrl).toBe("https://www.google.com/maps/search/?api=1&query=%EA%B2%BD%EB%B3%B5%EA%B6%81");
    expect(trip.stops[1].mapUrl).toBeUndefined();                       // ★not a map link — no button at all
    expect(trip.dayRoutes).toEqual({ "2026-10-15": ["https://www.google.com/maps/dir/?api=1&origin=a&destination=b"] });
    expect(trip.legs).toEqual({});

    replies.push({ ...TRIP, map: { days: [{ date: "2026-10-15", legs: [
      { from_item_id: "a", to_item_id: "b", url: "https://www.google.com/maps/dir/?api=1&origin=a&destination=b&travelmode=transit" },
      { from_item_id: "b", to_item_id: "c", url: "http://www.google.com/maps/dir/" },            // not https — dropped
      { from_item_id: "c", url: "https://www.google.com/maps/dir/?api=1&origin=c&destination=d" },  // no destination stop — dropped
    ] }] } });
    const withLegs = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(withLegs.legs).toEqual({ "a>b": "https://www.google.com/maps/dir/?api=1&origin=a&destination=b&travelmode=transit" });
  });

  it("keeps the rule sections behind an answer only when the server sends them (development mode)", async () => {
    const gateway = createLiveGateway();
    replies.push({ status: "answered", answer: "비 오면 취소돼요.", basis: { writer: "rules" }, basis_sources: ["t_doc_07#c5", { source: "t_doc_06#c2" }, 3] }, TRIP,
      { status: "answered", answer: "네." }, TRIP);
    const dev = await gateway.sendMessage(TRIP.trip_id, "비 오면?", "ko");
    expect(dev.messages.at(-1)).toMatchObject({ text: "비 오면 취소돼요.", basis: ["t_doc_07#c5", "t_doc_06#c2"] });
    const customer = await gateway.sendMessage(TRIP.trip_id, "또?", "ko");
    expect(customer.messages.at(-1)).not.toHaveProperty("basis");        // ★no field — nothing is shown to the customer
  });

  it("sends the stop the customer picked as item_id, and nothing when none was picked", async () => {
    const gateway = createLiveGateway();
    replies.push({ status: "adjusted", answer: "바꿨어요." }, TRIP, { status: "answered", answer: "네." }, TRIP);
    await gateway.sendMessage(TRIP.trip_id, "다른 데로 바꿔 줘", "ko", "b");
    await gateway.sendMessage(TRIP.trip_id, "하루 요약", "ko");
    const bodies = calls.filter((call) => call.url.endsWith("/messages")).map((call) => JSON.parse(String(call.init.body)));
    expect(bodies[0]).toMatchObject({ message: "다른 데로 바꿔 줘", item_id: "b" });
    expect(bodies[1]).not.toHaveProperty("item_id");
  });

  it("carries restaurant marks with their source and drops a mark it cannot credit", async () => {
    replies.push({
      ...TRIP,
      items: [{ ...TRIP.items[0], badges: [
        { code: "michelin", label: "미쉐린 빕 구르망 (2026)", source: "미쉐린 가이드 서울" },
        { code: "nopo", label: "노포" },
        { code: "nopo", label: "노포", source: "카카오맵 노포 지도", extra: 1 },
      ] }, TRIP.items[1]],
    });
    const trip = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(trip.stops[0].badges).toEqual([
      { code: "michelin", label: "미쉐린 빕 구르망 (2026)", source: "미쉐린 가이드 서울" },
      { code: "nopo", label: "노포", source: "카카오맵 노포 지도" },
    ]);
    expect(trip.stops[1].badges).toEqual([]);
  });

  it("does not quietly become a new user when the session has ended", async () => {
    useBrowser(true);
    replies.push(new Response(JSON.stringify({ error: { code: "unauthenticated", message: "no" } }), { status: 401 }));
    const failure = await createLiveGateway().getTrip(TRIP.trip_id, "ko").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(LiveError);
    expect((failure as LiveError).code).toBe("session_expired");
    expect(sessionCalls.some((call) => new URL(call.url).pathname === "/v1/web/auth/session")).toBe(false);   // ★no second session made behind the user's back
  });

  it("keeps the folded rest of an answer and the 「혹시 이런 뜻이었나요?」 readings the server offers with it", async () => {
    const more = ["· 운영시간 원문: 10:00~18:30", "· 쉬는 날: 월요일"].join("\n");
    replies.push({ status: "answered", answer: "세종이야기 — 서울특별시 종로구 세종대로 지하175", more,
      choices_title: "혹시 이런 뜻이었나요?", choices: [{ label: "세종이야기 운영시간", message: "세종이야기 몇 시까지 해?" }] }, TRIP);
    const trip = await createLiveGateway().sendMessage(TRIP.trip_id, "그건 어딧는거야", "ko");
    expect(trip.messages.at(-1)).toMatchObject({ more, choicesTitle: "혹시 이런 뜻이었나요?",
      choices: [{ label: "세종이야기 운영시간", message: "세종이야기 몇 시까지 해?" }] });
  });

  it("shows the conversation from the server's record, keeping this tab's choices on the latest answer", async () => {
    const gateway = createLiveGateway();
    const question = ["다음 중 하나인가요?", "1) 1일차 저녁 식당 바꾸기", "2) 1일차 저녁 식당 알아보기"].join("\n");
    replies.push({ status: "clarify", answer: question,
      choices: [{ label: "1일차 저녁 식당 바꾸기", message: "1일차 저녁 식당 바꿔 줘" }, { label: "빈 것" }] }, TRIP);
    const asked = await gateway.sendMessage(TRIP.trip_id, "저녁", "ko");
    expect(asked.messages.at(-1)?.choices).toEqual([{ label: "1일차 저녁 식당 바꾸기", message: "1일차 저녁 식당 바꿔 줘" }]);
    // another device: the server's record is the conversation; this tab adds the choices to the same answer
    // (the record's times are after this tab's messages: the record has caught up with everything this tab saw)
    chat = [{ role: "customer", text: "저녁", case_id: "c1", at: "2999-01-01T00:00:00+09:00" },
      { role: "assistant", text: question, case_id: "c1", at: "2999-01-01T00:00:01+09:00" }];
    const trip = await gateway.getTrip(TRIP.trip_id, "ko");
    expect(trip.messages.map((message) => [message.role, message.text.split("\n")[0]])).toEqual([["user", "저녁"], ["assistant", "다음 중 하나인가요?"]]);
    expect(trip.messages[1].choices?.[0].message).toBe("1일차 저녁 식당 바꿔 줘");
    // the record lags: what this tab just said and heard (after the record's last turn) stays at the end, once
    chat = [{ role: "customer", text: "저녁", case_id: "c1", at: "2020-01-01T10:00:00+09:00" }];
    const lagging = await gateway.getTrip(TRIP.trip_id, "ko");
    expect(lagging.messages.map((message) => message.role)).toEqual(["user", "user", "assistant"]);
    expect(lagging.messages.at(-1)?.text).toBe(question);
    chat = [{ role: "customer", text: "저녁", case_id: "c1", at: "2026-09-29T10:00:00+09:00" },
      { role: "assistant", text: question, case_id: "c1", at: "2999-01-01T00:00:00+09:00" }];
    // a fresh browser: the record alone, no choices to invent
    window.sessionStorage.removeItem(`tripilot.web.live.messages:${TRIP.trip_id}`);
    expect((await gateway.getTrip(TRIP.trip_id, "ko")).messages[1]).not.toHaveProperty("choices");
  });

  it("does not append old local turns after they leave the recent server window", async () => {
    window.sessionStorage.setItem(`tripilot.web.live.messages:${TRIP.trip_id}`, JSON.stringify([
      { id: "old-q", role: "user", text: "옛 질문", createdAt: "2020-01-01T00:00:00Z" },
      { id: "old-a", role: "assistant", text: "옛 답변", createdAt: "2020-01-01T00:00:01Z" },
    ]));
    chat = [{ role: "customer", text: "최근 질문", case_id: "new", turn_id: 55, at: "2021-01-01T00:00:00Z" },
      { role: "assistant", text: "최근 답변", case_id: "new", turn_id: 56, at: "2021-01-01T00:00:01Z" }];
    const result = await createLiveGateway().getTrip(TRIP.trip_id, "ko");
    expect(result.messages.map((message) => message.text)).toEqual(["최근 질문", "최근 답변"]);
    expect(result.messages[1].id).toBe("chat-56");
  });

  it("matches identical answer text by case and keeps a pending answer without repeating its question", async () => {
    const gateway = createLiveGateway();
    replies.push({ status: "answered", case_id: "pending", answer: "같은 답변" }, TRIP);
    await gateway.sendMessage(TRIP.trip_id, "새 질문", "ko");
    chat = [{ role: "customer", text: "옛 질문", case_id: "earlier", at: "2020-01-01T00:00:00Z" },
      { role: "assistant", text: "같은 답변", case_id: "earlier", at: "2020-01-01T00:00:01Z" },
      { role: "customer", text: "새 질문", case_id: "pending", at: new Date().toISOString() }];
    const result = await gateway.getTrip(TRIP.trip_id, "ko");
    expect(result.messages.filter((message) => message.text === "새 질문")).toHaveLength(1);
    expect(result.messages.filter((message) => message.text === "같은 답변")).toHaveLength(2);
    expect(result.messages.at(-1)?.caseId).toBe("pending");
  });

  // 2026-09-30 (teammate question Q-05): sending and re-reading are two steps.
  it("keeps the server's reply when only the re-read after it fails — it is not reported as 'not sent'", async () => {
    const gateway = createLiveGateway();
    replies.push({ status: "answered", answer: "경복궁은 사직로 161에 있어요." }, new Response("{}", { status: 503 }));
    const failure = await gateway.sendMessage(TRIP.trip_id, "경복궁 어디야", "ko").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(LiveError);
    expect((failure as LiveError).code).toBe("reply_kept");
    const sent = ((failure as LiveError).detail as { sent: { role: string; text: string }[] }).sent;
    expect(sent.map((message) => [message.role, message.text])).toEqual([["user", "경복궁 어디야"], ["assistant", "경복궁은 사직로 161에 있어요."]]);
    // the next successful read still shows it (this tab's copy, until the server's record has it)
    expect((await gateway.getTrip(TRIP.trip_id, "ko")).messages.map((message) => message.text)).toContain("경복궁은 사직로 161에 있어요.");
  });

  it("sends a failed message again with the same request id, so the server does not act on it twice", async () => {
    const gateway = createLiveGateway();
    const ids = () => calls.filter((call) => call.url.endsWith("/messages")).map((call) => JSON.parse(String(call.init.body)).request_id as string);
    replies.push(new Response("{}", { status: 500 }));  // a failed send (a gateway 502 is now sent again by itself — stream.ts)
    await expect(gateway.sendMessage(TRIP.trip_id, "점심 바꿔 줘", "ko", "b")).rejects.toBeInstanceOf(LiveError);
    replies.push({ status: "adjusted", answer: "바꿨어요." });
    await gateway.sendMessage(TRIP.trip_id, "점심 바꿔 줘", "ko", "b");
    expect(ids()[1]).toBe(ids()[0]);
    // answered: the same words later are a new request
    replies.push({ status: "answered", answer: "네." });
    await gateway.sendMessage(TRIP.trip_id, "점심 바꿔 줘", "ko", "b");
    expect(ids()[2]).not.toBe(ids()[0]);
  });

  it("does not reuse a failed message's request id for different words or a different stop", async () => {
    const gateway = createLiveGateway();
    const ids = () => calls.filter((call) => call.url.endsWith("/messages")).map((call) => JSON.parse(String(call.init.body)).request_id as string);
    replies.push(new Response("{}", { status: 500 }));  // a failed send (a gateway 502 is now sent again by itself — stream.ts)
    await expect(gateway.sendMessage(TRIP.trip_id, "저녁 바꿔 줘", "ko", "a")).rejects.toBeInstanceOf(LiveError);
    replies.push({ status: "answered", answer: "네." });
    await gateway.sendMessage(TRIP.trip_id, "저녁 바꿔 줘", "ko", "b");
    expect(ids()[1]).not.toBe(ids()[0]);
  });

  // 2026-09-30: the customer's position from the browser, sent only after the server asked for it.
  describe("continuing a request that is waiting for input", () => {
    const wait = { status: "clarify", case_status: "waiting_input", case_id: "case-dining", version: 4,
      resume_token: "resume-private", answer: "몇 분쯤 늦으세요?", required_input_schema: { required: ["minutes"] } };
    const resume = { case_id: wait.case_id, resume_token: wait.resume_token, expected_version: wait.version };
    const bodies = () => calls.filter((call) => call.url.endsWith("/messages")).map((call) => JSON.parse(String(call.init.body)) as Record<string, unknown>);

    it("sends the explicit summary day without consuming a pending change reply", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      replies.push({ status: "answered", answer: "하루 일정", case_status: "completed" });
      await gateway.sendMessage(TRIP.trip_id, "2026-10-15 하루 일정을 요약해 주세요.", "ko", null, null, undefined, undefined, "2026-10-15");
      expect(bodies()[1]).toMatchObject({ summary_day: "2026-10-15" });
      expect(bodies()[1]).not.toHaveProperty("resume_token");
      expect(bodies()[1]).not.toHaveProperty("case_id");
      replies.push({ status: "answered", answer: "반영했어요" });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[2]).toMatchObject(resume);
    });

    it("retries a summary with its date and the same request id", async () => {
      const gateway = createLiveGateway();
      replies.push(new Response("{}", { status: 503 }));
      await expect(gateway.sendMessage(TRIP.trip_id, "2026-10-15 하루 일정을 요약해 주세요.", "ko", null, null, undefined, undefined, "2026-10-15")).rejects.toBeInstanceOf(LiveError);
      replies.push({ status: "answered", answer: "하루 일정" });
      await gateway.sendMessage(TRIP.trip_id, "2026-10-15 하루 일정을 요약해 주세요.", "ko", null, null, undefined, undefined, "2026-10-15");
      expect(bodies()[1].request_id).toBe(bodies()[0].request_id);
      expect(bodies()[1].summary_day).toBe("2026-10-15");
    });

    it("continues the same Case and clears it after completion without storing its token", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      const asked = await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      expect(asked.messages.at(-1)?.awaitingReply).toBe(true);
      expect(window.sessionStorage.getItem("tripilot.web.live.messages:" + TRIP.trip_id)).not.toContain(wait.resume_token);
      replies.push({ case_status: "completed", answer: "30분 늦는 것으로 반영했어요." });
      const finished = await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[1]).toMatchObject(resume);
      expect(finished.messages.some((message) => message.awaitingReply)).toBe(false);
      replies.push({ case_status: "completed", answer: "네." });
      await gateway.sendMessage(TRIP.trip_id, "다음 일정은요?", "ko");
      expect(bodies()[2]).not.toHaveProperty("case_id");
    });

    it("keeps pending requests separate when switching between trips", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      replies.push({ case_status: "completed", answer: "다른 여행이에요." });
      await gateway.sendMessage("another-trip", "30분이요", "ko");
      expect(bodies()[1]).not.toHaveProperty("case_id");
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[2]).toMatchObject(resume);
    });

    it("uses the renewed token and Case version when more information is still needed", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      replies.push({ ...wait, version: 8, resume_token: "renewed-private" });
      await gateway.sendMessage(TRIP.trip_id, "많이요", "ko");
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[2]).toMatchObject({ case_id: wait.case_id, resume_token: "renewed-private", expected_version: 8 });
    });

    it("retries a failed reply with the same request id and resume credentials", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      replies.push(new Response("{}", { status: 500 }));  // a failed send (a gateway 502 is now sent again by itself — stream.ts)
      await expect(gateway.sendMessage(TRIP.trip_id, "30분이요", "ko")).rejects.toBeInstanceOf(LiveError);
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[2]).toEqual(bodies()[1]);
    });

    it("does not restore resume credentials from conversation storage after a reload", async () => {
      replies.push(wait);
      await createLiveGateway().sendMessage(TRIP.trip_id, "늦어요", "ko");
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await createLiveGateway().sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[1]).not.toHaveProperty("case_id");
      expect(bodies()[1]).not.toHaveProperty("resume_token");
    });

    it("reads waiting and completion from SSE results just as from JSON", async () => {
      const gateway = createLiveGateway();
      replies.push(new Response(`event: result\ndata: ${JSON.stringify(wait)}\n\n`, { headers: { "Content-Type": "text/event-stream" } }));
      expect((await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko")).messages.at(-1)?.awaitingReply).toBe(true);
      replies.push(new Response('event: result\ndata: {"case_status":"completed","answer":"반영했어요."}\n\n', { headers: { "Content-Type": "text/event-stream" } }));
      expect((await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko")).messages.at(-1)?.awaitingReply).toBeUndefined();
      expect(bodies()[1]).toMatchObject(resume);
    });

    it("drops credentials when the login session is no longer the one that received them", async () => {
      const gateway = createLiveGateway();
      replies.push(wait);
      await gateway.sendMessage(TRIP.trip_id, "늦어요", "ko");
      resetSessionState();
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko");
      expect(bodies()[1]).not.toHaveProperty("case_id");
    });

    it("accepts explicit correlation and never reuses a failed reply id for a different Case", async () => {
      const gateway = createLiveGateway();
      replies.push(new Response("{}", { status: 500 }));  // a failed send (a gateway 502 is now sent again by itself — stream.ts)
      await expect(gateway.sendMessage(TRIP.trip_id, "30분이요", "ko", null, null, undefined, resume)).rejects.toBeInstanceOf(LiveError);
      const other = { ...resume, case_id: "another-case", resume_token: "another-private" };
      replies.push({ case_status: "completed", answer: "반영했어요." });
      await gateway.sendMessage(TRIP.trip_id, "30분이요", "ko", null, null, undefined, other);
      expect(bodies()[0]).toMatchObject(resume);
      expect(bodies()[1]).toMatchObject(other);
      expect(bodies()[1].request_id).not.toBe(bodies()[0].request_id);
    });
  });

  it("marks an answer that needs the customer's position, and sends the position only when given", async () => {
    const gateway = createLiveGateway();
    const bodies = () => calls.filter((call) => call.url.endsWith("/messages")).map((call) => JSON.parse(String(call.init.body)) as Record<string, unknown>);
    replies.push({ status: "answered", answer: "현재 위치를 알려 주시면 길을 찾아 드릴게요.", needs_location: true });
    const asked = await gateway.sendMessage(TRIP.trip_id, "여기서 경복궁 어떻게 가?", "ko");
    expect(asked.messages.at(-1)).toMatchObject({ role: "assistant", needsLocation: true });
    expect(bodies()[0]).not.toHaveProperty("location");

    replies.push({ status: "answered", answer: "지금 계신 곳에서 도보 12분이에요.", needs_location: true });
    const answered = await gateway.sendMessage(TRIP.trip_id, "여기서 경복궁 어떻게 가?", "ko", null, { lat: 37.57, lng: 126.98, accuracyM: 20, at: "2026-09-30T06:00:00.000Z" });
    expect(bodies()[1].location).toEqual({ lat: 37.57, lng: 126.98, accuracy_m: 20, at: "2026-09-30T06:00:00.000Z" });
    expect(bodies()[1].request_id).not.toBe(bodies()[0].request_id);
    // ★a position was sent: no second offer to send it again
    expect(answered.messages.at(-1)).not.toHaveProperty("needsLocation");
  });
});
