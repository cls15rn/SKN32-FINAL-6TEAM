import { expect, test, type Page } from "@playwright/test";
import { beforeTheTrip, mockServer, openTitle, start, TRIP_ID, tripScreen } from "./helpers";

/**
 * `[2026-10-07 사용자 결정 — 목업 C안 3단계]` 제목 펼침(B안) · 여행계획서 공유 · 일정 「상세 보기」. mock 서버 시험이다 — 화면 반응만 본다.
 *   - 제목을 누르면 칸이 늘고 연필 · 아래 줄 「여행계획서 열기 · 공유하기」. ▴ · 바깥 누름 · Esc 로 접는다. ★등록한 여행의 이름 바꾸기는 서버에 없다 — 입력칸은 열리지만 저장하면 「준비 중」이라 말하고 이름은 그대로다.
 *   - 공유하기: 보낼 곳(카카오톡은 키가 없어 준비 중 · 메시지 sms: · 텔레그램 공유 주소 · 링크 복사 · 더보기)과 「파일로 내려받기」.
 *   - 상세 보기: 그날 일정마다 카드 한 장(옆으로 넘기기 · 점), 지도는 그날 일정만(보는 일정 진하게, 경로선 없음), 사진은 서버가 아직 주지 않아 준비 중.
 */
// `[2026-10-13]` 가짜 서버 여행(2026-10-01)이 「지난 일정」으로 그려지지 않게 여행 전날로 시계를 고정한다(helpers.ts beforeTheTrip).
test.beforeEach(async ({ page }) => { await beforeTheTrip(page); });
test.beforeEach(async ({ page, request }) => {
  await mockServer(request).reset();
  await start(page);
});

async function openTrip(page: Page) {
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(tripScreen(page)).toBeVisible();
}
const titleButton = (page: Page) => page.locator("#trip-title-button");

test("제목을 누르면 칸이 늘고 연필과 「여행계획서 열기 · 공유하기」가 뜨며, 바깥을 누르거나 Esc 로 접는다", async ({ page }) => {
  await openTrip(page);
  await expect(titleButton(page)).toHaveAccessibleName("계획 이름 · 내 여행");
  await expect(titleButton(page)).toHaveAttribute("aria-expanded", "false");
  const menu = await openTitle(page);
  await expect(titleButton(page)).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "계획 이름 바꾸기 · 지금 이름은 내 여행" })).toBeVisible();
  const plan = menu.getByRole("link", { name: "여행계획서 열기" });
  await expect(plan).toHaveAttribute("href", new RegExp(`/plan/${TRIP_ID}\\?t=`));
  await expect(plan).toHaveAttribute("target", "_blank");
  await page.mouse.click(200, 200);                                                                     // 지도(바깥)를 누르면 접힌다
  await expect(menu).toHaveCount(0);
  await openTitle(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("group", { name: "여행계획서" })).toHaveCount(0);
  await expect(titleButton(page)).toBeFocused();
});

test("이름을 고쳐 저장해도 서버에 이름 바꾸기가 없으니 「준비 중」이라 말하고 이름은 그대로다 — Esc 는 아무 말 없이 그만둔다", async ({ page, request }) => {
  const server = mockServer(request);
  await openTrip(page);
  await openTitle(page);
  await page.getByRole("button", { name: "계획 이름 바꾸기 · 지금 이름은 내 여행" }).click();
  const field = page.getByRole("textbox", { name: "계획 이름" });
  await expect(field).toBeFocused();
  await expect(field).toHaveValue("내 여행");
  await field.press("Escape");
  await expect(titleButton(page)).toHaveAccessibleName("계획 이름 · 내 여행");
  await expect(page.getByRole("status").filter({ hasText: "준비 중" })).toHaveCount(0);
  await titleButton(page).click();
  await titleButton(page).click();                                                                       // 펼친 뒤 이름을 한 번 더 누르면 고친다
  await field.fill("가을 서울");
  await field.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "이름 바꾸기는 준비 중이에요" })).toContainText("저장하지 않았어요");
  await expect(titleButton(page)).toHaveAccessibleName("계획 이름 · 내 여행");                             // 바뀐 것처럼 보이지 않는다
  const writes = (await server.log()).filter((entry) => entry.path.startsWith(`/v1/web/trips/${TRIP_ID}`) && !["GET", "OPTIONS"].includes(entry.method));
  expect(writes).toEqual([]);                                                                            // 서버로 아무것도 보내지 않았다
});

test("공유하기는 아래에서 올라오는 창으로 보낼 곳과 「파일로 내려받기」를 보이고, 링크 복사는 복사 뒤 창과 제목 줄을 닫는다", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openTrip(page);
  const menu = await openTitle(page);
  const planUrl = (await menu.getByRole("link", { name: "여행계획서 열기" }).getAttribute("href"))!;
  await menu.getByRole("button", { name: "공유하기" }).click();
  const sheet = page.getByRole("dialog", { name: "여행계획서 공유" });
  await expect(sheet.getByRole("heading", { name: "여행계획서 공유" })).toBeFocused();
  await expect(sheet).toContainText("내 여행");
  await expect(sheet).toContainText(/10\.01 – 10\.02 · 2일 · \d+개 일정 · 여행계획서 링크/);
  await expect(sheet).toContainText("로그인 없이 열리는 내 여행 링크예요. 링크를 아는 사람은 누구나 볼 수 있어요.");
  const apps = sheet.getByRole("list", { name: "보낼 곳" }).getByRole("listitem");
  await expect(apps).toHaveText(["카카오톡", "디스코드", "메시지", "텔레그램", "링크 복사", "더보기"]);
  await expect(sheet.getByRole("link", { name: "텔레그램" })).toHaveAttribute("href", `https://t.me/share/url?url=${encodeURIComponent(planUrl)}&text=${encodeURIComponent("내 여행 — 여행계획서")}`);
  await expect(sheet.getByRole("link", { name: "메시지" })).toHaveAttribute("href", /^sms:\?&body=/);
  await expect(sheet.getByRole("link", { name: /파일로 내려받기/ })).toContainText("triPilot-내 여행.html");
  // 카카오톡은 앱 키가 없어 보내지 않는다 — 준비 중이라 말하고 창은 그대로
  await sheet.getByRole("button", { name: "카카오톡" }).click();
  await expect(page.getByRole("status").filter({ hasText: "카카오톡 공유는 준비 중이에요" })).toBeVisible();
  await expect(sheet).toBeVisible();
  await sheet.getByRole("button", { name: "링크 복사" }).click();
  await expect(page.getByRole("status").filter({ hasText: "링크를 복사했어요" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(planUrl);
  await expect(sheet).toHaveCount(0);
  await expect(page.getByRole("group", { name: "여행계획서" })).toHaveCount(0);
});

test("공유 창은 Esc 로 닫고 초점은 「공유하기」로 돌아온다", async ({ page }) => {
  await openTrip(page);
  const menu = await openTitle(page);
  await menu.getByRole("button", { name: "공유하기" }).click();
  await expect(page.getByRole("dialog", { name: "여행계획서 공유" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "여행계획서 공유" })).toHaveCount(0);
  await expect(menu.getByRole("button", { name: "공유하기" })).toBeFocused();
});

test("「상세 보기」는 그날 일정을 카드로 넘겨 보게 하고, 지도는 그날 일정만 경로선 없이 보이며, ← 로 돌아오면 그 일정이 목록에서 펼쳐진다", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "on" });
  await openTrip(page);
  await expect(page.locator("main path.trip-route-line")).not.toHaveCount(0);
  await page.locator("#stop-button-i-b").click();                                                        // 「상세 보기」는 펼친 카드에만 있다
  await page.getByRole("button", { name: "경복궁 관람 상세 보기" }).click();
  const detail = page.getByRole("heading", { name: "경복궁 관람 상세", level: 3 });
  await expect(detail).toBeFocused();
  await expect(page.getByRole("button", { name: "상세 보기 닫기" })).toBeVisible();                       // 머리줄은 ← 와 일정 이름
  await expect(page.locator("#trip-title-button")).toHaveCount(0);
  await expect(page.getByText("1일차 · 2/3", { exact: true })).toBeVisible();
  await expect(page.locator('[data-card-id="i-b"]')).toHaveAttribute("data-active", "true");
  await expect(page.locator('[data-card-id="i-b"]')).toContainText("고정한 일정");
  await expect(page.locator("main path.trip-route-line")).toHaveCount(0);                                 // 상세 보기에서는 경로선을 그리지 않는다
  await expect(page.getByRole("button", { name: "채팅 입력 열기" })).toHaveCount(0);                       // 채팅 막대도 없다
  await expect(page.getByText("등록된 사진이 없어요 · 장소 사진은 준비 중이에요")).toBeVisible();
  await page.getByRole("group", { name: "일정 고르기" }).getByRole("button", { name: "3. 점심 식당" }).click();
  await expect(page.getByRole("heading", { name: "점심 식당 상세", level: 3 })).toBeVisible();
  await expect(page.getByText("1일차 · 3/3", { exact: true })).toBeVisible();
  await expect(page.locator('[data-card-id="i-c"]')).toHaveAttribute("data-active", "true");
  await page.getByRole("button", { name: "상세 보기 닫기" }).click();
  await expect(page.locator("#stop-button-i-c")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#stop-button-i-c")).toBeFocused();
  await expect(page.locator("#trip-title-button")).toBeVisible();
});

test("상세 보기는 Esc 로도 닫히고, 「이 일정 질문하기」는 상세를 닫고 채팅으로 묻는다", async ({ page, request }) => {
  const server = mockServer(request);
  await openTrip(page);
  await page.locator("#stop-button-i-a").click();
  await page.getByRole("button", { name: "아침 식당 상세 보기" }).click();
  await expect(page.getByRole("heading", { name: "아침 식당 상세", level: 3 })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#stop-button-i-a")).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "아침 식당 상세 보기" }).click();
  await page.locator('[data-card-id="i-a"]').getByRole("button", { name: "이 일정 질문하기" }).click();
  await expect(page.locator("#trip-pane-chat")).not.toHaveAttribute("inert");                                   // `[ST4F-493]` 채팅 칸이 열린다(시트 머리의 채팅 단추는 없다)
  await expect.poll(async () => (await server.received("POST", "/messages")).length).toBe(1);
});

test("장소 출처는 상세 카드에서 이름 옆 꼬리표로 한 번만 말하고(「출처」 줄이 없다), 펼친 목록 카드에는 정보 줄이 없다(2026-10-13 카드 A안)", async ({ page }) => {
  // 실서버 확인(2026-10-08)에서 상세 카드에 「ⓒ한국관광공사」가 두 번 보였다 — mock 서버의 일정에는 출처가 없어 여기서 붙인다.
  await page.route(`**/v1/web/trips/${TRIP_ID}`, async (route) => {
    if (route.request().method() !== "GET") { await route.continue(); return; }
    const response = await route.fetch();
    const trip = await response.json();
    trip.items = (trip.items as { item_id: string; place_info?: Record<string, unknown> | null }[])
      .map((item) => item.item_id === "i-a" ? { ...item, place_info: { ...item.place_info, source_note: "ⓒ한국관광공사" } } : item);
    await route.fulfill({ response, json: trip });
  });
  await openTrip(page);
  await page.locator("#stop-button-i-a").click();
  await expect(page.locator("#stop-detail-i-a dl")).toHaveCount(0);                    // `[2026-10-13 카드 A안]` 펼친 카드에는 정보 줄이 없다(상세 보기 화면에 있다)
  await page.getByRole("button", { name: "아침 식당 상세 보기" }).click();
  const card = page.locator('[data-card-id="i-a"]');
  await expect(card.getByText("ⓒ한국관광공사", { exact: true })).toHaveCount(1);
  await expect(card.locator("dl")).not.toContainText("출처");
});

test("일정 카드는 접혀 있으면 「상세 보기」가 없고 ＋ 가 맨 오른쪽이며, 펼치면 「상세 보기」가 나오고 － 는 그대로 맨 오른쪽이다", async ({ page }) => {
  // `[2026-10-08 사용자 지시]` 접힌 카드에서 「상세 보기」를 빼고 ＋ 를 맨 오른쪽으로, 펼쳤을 때만 「상세 보기」.
  await openTrip(page);
  const card = page.locator("#trip-card-i-b");
  const mark = card.locator("[data-toggle-mark]");
  const detail = card.getByRole("button", { name: "경복궁 관람 상세 보기" });
  const gapRight = async () => { const [box, end] = [await card.boundingBox(), await mark.boundingBox()]; return Math.round(box!.x + box!.width - (end!.x + end!.width)); };
  await expect(detail).toHaveCount(0);
  await expect(mark).toHaveText("+");
  expect(await gapRight()).toBeLessThan(16);                                                            // 카드 오른쪽 끝에 붙어 있다
  await mark.click();                                                                                   // 오른쪽 끝의 ＋ 를 눌러도 펼친다
  await expect(page.locator("#stop-button-i-b")).toHaveAttribute("aria-expanded", "true");
  await expect(detail).toBeVisible();
  await expect(mark).toHaveText("−");
  expect(await gapRight()).toBeLessThan(16);
  const [button, end] = [await detail.boundingBox(), await mark.boundingBox()];
  expect(button!.y).toBeGreaterThanOrEqual(end!.y + end!.height - 1);                                    // `[ST4F-492]` 「상세 보기」는 머리 줄 아래 단추 줄(카드의 「이 일정 질문하기」가 빠져 혼자 한 줄) — － 는 머리 줄 맨 오른쪽 그대로
  await page.locator("#stop-button-i-b").click();                                                        // 이름을 눌러 접으면 다시 없다
  await expect(detail).toHaveCount(0);
});

test("상세 보기의 사진: 일정에 장소 ref(place_ref)가 오면 계획 확인 화면과 같은 호출로 등록된 사진을 보이고, 없으면 준비 중이라 말한다", async ({ page, request }) => {
  // `[2026-10-08]` 실서버의 여행 응답에는 아직 place_ref 가 없다(백엔드 요청) — 여기서 붙여 화면의 몫만 본다. 사진 호출은 mock 서버의 `GET /v1/web/places/photos`.
  const server = mockServer(request);
  await page.route(`**/v1/web/trips/${TRIP_ID}`, async (route) => {
    if (route.request().method() !== "GET") { await route.continue(); return; }
    const response = await route.fetch();
    const trip = await response.json();
    trip.items = (trip.items as { item_id: string }[]).map((item) => item.item_id === "i-b" ? { ...item, place_ref: "tour:126508" } : item);
    await route.fulfill({ response, json: trip });
  });
  await openTrip(page);
  await page.locator("#stop-button-i-b").click();
  await page.getByRole("button", { name: "경복궁 관람 상세 보기" }).click();
  const photos = page.getByRole("region", { name: /등록된 사진 · 경복궁 관람/ });
  await expect(photos.getByRole("img", { name: "경복궁_1 ⓒ한국관광공사" })).toBeVisible();
  await expect(photos).toContainText("1장");
  await expect(page.getByRole("button", { name: "시트를 위로 올리면 경복궁 관람 사진 1장 ↑" })).toBeVisible();
  expect((await server.received("GET", "/places/photos")).map((entry) => entry.query)).toEqual(["?ref=tour%3A126508"]);
  // ref 가 없는 일정(아침 식당)으로 넘기면 부르지 않고 준비 중이라 말한다
  await page.getByRole("group", { name: "일정 고르기" }).getByRole("button", { name: "1. 아침 식당" }).click();
  await expect(page.getByRole("region", { name: /등록된 사진 · 아침 식당/ })).toContainText("등록된 사진이 없어요 · 장소 사진은 준비 중이에요");
  expect(await server.received("GET", "/places/photos")).toHaveLength(1);
});
