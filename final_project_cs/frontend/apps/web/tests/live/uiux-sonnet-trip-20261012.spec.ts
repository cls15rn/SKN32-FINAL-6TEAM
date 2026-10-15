import { expect, test, type Page } from "@playwright/test";
import { beforeTheTrip, mockServer, openTitle, start, TRIP_ID, tripScreen, openPane } from "./helpers";

async function openTrip(page: Page) {
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(tripScreen(page)).toBeVisible();
}

// `[2026-10-13]` 가짜 서버 여행(2026-10-01)이 「지난 일정」으로 그려지지 않게 여행 전날로 시계를 고정한다(helpers.ts beforeTheTrip).
test.beforeEach(async ({ page }) => { await beforeTheTrip(page); });
test.beforeEach(async ({ page, request }) => {
  await mockServer(request).reset();
  await start(page);
  await page.route("**/v1/web/support/notices", route => route.fulfill({ json: { notices: [], maintenance: { enabled: false, message: "" } } }));
});

for (const width of [375, 1280]) test(`알림 센터 탭·재렌더 초점 유지와 Tab 순환 ${width}px`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 850 });
  await mockServer(request).scenario({ bell: "on" });
  await openTrip(page);
  await page.locator("#trip-bell").click();
  const center = page.getByRole("dialog", { name: "알림", exact: true });
  await expect(center.getByRole("heading", { name: "알림", exact: true })).toBeFocused();
  const history = center.getByRole("tab", { name: /변경 이력/ });
  await history.click();
  await expect(history).toBeFocused();
  await expect(history).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  const warnings = center.getByRole("tab", { name: /살펴볼 점/ });
  await expect(warnings).toBeFocused();
  await expect(warnings).toHaveAttribute("aria-selected", "true");
  await mockServer(request).ring(["itinerary"]);
  // Wait for the actual trip re-read, then confirm it did not refocus the heading.
  const readsBefore = (await mockServer(request).received("GET", `/trips/${TRIP_ID}`)).length;
  await mockServer(request).ring(["itinerary"]);
  await expect.poll(async () => (await mockServer(request).received("GET", `/trips/${TRIP_ID}`)).length).toBeGreaterThan(readsBefore);
  await expect(warnings).toBeFocused();
  expect(await page.getByRole("button", { name: "메뉴", exact: true, includeHidden: true }).evaluate(element => Boolean(element.closest("[inert]")))).toBe(true);
  for (let at = 0; at < 20; at += 1) {
    await page.keyboard.press("Tab");
    expect(await center.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  await center.getByRole("button", { name: "알림 센터 닫기", exact: true }).focus();
  await page.keyboard.press("Shift+Tab");
  expect(await center.evaluate(element => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(center).toHaveCount(0);
  await expect(page.locator("#trip-bell")).toBeFocused();
  expect(await page.getByRole("button", { name: "메뉴", exact: true }).evaluate(element => Boolean(element.closest("[inert]")))).toBe(false);
});

test("알림의 일정에서 보기는 채팅·상세에서 목록을 열고 해당 제목으로 키보드 초점을 옮긴다", async ({ page, request }) => {
  await mockServer(request).scenario({ proposals: "open" });
  await openTrip(page);
  await openPane(page, "채팅");
  const target = page.locator("#stop-button-i-c");
  async function goFromNotice() {
    await page.locator("#trip-bell").click();
    const center = page.getByRole("dialog", { name: "알림", exact: true });
    const go = center.getByRole("region", { name: /선택이 필요해요/ }).getByRole("button", { name: "일정에서 보기", exact: true });
    await go.focus(); await go.press("Enter");
    await expect(center).toHaveCount(0);
    await expect(target).toBeVisible();
    await expect(target).toHaveAttribute("aria-expanded", "true");
    await expect(target).toBeFocused();
    expect(await target.evaluate(element => Boolean(element.closest("[inert]")))).toBe(false);
    await expect(page.locator("#trip-pane-schedule")).not.toHaveAttribute("inert");                            // `[ST4F-493]` 일정 칸(시트 머리의 채팅 단추는 없다)
  }
  await goFromNotice();
  await page.getByRole("button", { name: "점심 식당 상세 보기", exact: true }).click();
  await expect(target).not.toBeVisible();
  await goFromNotice();
});

for (const empty of [false, true]) test(`등록 여행 ${empty ? "일정0개는 날짜줄 숨김" : "하루 일정도 날짜줄 표시"}`, async ({ page }) => {
  await page.route(`**/v1/web/trips/${TRIP_ID}`, async route => {
    const response = await route.fetch();
    const view = await response.json();
    const date = view.items[0]?.starts_at?.slice(0, 10);
    view.items = empty ? [] : view.items.filter((item: { starts_at: string }) => item.starts_at.slice(0, 10) === date);
    await route.fulfill({ response, json: view });
  });
  await openTrip(page);
  const strip = page.getByRole("tablist", { name: "일차 고르기", exact: true });
  if (empty) {
    await expect(strip).toHaveCount(0);
    await expect(page.getByText("등록한 일정이 없어요.", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "여행 정보를 불러오지 못했어요", exact: true })).toHaveCount(0);
  }
  else {
    await expect(strip).toBeVisible();
    await expect(strip.getByRole("tab", { name: /1일차/ })).toContainText(/\d{2}\.\d{2}/);
    await expect(strip.getByRole("tab")).toHaveCount(2);
  }
});

for (const key of ["Enter", "Escape"]) test(`제목 편집 ${key} 종료 뒤 제목 버튼으로 초점 복귀`, async ({ page }) => {
  await openTrip(page);
  await openTitle(page);
  await page.getByRole("button", { name: /^계획 이름 바꾸기/ }).click();
  const input = page.getByRole("textbox", { name: "계획 이름", exact: true });
  await input.fill("여행 제목 초점 시험");
  await input.press(key);
  await expect(input).toHaveCount(0);
  await expect(page.locator("#trip-title-button")).toBeFocused();
  await expect(page.getByRole("group", { name: "여행계획서", exact: true })).toHaveCount(0);
});

for (const key of ["Enter", "Space"]) test(`머리 영역 드래그 직후 첫 ${key}가 목록 높이를 바꾼다`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openTrip(page);
  const strip = page.getByRole("tablist", { name: "일차 고르기", exact: true });
  const point = await strip.evaluate(element => {
    const [first, second] = [...element.querySelectorAll("button")].map(button => button.getBoundingClientRect());
    const x = (first.right + second.left) / 2, y = (first.top + first.bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    return { x, y, hitsStripGap: Boolean(hit && element.contains(hit) && !hit.closest("button, a, input, select, textarea")) };
  });
  expect(point.hitsStripGap).toBe(true);
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await page.mouse.move(point.x, point.y - 80, { steps: 8 });
  await page.mouse.up();
  const checking = page.locator("[data-sheet]");
  await expect(checking).toHaveAttribute("data-sheet", "custom");
  const handle = page.getByRole("button", { name: "목록 높이 바꾸기", exact: true });
  await handle.focus();
  await handle.press(key);
  await expect(checking).toHaveAttribute("data-sheet", "half");
  await expect(handle).toBeFocused();
});

test("채팅 Enter 전송 동안 입력 초점·초안을 유지하고 중복 전송하지 않는다", async ({ page }) => {
  let release = () => {};
  const waiting = new Promise<void>(resolve => { release = resolve; });
  let sent = 0;
  await page.route(`**/v1/web/trips/${TRIP_ID}/messages`, async route => { sent += 1; await waiting; await route.continue(); });
  await openTrip(page);
  await openPane(page, "채팅");
  const input = page.getByRole("textbox", { name: "여행 메시지", exact: true });
  await input.fill("다음 여행 일정을 알려 주세요");
  await input.press("Enter");
  await expect(input).toHaveJSProperty("readOnly", true);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("다음 여행 일정을 알려 주세요");
  await expect.poll(() => sent).toBe(1);
  await input.press("Enter");
  expect(sent).toBe(1);
  release();
  await expect(input).toHaveJSProperty("readOnly", false);
  await expect(input).toHaveValue("");
  await expect(input).toBeFocused();
  expect(sent).toBe(1);
});

for (const failure of [false, true]) test(`문의 전송 ${failure ? "실패" : "성공"} 뒤 키보드 초점과 초안 처리`, async ({ page }) => {
  let release = () => {};
  const waiting = new Promise<void>(resolve => { release = resolve; });
  let sent = 0;
  const inquiry = { id: "focus-inquiry", title: "일정 문의", body: "여행 일정 확인을 도와 주세요.", language: "ko", receivedAt: "2026-10-12T00:00:00Z", status: "접수", replies: [] };
  await page.route("**/v1/web/support/inquiries", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { inquiries: [] } });
    sent += 1;
    await waiting;
    return failure ? route.fulfill({ status: 503, json: { error: { code: "busy", message: "잠시 뒤 다시 시도해 주세요." } } }) : route.fulfill({ json: { inquiry } });
  });
  await page.goto("/support/new");   // `[2026-10-12]` the form is its own screen; the list is `/support`
  const title = page.getByRole("textbox", { name: "제목", exact: true });
  const body = page.getByRole("textbox", { name: "문의 내용", exact: true });
  await title.fill(inquiry.title); await body.fill(inquiry.body);
  const send = page.getByRole("button", { name: "문의 보내기", exact: true });
  await send.focus(); await send.press("Enter");
  const pending = page.getByRole("button", { name: "접수 중…", exact: true });
  await expect(pending).toBeFocused();
  await expect(body).toHaveJSProperty("readOnly", true);
  await expect.poll(() => sent).toBe(1);
  await pending.press("Enter");
  expect(sent).toBe(1);
  release();
  if (failure) {
    await expect(body).toHaveJSProperty("readOnly", false);
    await expect(page.locator("#main-content").getByRole("alert")).toContainText("입력한 내용은 그대로예요");
    await expect(send).toBeFocused();
    await expect(body).toHaveValue(inquiry.body);
  } else {
    // `[2026-10-12]` A recorded inquiry goes back to the list, which says so once.
    await expect(page).toHaveURL(/\/support$/);
    await expect(page.locator("#main-content").getByRole("status")).toContainText("내용을 기록했어요");
  }
  expect(sent).toBe(1);
});
