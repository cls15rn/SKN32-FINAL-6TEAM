import { expect, test } from "@playwright/test";
import { agree, mockServer, registerStubTrip, start, TRIP_ID, useKorean } from "./helpers";

test.beforeEach(async ({ page, request }) => { await mockServer(request).reset(); await useKorean(page); await agree(page); });

test("메뉴는 프로필 칸·여행 목록 보기·여행 취향 설문·문의함·설정 순서이고, Tab 순환과 Esc·포커스 복귀를 지키며, 링크는 메뉴를 닫는다", async ({ page, request }) => {
  // `[2026-10-13 회의 확정 — 메뉴 = 마이페이지]` 한 판: 프로필 칸(이미지 · 이름 · 로그인 칩 · 단추 하나 — 게스트는 「로그인 · 가입」) 다음 네 줄.
  // 앞의 세 줄은 전과 같은 이름이고(`[2026-10-13 사용자 지시]`) 줄마다 보조 문구가 있다. 언어 · 테마 · 일정 목록 크기 · 애니메이션은 설정 화면으로 옮겼다.
  await mockServer(request).scenario({ social: "off" });
  await page.goto("/trips/new");
  const open = page.getByRole("button", { name: "메뉴", exact: true });
  await open.click();
  const menu = page.getByRole("dialog", { name: "메뉴" });
  const signIn = menu.getByRole("link", { name: "로그인 · 가입", exact: true });
  const trips = menu.getByRole("link", { name: /^여행 목록 보기/ });
  const survey = menu.getByRole("button", { name: /^여행 취향 설문/ });
  const support = menu.getByRole("link", { name: /^문의함/ });
  const settings = menu.getByRole("link", { name: /^설정/ });
  const close = menu.getByRole("button", { name: "메뉴 닫기" });
  const tops = await Promise.all([signIn, trips, survey, support, settings].map(async (item) => (await item.boundingBox())!.y));
  expect([...tops].sort((a, b) => a - b)).toEqual(tops);
  // One board holds exactly the profile cell and the four rows; the app settings are not in the menu any more.
  expect(await trips.evaluate((link) => {
    const board = link.parentElement!;
    return board.children.length === 5 && Boolean(board.querySelector('a[href="/trips"]')) && Boolean(board.querySelector('a[href="/support"]')) && Boolean(board.querySelector('a[href="/mypage"]'))
      && !board.querySelector('[role="switch"]') && !board.textContent!.includes("LANGUAGE");
  })).toBe(true);
  await expect(menu).toContainText("게스트");
  await expect(trips).toContainText("아직 등록한 여행이 없어요");                  // 줄 아래 보조 문구(이 브라우저의 여행 개수 — 세션이 없는 첫 방문)
  await expect(support).toContainText("보낸 문의와 답변");

  await expect(signIn).toBeFocused();
  for (const next of [trips, survey, support, settings, close, signIn]) {
    await page.keyboard.press("Tab");
    await expect(next).toBeFocused();
  }
  await page.keyboard.press("Shift+Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(settings).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(open).toBeFocused();

  await open.click();
  await expect(signIn).toBeFocused();   // 메뉴가 열려 초점을 받은 뒤에 Tab 을 누른다(위와 같다)
  await page.keyboard.press("Tab");
  await expect(trips).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/trips$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await open.click();
  await trips.click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/trips$/);
  await open.click();
  await settings.click();
  await expect(page).toHaveURL(/\/mypage$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "설정", exact: true })).toBeVisible();
});

test("설정 화면의 언어 카드를 펼쳐 고르면 바로 적용·접히고, 첫 화면과 메뉴에는 언어 카드가 없다(2026-10-13 — 언어는 설정 화면에만)", async ({ page }) => {
  await page.goto("/");
  const home = page.locator("main").getByRole("button", { name: /LANGUAGE/ });
  await expect(home).toHaveCount(0);
  await page.getByRole("button", { name: "메뉴", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "메뉴" }).getByRole("button", { name: /LANGUAGE/ })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await page.goto("/mypage");
  const toggle = page.getByRole("button", { name: /LANGUAGE/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toContainText("한국어");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "한국어", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "English", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "English", exact: true }).click();

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await expect(toggle).toContainText("English");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await page.goto("/");
  await expect(page.locator("#intro-title")).toContainText("From plans to memories");

  // Back to Korean on the settings screen — the only place the card lives.
  await page.goto("/mypage");
  await toggle.click();
  await page.getByRole("button", { name: "한국어", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "ko");
  await expect(toggle).toContainText("한국어");
});

test("플로팅 버튼 스위치는 없다 — 예전에 켜 둔 설정이 남아 있어도 여행 화면은 시트의 일차 칩과 아래 채팅 버튼이다", async ({ page }) => {
  // `[2026-10-07 사용자 결정 — 목업 C안]` 여행 화면이 늘 지도 + 시트라 플로팅 버튼 · 고정 하단 탭을 고르는 스위치를 메뉴에서 뺐다.
  await page.addInitScript(() => localStorage.setItem("tripilot.web.settings.v1", JSON.stringify({ language: "ko", navigation: "floating" })));
  await registerStubTrip(page);
  await expect(page.locator("#trip-pane-schedule")).not.toHaveAttribute("inert");                              // `[ST4F-493]` 시트 머리의 채팅 단추는 없다
  await expect(page.getByRole("button", { name: "채팅 입력 열기" })).toBeVisible();
  await expect(page.getByRole("button", { name: "일정 · 메뉴 열기 또는 닫기" })).toHaveCount(0);
  await page.goto("/mypage");                                                      // `[2026-10-13]` 앱 설정은 설정 화면에 있다
  await expect(page.getByRole("switch", { name: "애니메이션 건너뛰기" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "플로팅 버튼 사용" })).toHaveCount(0);
});

test("테마 카드에서 화이트를 고르면 모든 화면에 바로 적용되고, 새로고침 뒤에는 첫 화면을 그리기 전에 적용된다", async ({ page }) => {
  // When the saved theme lands on <html>: before <body> exists means before the first paint (no green flash).
  await page.addInitScript(() => {
    new MutationObserver(() => {
      if (document.documentElement?.dataset.theme === "neutral" && !("themeBeforeBody" in window)) Object.assign(window, { themeBeforeBody: document.body === null });
    }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-theme"] });
  });
  await page.goto("/trips");
  const html = page.locator("html");
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await expect(html).toHaveAttribute("data-theme", "green");
  expect(await background()).toBe("rgb(243, 245, 243)");

  await page.goto("/mypage");                                                      // `[2026-10-13]` 테마 카드는 설정 화면에 있다(전에는 메뉴)
  await expect(html).toHaveAttribute("data-theme", "green");
  const card = page.getByRole("group", { name: /THEME/ });
  const green = card.getByRole("button", { name: "그린", exact: true });
  const white = card.getByRole("button", { name: "화이트", exact: true });
  await expect(green).toHaveAttribute("aria-pressed", "true");
  await white.click();
  await expect(white).toHaveAttribute("aria-pressed", "true");
  await expect(green).toHaveAttribute("aria-pressed", "false");
  await expect(card).toContainText("화이트");
  await expect(html).toHaveAttribute("data-theme", "neutral");
  expect(await background()).toBe("rgb(242, 242, 239)");
  // Each sample keeps its own theme's colours, whichever is chosen; the chosen option uses the selection colour.
  const sample = (option: typeof green) => option.locator("[data-theme] > span").evaluate((dot) => getComputedStyle(dot).backgroundColor);
  expect([await sample(green), await sample(white)]).toEqual(["rgb(46, 96, 71)", "rgb(28, 28, 30)"]);
  expect(await white.evaluate((option) => getComputedStyle(option).borderColor)).toBe("rgb(36, 87, 214)");

  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "neutral");
  expect(await page.evaluate(() => (window as unknown as { themeBeforeBody?: boolean }).themeBeforeBody)).toBe(true);
  expect(await background()).toBe("rgb(242, 242, 239)");

  // Another screen follows the same choice; choosing green again returns every colour.
  await page.goto("/start");
  await expect(html).toHaveAttribute("data-theme", "neutral");
  await page.goto("/mypage");
  await page.getByRole("group", { name: /THEME/ }).getByRole("button", { name: "그린", exact: true }).click();
  await expect(html).toHaveAttribute("data-theme", "green");
  await page.goto("/start");
  await expect(html).toHaveAttribute("data-theme", "green");
  expect(await background()).toBe("rgb(243, 245, 243)");
});

test("「애니메이션 건너뛰기」 스위치는 기본 꺼짐이고, 켜면 이 브라우저가 기억해 새로고침 뒤에도 켜져 있다(시스템의 「동작 줄이기」와 별개)", async ({ page, request }) => {
  await mockServer(request).scenario({ social: "off" });
  await page.goto("/mypage");                                                      // `[2026-10-13]` 설정 화면에 있다(전에는 메뉴)
  const skip = page.getByRole("switch", { name: "애니메이션 건너뛰기" });
  await expect(skip).not.toBeChecked();
  await skip.click();
  await expect(skip).toBeChecked();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tripilot.web.settings.v1") ?? "{}").skipAnimation)).toBe(true);
  await page.reload();
  await expect(skip).toBeChecked();
  await skip.click();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tripilot.web.settings.v1") ?? "{}").skipAnimation)).toBe(false);
});



test("등록 여행 메뉴의 모든 항목에 Tab으로 닿고 항로 지킴이와 닫기 뒤 처음으로 돌아온다", async ({ page, request }) => {
  await mockServer(request).scenario({ social: "off", guardian: "on" });
  await start(page);
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(page.getByRole("button", { name: "채팅 입력 열기" })).toBeVisible();
  const open = page.getByRole("button", { name: "메뉴", exact: true });
  await open.click();
  const menu = page.getByRole("dialog", { name: "메뉴" });
  const first = menu.getByRole("link", { name: "로그인 · 가입", exact: true });   // `[2026-10-13]` 게스트 프로필 칸의 단추
  const keeper = menu.getByRole("button", { name: "항로 지킴이 끄기" });
  const close = menu.getByRole("button", { name: "메뉴 닫기" });
  await expect(first).toBeFocused();
  await expect(menu.getByRole("switch", { name: "플로팅 버튼 사용" })).toHaveCount(0);
  await expect(menu.getByRole("switch", { name: "데스크탑 화면으로 보기" })).toHaveCount(0);
  for (const next of [
    menu.getByRole("link", { name: /^여행 목록 보기/ }),
    menu.getByRole("button", { name: /^여행 취향 설문/ }),
    menu.getByRole("link", { name: /^문의함/ }),
    menu.getByRole("link", { name: /^설정/ }),
    keeper, close, first,                                                       // 마지막 줄 다음은 머리(항로 지킴이 · 닫기), 그다음 다시 처음
  ]) { await page.keyboard.press("Tab"); await expect(next).toBeFocused(); }
  await page.keyboard.press("Shift+Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab"); await expect(keeper).toBeFocused();
  await page.keyboard.press("Escape"); await expect(open).toBeFocused();
});
