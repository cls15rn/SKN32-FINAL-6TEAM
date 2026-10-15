import { expect, test, type Page } from "@playwright/test";
import { dayTab, mockServer, start, TRIP_ID, tripScreen } from "./helpers";

/**
 * `[2026-10-04]` The lines between stops on the trip's map (`GET /v1/web/trips/{id}/route-shapes`, mobility session). The free map (OpenStreetMap, drawn with
 * Leaflet) is the build these tests run on; the Naver and Google drawing is in `maps.spec.ts`. mock 서버 시험이다 — 화면이 무엇을 그리고 무엇을 말하는지를 본다.
 * ★What the screen must say: the data source (ODbL) whenever a line is drawn; a line made of a guess is dashed and pale; subway and bus lines are not real tracks.
 * `[2026-10-07 사용자 결정 — 목업 C안]` 지도는 시트 뒤에 늘 있고, 출처와 선의 뜻은 일정 목록 끝 출처 줄에, 선을 읽지 못한 말은 지도 아래 안내 칩에 있다.
 */
test.beforeEach(async ({ request }) => { await mockServer(request).reset(); });

const map = (page: Page) => page.locator("#main-content .leaflet-container");
const lines = (page: Page) => map(page).locator("path.trip-route-line");
const caption = (page: Page) => tripScreen(page);                    // 목록 끝 출처 줄(경로선 출처 · 선의 뜻)
/** The map's ＋ / －: the buttons show only while the pointer is on the map (`map-controls.spec.ts`), so the pointer goes there first. */
async function zoom(page: Page, name: "확대" | "축소") {
  await page.getByRole("region", { name: "여행 지도", exact: true }).hover({ position: { x: 150, y: 120 } });
  const group = page.getByRole("group", { name: "지도 단추" });
  const expand = group.getByRole("button", { name: "지도 단추 펼치기", exact: true });
  if (await expand.count()) await expand.click();
  await group.getByRole("button", { name, exact: true }).click();
}

async function openMap(page: Page) {
  await start(page);
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(tripScreen(page)).toBeVisible();
  await expect(map(page).locator(".leaflet-marker-icon").first()).toBeVisible();
}

test("선이 있는 여행: 길을 아는 구간은 실선, 직선으로 이은 구간은 점선·옅은 색으로 그리고, 출처와 점선·버스의 뜻을 목록 끝 출처 줄에 적는다", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "on" });
  await openMap(page);
  await expect(lines(page)).toHaveCount(2);
  // `[2026-10-15 ST4F-495]` the walk (a road the server knows) is a row of round dots in the walking colour; a guess stays the pale long dash
  const walk = map(page).locator("path.trip-route-line--walk");
  const dashed = map(page).locator("path.trip-route-line--dashed");
  await expect(walk).toHaveCount(1);
  await expect(dashed).toHaveCount(1);
  expect(await dashed.getAttribute("stroke-dasharray")).toBe("8 10");
  expect(await walk.getAttribute("stroke-dasharray")).toBe("0.1 10");
  expect(Number(await dashed.getAttribute("stroke-opacity"))).toBeLessThan(Number(await walk.getAttribute("stroke-opacity")));
  // the line goes through every point the server sent, in order: the first and the last are the two places around the move
  const route = await walk.getAttribute("d");
  expect(route?.match(/[ML]/g)?.length).toBe(3);

  await caption(page).locator("details[class*=credit] > summary").click();                   // `[ST4F-493]` 출처 · 선의 뜻은 접힌 줄 안
  const text = await caption(page).innerText();
  expect(text).toContain("경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)");   // the data source, as the server wrote it
  expect(text).toContain("점선은 길을 몰라 두 곳을 직선으로 이은 구간이에요.");
  expect(text).toContain("동그란 점은 걷는 구간이에요.");                              // `[ST4F-495]` a guess and a walk on the same map
  expect(text).toContain("버스는 정류장 정보가 없어 직선으로 이어요.");
  expect(text).toContain("아침 식당 → 경복궁: 정류장 정보가 없어 직선으로 이었어요");   // the server own reason for that guess (its `note`), as it wrote it
  expect(text).not.toContain("실제 선로 모양이 아니에요");                                // no subway line is on this map

  // the pins stay pressable and the lines do not cover them
  await expect(map(page).locator(".leaflet-marker-icon")).not.toHaveCount(0);
});

test("서버에 이 경로가 아직 없으면(404) 선도 출처 문구도 오류 문구도 없이 핀만 보인다", async ({ page }) => {
  await openMap(page);                                                 // default scenario: routeShapes "off" → FastAPI 404 {detail}
  await expect(lines(page)).toHaveCount(0);
  const text = await caption(page).innerText();
  expect(text).not.toContain("ODbL");
  await expect(page.locator("#main-content")).not.toContainText("경로선을 불러오지 못했어요");
  await expect(map(page).locator(".leaflet-marker-icon").first()).toBeVisible();
});

test("선을 읽다 실패하면(500) 읽지 못했다고 말하고 핀은 그대로 보인다 — 선이 없다는 말이 아니다", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "fail" });
  await openMap(page);
  await expect(page.locator("#main-content").getByRole("status").filter({ hasText: "경로선을 불러오지 못했어요" })).toBeVisible();   // 지도 아래 안내 칩
  await expect(lines(page)).toHaveCount(0);
  await expect(map(page).locator(".leaflet-marker-icon").first()).toBeVisible();
});

test("서버가 선을 늦게 줘도(길 자료를 올리는 중) 핀이 먼저 보이고, 선은 오면 그려진다", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "slow" });
  await openMap(page);
  await expect(lines(page)).toHaveCount(0);                            // not there yet — but the map is already usable
  await expect(map(page).locator(".leaflet-marker-icon").first()).toBeVisible();
  await expect(lines(page)).toHaveCount(2, { timeout: 15_000 });
});

test("다른 날로 바꾸면 그 날 장소 사이의 선만 남는다(둘째 날에는 선이 없다)", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "on" });
  await openMap(page);
  await expect(lines(page)).toHaveCount(2);
  await dayTab(page, /^2일차/).click();
  await expect(lines(page)).toHaveCount(0);
  await expect(caption(page)).not.toContainText("ODbL");               // no line, no data-source line
});

test("확대하면 상세 경로선을 따로 한 번 받아 그린다: 축소 상태는 짧은 선, 확대 수준 15 이상에서 상세 선으로 바뀌고, 다시 줄여도 상세 선이 남는다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ routeShapes: "on" });
  await openMap(page);
  await caption(page).locator("details[class*=credit] > summary").click();                   // `[ST4F-493]` 출처 · 「(상세 선)」은 접힌 줄 안
  // ★점 개수는 지도가 그리는 길이 아니라 서버가 준 점으로 가린다: 지도는 화면 밖 구간을 자르고 한 점에 가까운 점은 줄여 그린다. 그래서 서버가 쓴 설명(상세 선이면 「(상세 선)」)으로 본다.
  const used = async () => ((await caption(page).innerText()).includes("(상세 선)") ? "detail" : "short");
  const asked = async () => (await server.received("GET", "/route-shapes")).map((entry) => entry.query);
  expect(await asked()).toEqual([null]);                                                  // 처음에는 짧은 선만(축소용)
  expect(await used()).toBe("short");
  for (let at = 0; at < 6 && (await asked()).length < 2; at += 1) {                      // 확대 수준 15에 닿을 때까지 한 단계씩
    await zoom(page, "확대");
    await page.waitForTimeout(450);
  }
  await expect.poll(asked).toEqual([null, "?detail=true"]);                                // 상세 선은 한 번만 따로 받는다
  await expect.poll(used).toBe("detail");                                                  // 받은 상세 선으로 바뀌어 그려진다(웹은 점을 줄이지 않는다)
  for (let at = 0; at < 3; at += 1) { await zoom(page, "축소"); await page.waitForTimeout(450); }
  expect(await used()).toBe("detail");                                                     // 줄여도 상세 선은 그대로(어느 확대에서도 맞고, 바뀌면 선이 튄다)
  expect(await asked()).toEqual([null, "?detail=true"]);
});
