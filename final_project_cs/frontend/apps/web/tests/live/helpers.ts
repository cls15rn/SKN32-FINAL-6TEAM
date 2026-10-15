import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { TERMS_VERSION } from "../../src/features/consent/terms-content";

export const STUB = `http://127.0.0.1:${process.env.STUB_PORT ?? 8043}`;
/** The web app under test (`tests/live/serve.mjs`). */
export const APP = `http://127.0.0.1:${process.env.LIVE_PORT ?? 3102}`;
export const TRIP_ID = "11111111-2222-3333-4444-555555555555";
export const KEY_STORAGE = "tripilot.web.user-key.v1";

/** `key`: the legacy `X-User-Key` header (agents, an older page); `session`: the session cookie's value; `csrf`: the `X-CSRF-Token` header. */
export interface LoggedRequest { method: string; path: string; /** `[2026-10-05]` The query string ("?detail=true"), null when there is none. */ query: string | null; key: string | null; session: string | null; csrf: string | null; accept: string | null; body: Record<string, unknown> | null }

/** Talks to the test mock server's test control (never part of the real API). */
export function mockServer(request: APIRequestContext) {
  return {
    reset: async () => { await request.post(`${STUB}/__test/reset`); },
    scenario: async (change: Record<string, unknown>) => { await request.post(`${STUB}/__test/scenario`, { data: change }); },
    /** `[2026-10-05]` What the server's consent record says (another device agreed or withdrew): `{ privacy: false }`. Needs the scenario `consents: "on" | "gate"`. */
    consents: async (items: ConsentSeed, version?: string) => { await request.post(`${STUB}/__test/consents`, { data: { items, version } }); },
    log: async (): Promise<LoggedRequest[]> => (await request.get(`${STUB}/__test/log`)).json(),
    /** Ring the "this trip changed" bell on every open stream; returns how many streams were open. */
    ring: async (kinds?: string[]): Promise<number> => (await (await request.post(`${STUB}/__test/ring`, { data: kinds ? { kinds } : {} })).json()).rang,
    /** Close every open bell stream, as a dropped connection would. */
    hangup: async (): Promise<number> => (await (await request.post(`${STUB}/__test/hangup`)).json()).closed,
    /** Requests received for a path (matched by suffix), oldest first. */
    async received(method: string, suffix: string): Promise<LoggedRequest[]> {
      return (await this.log()).filter((entry) => entry.method === method && entry.path.endsWith(suffix));
    },
  };
}

/** The session cookie the test mock server knows (a returning visitor's browser). */
export const KNOWN_SESSION = "known-session";
export const SESSION_COOKIE = "tripilot_sid_dev";

export const CONSENT_STORAGE = "tripilot.web.consent.v1";
export type ConsentSeed = Partial<Record<"service_terms" | "privacy" | "sensitive" | "location" | "alert_channel" | "service_improvement", boolean>>;

/**
 * `[2026-10-05]` The customer has already agreed to the CURRENT terms in this browser: the required items (and `extra` ones, e.g. `{ location: true }`), recorded as the
 * server's record too (`synced`). Put in before the page loads, only when this browser has no consent record yet - so a test can seed its own and keep it.
 * ★The app is closed to anyone who has not agreed (`ConsentGate`); a test of a returning customer starts with this, a test of a FIRST visit does not.
 */
export async function agree(page: Page, extra: ConsentSeed = {}) {
  await page.addInitScript(([key, version, items]) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version, items: { service_terms: true, privacy: true, sensitive: false, location: false, alert_channel: false, service_improvement: false, ...items }, at: "2026-10-05T09:00:00.000Z", synced: true }));
  }, [CONSENT_STORAGE, TERMS_VERSION, extra] as const);
}

/**
 * `[2026-10-05]` Turn optional consents ON in the record this browser already carries (the one `start` put in): for a test whose `beforeEach` has already called `start`,
 * where `agree(page, { location: true })` would come too late (it keeps a record that is there). Runs after the earlier init scripts, so it sees the record they wrote.
 */
export async function alsoAgree(page: Page, extra: ConsentSeed) {
  await page.addInitScript(([key, items]) => {
    const stored = JSON.parse(localStorage.getItem(key) ?? "null") as { items?: Record<string, boolean> } | null;
    if (stored) localStorage.setItem(key, JSON.stringify({ ...stored, items: { ...stored.items, ...items } }));
  }, [CONSENT_STORAGE, extra] as const);
}

/**
 * Korean UI, and — unless `session` is null (a first visit) — a session cookie in this browser. ★`[2026-10-04]` The page keeps no key any more:
 * the server's cookie is what makes a browser a returning one. `"acop_u_known"` (the old name of the known key) means the known session; any other string
 * is a cookie value the mock server does not know (an ended session).
 */
export async function start(page: Page, session: string | null = "acop_u_known", seedConsent = true) {
  await page.addInitScript(() => {
    if (!localStorage.getItem("tripilot.web.settings.v1")) localStorage.setItem("tripilot.web.settings.v1", JSON.stringify({ language: "ko" }));
  });
  if (session) {
    await page.context().addCookies([{ name: SESSION_COOKIE, value: session === "acop_u_known" ? KNOWN_SESSION : session, url: STUB }]);
    if (seedConsent) await agree(page);                  // a returning customer has agreed to the terms; a first visit (no session) has not
  }
}

/** Korean UI, no cookie, and a user key an older version of the page kept in this browser (the mock server knows `acop_u_known`). */
export async function startWithOldKey(page: Page, key = "acop_u_known") {
  await start(page, null);
  await page.addInitScript(([storageKey, value]) => { if (!localStorage.getItem(storageKey)) localStorage.setItem(storageKey, value); }, [KEY_STORAGE, key] as const);
}

/** Finish the onboarding: agree to the terms, skip every question but the last, answer that one. Leaves the summary open. */
export async function finishOnboarding(page: Page, beforeTerms?: () => Promise<void>, optional: Array<"sensitive" | "location" | "alert_channel" | "service_improvement"> = []) {
  await page.goto("/start");
  await beforeTerms?.();
  await page.getByRole("button", { name: /약관 동의/ }).click();
  await agreeTerms(page, optional);
  await page.getByRole("button", { name: "시작하기" }).click();
  await expect(page.locator("#question-title-0")).toBeFocused();   // 「시작하기」의 넘김이 끝나야 다음 누름을 받는다
  const skip = page.getByRole("button", { name: "응답하지 않고 넘어가기" });
  // ★카드가 넘어가는 동안의 누름은 화면이 무시한다 — 넘김이 끝나 다음 카드 제목으로 초점이 옮겨진 것을 보고 다음을 누른다
  for (let index = 0; index < 5; index += 1) {
    await skip.click();
    await expect(page.locator(`#question-title-${index + 1}`)).toBeFocused();
  }
  await page.getByRole("button", { name: "여유롭게" }).click();
  await page.getByRole("button", { name: "설정 완료" }).click();
  await expect(page.getByRole("heading", { name: "여행 취향 설정 완료" })).toBeVisible();
}

/** Open the registration page once React has taken it over (a field filled before that is lost). */
export async function openRegistration(page: Page) {
  await page.goto("/trips/new");
  await expect(page.locator("#plan-source")).toBeVisible();
  await page.waitForFunction(() => {
    const element = document.querySelector("textarea");
    return Boolean(element) && Object.keys(element as object).some((key) => key.startsWith("__reactFiber"));
  });
}

/** The registration page's three panels (live): by the heading each shows. */
export const PANES = {
  text: (page: Page) => page.getByRole("region", { name: "직접 입력" }),
  files: (page: Page) => page.getByRole("region", { name: "파일 선택", exact: true }),
  plan: (page: Page) => page.getByRole("region", { name: "계획 짜 주기 (테스트)" }),
};

/** Fill the third panel, 「계획 짜 주기 (테스트)」: first day, days, travelers and (optionally) the wish. */
export async function fillPlanAsk(page: Page, ask: { start: string; days: number; party: number; wish?: string }) {
  await page.getByLabel("첫날", { exact: true }).fill(ask.start);
  await page.getByLabel("일수", { exact: true }).selectOption(String(ask.days));
  await page.getByLabel("인원", { exact: true }).selectOption(String(ask.party));
  if (ask.wish !== undefined) await page.getByLabel("원하는 여행", { exact: false }).fill(ask.wish);
}

/** A first day that is surely not past: a week from today in Seoul. */
export function weekAhead(): string {
  return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
}

// ── helpers that came from the old demo-build suite (`tests/e2e`, removed 2026-10-03) ─────────────────────────

/** Korean UI and no stored key: a first visit, which asks the test mock server for nothing until the customer registers. */
export const useKorean = (page: Page) => start(page, null);

/**
 * Next draws a page on the server first, and a click before React has taken it over does nothing. Wait until the element is React's
 * (found 2026-10-03: a press did nothing when the machine was busy).
 */
export async function hydrated(page: Page, selector = "textarea") {
  await page.waitForFunction((css) => {
    const element = document.querySelector(css);
    return Boolean(element) && Object.keys(element as object).some((key) => key.startsWith("__reactFiber"));
  }, selector);
}

export async function noHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

/**
 * Terms card open on /start: tick each REQUIRED item (optional items are left unticked unless `optional` names them), then go on to the preferences.
 * `[2026-10-05]` Consent is per item: 서비스 이용약관 and 개인정보 수집·이용 are required. `[2026-10-05 사용자 지시]` A required item is ticked at once - no reading to the end first.
 * ★`[2026-10-06 사용자 지시]` The FIRST agreement goes straight on to the plan screen (the preference survey is not part of the first run any more). So that the many
 *   start-screen tests that continue with the preferences keep their steps, this helper then opens the preferences from the menu, as a customer would (`openPreferencesFromMenu`).
 * ★`[2026-10-12 사용자 지시]` A customer who had already agreed goes to My trips too (the press never opens the preferences card) — the helper then opens the preferences from the menu the same way.
 */
export async function agreeTerms(page: Page, optional: Array<"sensitive" | "location" | "alert_channel" | "service_improvement"> = []) {
  await expect(page.getByRole("button", { name: /약관 동의/ })).toHaveAttribute("aria-expanded", "true");
  const alreadyAgreed = (await page.locator("#consent-service_terms").isChecked()) && (await page.locator("#consent-privacy").isChecked());
  for (const code of ["service_terms", "privacy"]) {
    if (await page.locator(`#consent-${code}`).isChecked()) continue;                       // a returning customer's boxes are already on - ticking again would untick
    await page.locator(`li[data-doc="${code}"] label`).click();
    await expect(page.locator(`#consent-${code}`)).toBeChecked();
  }
  for (const code of optional) await page.locator(`#consent-${code}`).evaluate((input: HTMLInputElement) => { if (!input.checked) input.click(); });
  await page.getByRole("button", { name: "동의하고 다음으로" }).click();
  await expect(page).toHaveURL(alreadyAgreed ? /\/trips$/ : /\/trips\/new$/);
  await openPreferencesFromMenu(page);
}

/**
 * `[2026-10-06]` In the menu, the preferences row opens the start screen on its preferences card (the survey is done on its own, not in the first run).
 * `[2026-10-13 메뉴 = 마이페이지]` The row 「여행 취향 설문」 now has a note under it (what was chosen); the preferences line on My page is gone (My page is the settings screen now).
 */
export async function openPreferencesFromMenu(page: Page) {
  await page.getByRole("button", { name: "메뉴", exact: true }).click();
  await page.getByRole("dialog", { name: "메뉴" }).getByRole("button", { name: /^여행 취향 설문/ }).click();
  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByRole("button", { name: /여행 취향 알아보기/ })).toHaveAttribute("aria-expanded", "true");
}

/**
 * `[2026-10-13 메뉴 = 마이페이지]` The language card left the menu for the settings screen (menu › 설정): open it there, pick a language by its own name,
 * and come back to the screen the test was on (in-app back). `menu` is the menu button's name in the language shown now.
 */
export async function pickLanguage(page: Page, menu: "메뉴" | "Menu", language: "한국어" | "English") {
  await page.getByRole("button", { name: menu, exact: true }).click();
  await page.getByRole("dialog", { name: menu }).getByRole("link", { name: menu === "메뉴" ? /^설정/ : /^Settings/ }).click();
  await expect(page).toHaveURL(/\/mypage$/);
  await page.getByRole("button", { name: /LANGUAGE/ }).click();
  await page.getByRole("button", { name: language, exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", language === "English" ? "en" : "ko");
  await page.goBack();
}

/** The start screen is asked once and kept in this browser. For a test that needs the first-time screen again: forget what it kept, then reload. */
export async function forgetStartScreen(page: Page) {
  await page.evaluate(() => localStorage.removeItem("tripilot.web.onboarding.v1"));
  await page.reload();
}

/**
 * `[2026-10-06 사용자 지시]` 「계획 확인하기」: before the plan is read the Course Keeper card opens - 「켜고 진행」 goes on (or 「건너뛰기 — 끄고 진행」, which sends `on_disruption: "ask_first"`).
 * A test that is not about the card goes through it with this.
 */
export async function checkPlan(page: Page, choice: "켜고 진행" | "건너뛰기 — 끄고 진행" = "켜고 진행") {
  await page.getByRole("button", { name: "계획 확인하기" }).click();
  // `[2026-10-06 사용자 지시]` Turned on once, it stays on for the next plan and the card is not asked again - so the card may not come.
  const card = page.getByRole("dialog");
  const asked = await card.waitFor({ state: "visible", timeout: 2500 }).then(() => true, () => false);
  if (asked) await card.getByRole("button", { name: choice }).click();
}

/**
 * `[2026-10-07]` The registered trip's screen (map + sheet, `features/trip/trip-screen.tsx`): its plan pane is there once the trip has loaded. (The old screen's heading 「나의 여행」 is gone —
 * the header's title is the trip's own name.)
 */
export const tripScreen = (page: Page) => page.locator("#trip-pane-schedule");

/** Open the trip's notices (the bell beside the menu): the choices, the undo, the warnings, the notices, the history, the plan links. */
export async function openNotices(page: Page) {
  await page.getByRole("button", { name: "알림 센터 열기" }).click();
  await expect(page.getByRole("dialog", { name: "알림" })).toBeVisible();
  return page.getByRole("dialog", { name: "알림" });
}

/**
 * The two panes of the trip's sheet. `[2026-10-14 사용자 결정 — ST4F-493]` The sheet head's chat button is gone: the chat pane opens when the chat field (behind the round
 * 「채팅 입력 열기」 button) takes the focus, and its ← (「일정으로 돌아가기」) or Escape goes back to the schedule. Which pane is open is read from the panes themselves (`inert`).
 */
export const chatButton = (page: Page) => page.getByRole("button", { name: "채팅 입력 열기" });
export const chatPaneOpen = async (page: Page) => (await page.locator("#trip-pane-chat").getAttribute("inert")) === null;

/** Opens a pane of the trip's sheet (nothing to do when it is open already). */
export async function openPane(page: Page, name: "일정" | "채팅") {
  const chat = name === "채팅";
  if ((await chatPaneOpen(page)) !== chat) {
    if (chat) {
      if (await chatButton(page).count()) await chatButton(page).click();
      await page.locator("#trip-chat-message").focus();
    } else await page.getByRole("button", { name: "일정으로 돌아가기" }).click();
  }
  await expect(page.locator(chat ? "#trip-pane-chat" : "#trip-pane-schedule")).not.toHaveAttribute("inert");
}

/** `[2026-10-07 목업 C안 B]` Open the trip's name in the header: the row 「여행계획서 열기 · 공유하기」 under it. */
export async function openTitle(page: Page) {
  await page.locator("#trip-title-button").click();
  await expect(page.getByRole("group", { name: "여행계획서" })).toBeVisible();
  return page.getByRole("group", { name: "여행계획서" });
}

/** A day chip in the trip's sheet head (「전체 · 1일차 · 2일차 …」). */
export const dayTab = (page: Page, name: RegExp) => page.getByRole("tablist", { name: "일차 고르기" }).getByRole("tab", { name });

/**
 * Register a trip the way a customer does, against the test mock server: write a plan, 「계획 확인하기」, wait for the plan-check screen's result,
 * 「여행 등록」, and land on the trip screen (the mock's one trip, `TRIP_ID`). Needs the mock's default scenario (a plan whose check passes).
 */
/**
 * `[2026-10-13]` The mock server's trip is on 2026-10-01. Since the 10-12 timeline (「지난 일정」 — a stop whose end has passed by the device clock is one grey line), the trip
 * screen draws every stop of it as past once that day is over: no card, no booking tag, no way row. A test of the trip screen as it looks before the trip starts the page's
 * clock on the day before and lets it run (a stopped clock would also stop gestures that time the pointer).
 */
export async function beforeTheTrip(page: Page) {
  await page.clock.install({ time: new Date("2026-09-30T12:00:00+09:00") });
  await page.clock.resume();
}

export async function registerStubTrip(page: Page, plan = "10/1 09:00 경복궁 관람") {
  await beforeTheTrip(page);
  await openRegistration(page);
  await page.getByLabel("나의 여행 계획").fill(plan);
  await checkPlan(page);
  await expect(page).toHaveURL(/\/intakes\/[0-9a-f-]+$/);
  await page.getByRole("button", { name: "여행 등록" }).click();
  await expect(page).toHaveURL(new RegExp(`/trips/${TRIP_ID}$`));
  await expect(tripScreen(page)).toBeVisible();
}
