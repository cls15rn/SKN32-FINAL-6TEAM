import { expect, test } from "@playwright/test";
import { beforeTheTrip, alsoAgree, APP, start, mockServer, openNotices, openTitle, TRIP_ID, tripScreen, openPane } from "./helpers";

// `[2026-10-13]` 가짜 서버 여행(2026-10-01)이 「지난 일정」으로 그려지지 않게 여행 전날로 시계를 고정한다(helpers.ts beforeTheTrip).
test.beforeEach(async ({ page }) => { await beforeTheTrip(page); });
test.beforeEach(async ({ page, request }) => {
  await mockServer(request).reset();
  await start(page);
});

const openTrip = async (page: import("@playwright/test").Page) => {
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(tripScreen(page)).toBeVisible();
};

test("예약·고정 표시가 없는 식당도 제공된 식당 뱃지를 표시한다", async ({ page }) => {
  await page.route(url => url.pathname === `/v1/web/trips/${TRIP_ID}`, async route => {
    const response = await route.fetch();
    const body = await response.json();
    Object.assign(body.items.find((item: { item_id: string }) => item.item_id === "i-a"), {
      booked: false, customer_pinned: false, paused: false,
      badges: [{ code: "nopo", label: "노포", source: "시험용 여행 자료" }],
    });
    await route.fulfill({ response, json: body });
  });
  await openTrip(page);
  const card = page.locator("#stop-button-i-a");
  await expect(card.getByText("노포", { exact: true })).toBeVisible();
  await expect(card.getByText("예약", { exact: true })).toHaveCount(0);
  await expect(card.getByText("고정한 일정", { exact: true })).toHaveCount(0);
});

test("여행 화면(지도 + 시트)은 서버가 준 일정·고정·다른 안을 하루씩 보이고, 이동 항목은 일정 사이의 이동 줄로, 알림·이력·경고·계획서 링크는 종 아래에 그대로 보인다", async ({ page, request }) => {
  await mockServer(request).scenario({ routeShapes: "on" });                                     // 수단 · 거리는 서버의 경로선에서 읽는다
  await openTrip(page);
  await expect(page.getByRole("heading", { level: 1, name: "내 여행" })).toBeVisible();          // 머리줄 제목은 서버가 준 여행 이름
  await expect(page.getByRole("tablist", { name: "일차 고르기" }).getByRole("tab")).toHaveText([/전체/, /1일차/, /2일차/]);
  const schedule = tripScreen(page);
  await expect(schedule.getByText("아침 식당", { exact: true })).toBeVisible();                   // (목록 끝 출처 줄의 「아침 식당 → 경복궁: …」 말고 카드 제목)
  await expect(schedule.getByText("둘째 날 박물관")).toHaveCount(0);                          // 하루씩 — 다음 날은 칩이나 옆으로 밀어서

  // 이동 항목(i-m)은 일정이 아니라 두 일정 사이의 이동 줄: 출발 시각 · 수단 · 걸리는 시간 · 거리 · 여유(서버의 경로선과 시각에서)
  const move = schedule.locator('[data-entry-id="i-m"]');
  await expect(move).toContainText("11:10");
  await expect(move).toContainText("도보 20분 · 1.3km");
  await expect(move).toContainText("여유 30분");
  await move.getByRole("button").click();
  await expect(move).toContainText("경복궁 관람 → 점심 식당");                                    // 서버의 문장 그대로
  await expect(move).toContainText("11:10 출발 → 11:30 도착");
  // 이동 항목이 없는 구간(아침 식당 → 경복궁)은 서버가 그린 선(버스 · 직선)과, 서버가 준 길찾기 링크
  const leg = schedule.locator('[data-entry-id="i-a>i-b"]');
  await expect(leg).toContainText("버스 · 640m");
  await leg.getByRole("button").click();
  await expect(leg).toContainText("길을 몰라 두 곳을 직선으로 이은 구간이에요.");
  // `[2026-10-14 ST4F-492]` 「지도 앱에서 길찾기」 is a button that opens the sheet of three map apps; its Google link is the server's directions address
  await expect(schedule.getByRole("button", { name: "지도 앱에서 길찾기" })).toHaveCount(1);
  await schedule.getByRole("button", { name: "지도 앱에서 길찾기" }).click();
  const directions = page.getByRole("dialog", { name: "지도 앱에서 길찾기" });
  await expect(directions.locator('[data-map-app="google"]')).toHaveAttribute("href", "https://www.google.com/maps/dir/?api=1&origin=a&destination=b&travelmode=transit");
  await expect(directions.locator('[data-map-app="naver"]')).toHaveAttribute("href", /^https:\/\/map\.naver\.com\/p\/directions\/.+\/-\/transit$/);
  await expect(directions.locator('[data-map-app="kakao"]')).toHaveAttribute("href", /^https:\/\/map\.kakao\.com\/link\/by\/traffic\//);
  await directions.getByRole("button", { name: "닫기" }).click();
  await expect(directions).toHaveCount(0);

  // 고정한 일정 표시와 서버가 남겨 둔 다른 안
  await expect(page.locator("#stop-button-i-b").getByText("고정한 일정")).toBeVisible();
  // `[2026-10-13 카드 A안]` 접힌 카드 아래 줄은 분류 · 방문일 영업시간 · 주소이고(서버가 안 보냈으면 「… 정보 없음」), 펼치면 단추만 — 다른 안 · 장소 사실은 「상세 보기」 화면에 있다.
  await expect(page.locator("#stop-button-i-b")).toContainText("주소 정보 없음");
  await page.locator("#stop-button-i-b").click();
  await expect(page.locator("#stop-detail-i-b")).not.toContainText("다른 안");
  await expect(page.locator("#stop-detail-i-b").getByRole("button", { name: "이 일정 질문하기" })).toHaveCount(0);  // `[ST4F-492]` 카드에는 없다(상세 보기에만)
  await expect(page.locator("#stop-detail-i-b").getByRole("button", { name: "지도 앱으로 열기" })).toHaveCount(0);  // 지도 링크가 없으면 단추도 없다
  await page.getByRole("button", { name: "경복궁 관람 상세 보기" }).click();
  await expect(page.locator('[data-card-id="i-b"]')).toContainText("다른 안");
  await expect(page.locator('[data-card-id="i-b"]')).toContainText("창덕궁");
  await page.keyboard.press("Escape");

  // 서버가 보낸 장소 사실(요식 원장) — 접힌 카드에 분류 · 주소, 상세 보기 화면에 전화 · 영업시간 · 미쉐린 · 편의
  for (const text of ["한식", "서울특별시 종로구 율곡로1길 7"]) await expect(page.locator("#stop-button-i-a")).toContainText(text);
  await page.locator("#stop-button-i-a").click();
  const detail = page.locator("#stop-detail-i-a");
  // 지도 앱으로 열기 — 서버가 준 구글 지도 링크를 새 창으로(서버가 링크를 안 준 일정에는 단추가 없다)
  // `[ST4F-492]` the card opens the sheet of three map apps; the detail (below) has the three links in a row
  await expect(detail.getByRole("button", { name: "지도 앱으로 열기" })).toHaveCount(1);
  await page.getByRole("button", { name: "아침 식당 상세 보기" }).click();
  const facts = page.locator('[data-card-id="i-a"]');
  for (const text of ["서울특별시 종로구 율곡로1길 7", "02-000-0000", "한식", "월 08:00–21:00", "셀렉티드 (2026)", "카드 결제"]) await expect(facts).toContainText(text);
  const mapApps = facts.getByRole("group", { name: "지도 앱으로 열기" });
  await expect(mapApps.locator('[data-map-app="google"]')).toHaveAttribute("href", "https://www.google.com/maps/search/?api=1&query=%EC%95%84%EC%B9%A8");
  await expect(mapApps.locator('[data-map-app="google"]')).toHaveAttribute("target", "_blank");
  await expect(mapApps.locator('[data-map-app="naver"]')).toHaveAttribute("href", /^https:\/\/map\.naver\.com\/p\/search\//);
  await expect(facts).not.toContainText("michelin_selected");
  await page.keyboard.press("Escape");

  // 종 — 알림 센터: 할 일 · 탭 셋(서버가 찾은 것 · 보낸 것 · 바꾼 것)
  const notices = await openNotices(page);
  const tabs = notices.getByRole("tablist", { name: "알림 종류" }).getByRole("tab");
  await expect(tabs).toHaveText([/^살펴볼 점 \d+$/, "받은 알림 2", /^변경 이력 \d+$/]);
  const list = notices.getByRole("tabpanel");
  await expect(notices.getByRole("tab", { name: /받은 알림/ })).toHaveAttribute("aria-selected", "true");   // 안 읽은 알림이 있으면 받은 알림부터
  await expect(list).toContainText("오늘 첫 일정은 08:00 아침 식당이에요.");
  await expect(list.getByRole("img", { name: "새 알림" })).toHaveCount(2);
  await notices.getByRole("tab", { name: /살펴볼 점/ }).click();
  await expect(list).toContainText("하루가 빡빡해요");
  await expect(list).toContainText("일정을 줄이세요");
  await notices.getByRole("tab", { name: /변경 이력/ }).click();
  await expect(list).toContainText("처음 등록");
  // 여행계획서 — 제목을 펼친 줄(목업 C안 B)
  await notices.getByRole("button", { name: "알림 센터 닫기" }).click();
  const plan = (await openTitle(page)).getByRole("link", { name: "여행계획서 열기" });
  await expect(plan).toHaveAttribute("href", new RegExp(`/plan/${TRIP_ID}\\?t=`));
  await expect(plan).toHaveAttribute("target", "_blank");
  // 데모 잔재가 없다
  await expect(page.getByRole("link", { name: "일정 수정" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "검증 결과 다시 보기" })).toHaveCount(0);
});

test("서버가 경로선을 주지 않으면 이동 줄은 수단·거리를 지어내지 않는다 — 출발 시각 · 걸리는 시간 · 여유만, 이동 항목이 없는 구간은 정보 없음과 길찾기 링크", async ({ page }) => {
  await openTrip(page);                                                                          // 기본 장면: route-shapes 404
  const schedule = tripScreen(page);
  const move = schedule.locator('[data-entry-id="i-m"]');
  await expect(move).toContainText("이동 20분");
  await expect(move).toContainText("여유 30분");
  await expect(move).not.toContainText("도보");
  await expect(move).not.toContainText("km");
  const unknownLeg = schedule.locator('[data-entry-id="i-a>i-b"]');
  await expect(unknownLeg).toContainText("이동 정보가 아직 없어요");
  await unknownLeg.getByRole("button", { name: "지도 앱에서 길찾기" }).click();                                   // `[ST4F-492]` 단추 → 지도 앱 시트
  await expect(page.getByRole("dialog", { name: "지도 앱에서 길찾기" }).locator('[data-map-app="google"]')).toHaveAttribute("href", "https://www.google.com/maps/dir/?api=1&origin=a&destination=b&travelmode=transit");
  await page.keyboard.press("Escape");
  await expect(unknownLeg).not.toContainText(/\d+분|km/);
  await expect(schedule.locator('[data-entry-id="i-a>i-b"]')).not.toContainText("버스");
});

test("경고와 알림이 없으면 탭은 0 과 「없어요」만 말하고 줄을 지어내지 않으며, 종에는 수가 없다", async ({ page, request }) => {
  await mockServer(request).scenario({ warnings: "none", notices: "none" });
  await openTrip(page);
  await expect(page.getByRole("button", { name: "알림 센터 열기", exact: true })).toBeVisible();     // 읽지 않은 알림이 없다
  const notices = await openNotices(page);
  await expect(notices.getByRole("tab", { name: /살펴볼 점/ })).toHaveText("살펴볼 점 0");
  await expect(notices.getByRole("tab", { name: /받은 알림/ })).toHaveText("받은 알림 0");
  await expect(notices.getByRole("tabpanel")).toHaveText("살펴볼 점이 없어요.");
  await expect(notices.getByRole("tabpanel").getByRole("listitem")).toHaveCount(0);
  await expect(notices.getByText("지금 처리할 일은 없어요.")).toBeVisible();
});

test("서버가 기다리는 선택이 있으면 「선택이 필요해요」가 뜨고, 고르면 그 안의 key 가 서버로 가고 칸이 사라진다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "open" });
  await openTrip(page);
  await openNotices(page);
  const ask = page.getByRole("region", { name: /선택이 필요해요/ });
  await expect(ask).toBeVisible();
  await expect(ask).toContainText("점심 식당이 문을 닫았어요. 대체 식당을 골라 주세요.");   // 서버 통지 문장 그대로
  await expect(ask).toContainText("12:00 · 점심 식당");
  await ask.getByRole("button", { name: /대체 식당 A/ }).click();

  await expect.poll(async () => (await server.received("POST", "/choose")).length).toBe(1);
  const [choose] = await server.received("POST", "/choose");
  expect(choose.body).toEqual({ key: "o-1" });
  await expect(page.getByRole("region", { name: /선택이 필요해요/ })).toHaveCount(0);
  await expect(page.getByText("고르신 곳으로 일정을 바꿨어요.")).toBeVisible();          // 칸이 사라져도 결과는 말한다
});

test("실내·야외를 모르는 일정의 「바꿀까요?」: 「바꿔 줘」가 key change 를 보내고, 서버가 채운 다른 곳을 다시 읽어 보인다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "consent" });
  await openTrip(page);
  await openNotices(page);
  const ask = page.getByRole("region", { name: /선택이 필요해요/ });
  await expect(ask.getByRole("button", { name: "바꿔 줘 — 다른 곳 보기" })).toBeVisible();
  await ask.getByRole("button", { name: "바꿔 줘 — 다른 곳 보기" }).click();
  await expect.poll(async () => (await server.received("POST", "/choose")).length).toBe(1);
  expect((await server.received("POST", "/choose"))[0].body).toEqual({ key: "change" });
  await expect(ask.getByRole("button", { name: /실내 박물관/ })).toBeVisible();              // 같은 제안을 다시 읽었다
  await expect(ask.getByRole("button", { name: "바꿔 줘 — 다른 곳 보기" })).toHaveCount(0);  // 선택지가 생기면 물음은 사라진다
  await expect(page.getByText("다른 곳을 찾았어요 — 위에서 골라 주세요.")).toBeVisible();
});

test("예보 주의 알림은 외부 발송을 건너뛰어도 변경 버튼을 보이고 누르면 대체안을 요청한다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "forecast" });
  await openTrip(page);
  await openNotices(page);
  const ask = page.getByRole("region", { name: /선택이 필요해요/ });
  await expect(ask).toContainText("예보 주의사항이 있어요(강수확률 80% · 풍속 35km/h)");
  await expect(ask).toContainText("답이 없으면 원래 일정을 그대로 둡니다.");
  await ask.getByRole("button", { name: "예보에 맞게 일정 변경", exact: true }).click();
  await expect.poll(async () => (await server.received("POST", "/choose")).length).toBe(1);
  expect((await server.received("POST", "/choose"))[0].body).toEqual({ key: "change" });
  await expect(ask.getByRole("button", { name: /실내 박물관/ })).toBeVisible();
  await expect(ask.getByRole("button", { name: "예보에 맞게 일정 변경", exact: true })).toHaveCount(0);
  await ask.getByRole("button", { name: /실내 박물관/ }).click();
  await expect(page.getByText("고르신 곳으로 일정을 바꿨어요.")).toBeVisible();
});

test("「바꿔 줘」에 다른 곳이 없으면 원래 일정을 그대로 둔다고 알린다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "consent", choose: "no_alternate" });
  await openTrip(page);
  await openNotices(page);
  await page.getByRole("button", { name: "바꿔 줘 — 다른 곳 보기" }).click();
  await expect(page.getByText("바꿀 수 있는 다른 곳을 찾지 못했어요 — 원래 일정을 그대로 둡니다.")).toBeVisible();
});

test("자동으로 바꾼 일정은 「되돌리기」로 서버가 준 값 그대로 되돌리고, 서버의 답을 보인다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ undo: "open" });
  await openTrip(page);
  await openNotices(page);
  const card = page.getByRole("region", { name: "자동으로 바꾼 일정" });
  await expect(card).toContainText("비 소식이 있어 09:30 경복궁 관람을 실내 박물관으로 바꿨어요.");
  await card.getByRole("button", { name: "되돌리기" }).click();
  await expect.poll(async () => (await server.received("POST", "/rollback")).length).toBe(1);
  expect((await server.received("POST", "/rollback"))[0].body).toEqual({ request_id: "rollback:v1->v0", base_version: 1, to_version: 0 });
  await expect(page.getByText("09:30 일정을 원래대로(경복궁 관람) 되돌렸어요.")).toBeVisible();
  await expect(page.getByRole("region", { name: "자동으로 바꾼 일정" })).toHaveCount(0);   // 되돌린 뒤엔 칸이 없다
});

test("그 사이 일정이 또 바뀌어 되돌리기가 거절되면(409) 아무것도 안 바뀌었다고 알린다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ undo: "stale" });
  await openTrip(page);
  await openNotices(page);
  await page.getByRole("region", { name: "자동으로 바꾼 일정" }).getByRole("button", { name: "되돌리기" }).click();
  await expect(page.getByText("그 사이 일정이 다시 바뀌어 되돌리지 않았어요. 지금 상태를 다시 불러왔어요.")).toBeVisible();
});

test("「지금 일정 그대로 두기」는 key 를 null 로 보낸다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "open" });
  await openTrip(page);
  await openNotices(page);
  await page.getByRole("button", { name: "지금 일정 그대로 두기" }).click();
  await expect.poll(async () => (await server.received("POST", "/choose")).length).toBe(1);
  expect((await server.received("POST", "/choose"))[0].body).toEqual({ key: null });
});

test("이미 정해진 선택(409)은 「아무것도 바뀌지 않았다」고 알리고 서버 상태를 다시 읽는다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ proposals: "open", choose: "conflict" });
  await openTrip(page);
  await openNotices(page);
  await page.getByRole("button", { name: /대체 식당 B/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "이미 정해졌거나" })).toContainText("아무것도 바뀌지 않았고");
  // 알림 뒤에 화면이 서버를 다시 읽는다
  await expect.poll(async () => (await server.received("GET", "/proposals")).length).toBeGreaterThan(1);
});

test("지도는 서버 좌표가 있는 그날의 일정만 실제 지도(OpenStreetMap)에 핀으로 그린다", async ({ page }) => {
  await openTrip(page);
  const map = page.locator("main .leaflet-container");
  // 번호는 그날 일정의 순서이고, 핀의 title 에는 날짜와 시각이 붙는다(접근성 이름은 핀 안의 번호라 title 의 앞부분으로 찾는다). 이동 항목(i-m)은 일정이 아니라 번호를 차지하지 않는다.
  await expect(map.locator('.leaflet-marker-icon[title^="1. 아침 식당"]')).toBeVisible();
  await expect(map.locator('.leaflet-marker-icon[title^="2. 경복궁 관람"]')).toBeVisible();
  await expect(map.locator('.leaflet-marker-icon[title^="3. 점심 식당"]')).toBeVisible();
  await expect(map.locator('.leaflet-marker-icon[title*="둘째 날 박물관"]')).toHaveCount(0);       // 다음 날 일정은 그날 지도에 없다
  await expect(map.getByRole("link", { name: "OpenStreetMap" })).toBeVisible();            // 출처 표시는 늘 보인다
  await expect(map.getByText(/개념도|실제 위치·거리·이동 경로를 표시하지 않습니다/)).toHaveCount(0);
});

test("채팅: 빠른 질문 세 개와 일정 상세가 화면 문장 그대로 서버로 가고, 서버 답이 그대로 보인다", async ({ page, request }) => {
  const server = mockServer(request);
  await openTrip(page);
  await openPane(page, "채팅");
  const chat = page.locator("#trip-pane-chat");
  const last = () => chat.locator("article[data-role=assistant]").last();

  await chat.getByRole("button", { name: "하루 요약" }).click();
  await expect(last()).toContainText("서버 답: 2026-10-01 하루 일정을 요약해 주세요.");
  await chat.getByRole("button", { name: "예약 표시" }).click();
  await expect(last()).toContainText("서버 답: 2026-10-01 예약 표시를 알려 주세요.");

  // 일정을 고른 뒤: 「선택 일정」(상세)·「다음 일정」
  await openPane(page, "일정");
  await page.locator("#stop-button-i-b").click();
  await openPane(page, "채팅");
  await chat.getByRole("button", { name: "선택 일정" }).click();
  await expect(last()).toContainText("서버 답: 2026-10-01 09:30 경복궁 관람 일정의 상세를 알려 주세요.");
  await chat.getByRole("button", { name: "다음 일정" }).click();
  await expect(last()).toContainText("서버 답: 2026-10-01 09:30 경복궁 관람 다음 일정을 알려 주세요.");

  const sent = (await server.received("POST", "/messages")).map((entry) => entry.body?.message);
  expect(sent).toEqual([
    "2026-10-01 하루 일정을 요약해 주세요.", "2026-10-01 예약 표시를 알려 주세요.",
    "2026-10-01 09:30 경복궁 관람 일정의 상세를 알려 주세요.", "2026-10-01 09:30 경복궁 관람 다음 일정을 알려 주세요.",
  ]);
  // 요청마다 다른 request_id — 서버가 「같은 요청」으로 착각하지 않는다
  const ids = (await server.received("POST", "/messages")).map((entry) => entry.body?.request_id);
  expect(new Set(ids).size).toBe(4);
  // ★서버가 대화를 기록하고 화면이 그 기록을 읽어도 답은 한 번씩만 보인다(기록 시각이 받은 시각보다 이르다) —
  //   2026-09-29 실서버에서 모든 답이 두 번 보였다. 새로고침 뒤에도 같다.
  const answers = async () => chat.locator("article[data-role=assistant] p:nth-child(2)").allInnerTexts();
  expect((await answers()).filter((text) => text.startsWith("서버 답:"))).toHaveLength(4);
  await page.reload();
  await openPane(page, "채팅");
  await expect.poll(async () => (await answers()).filter((text) => text.startsWith("서버 답:")).length).toBe(4);
});

test("직접 쓴 질문도 보내고, 옛 서버처럼 answer 없이 escalated 만 오면 화면이 「담당자에게 넘겼어요」를 지어내지 않는다", async ({ page, request }) => {
  await mockServer(request).scenario({ chat: "escalated_bare" });
  await openTrip(page);
  await openPane(page, "채팅");
  const chat = page.locator("#trip-pane-chat");
  await page.getByPlaceholder("일정에 대해 궁금한 점을 입력하세요").fill("뭐라고요?");             // 입력은 화면 아래 채팅 막대
  await page.getByRole("button", { name: "메시지 전송" }).click();
  const reply = chat.locator("article[data-role=assistant]").last();
  await expect(reply).toContainText("escalated");
  await expect(reply).not.toContainText("담당자");
});

test("옛 검증 결과·진행 주소는 그 여행 화면으로 보낸다 — 따로 보는 검증 단계가 없으니 지어낸 0 대신 실제 여행 화면이 열린다", async ({ page }) => {
  await start(page);
  for (const path of ["results", "verification"]) {
    await page.goto(`/trips/${TRIP_ID}/${path}`);
    await expect(page).toHaveURL(new RegExp(`/trips/${TRIP_ID}$`));
    await expect(tripScreen(page)).toBeVisible();
    await expect(page.getByText("조정한 일정")).toHaveCount(0);
  }
});

test("여행을 다시 읽다 실패하면(연결·서버 오류) 보이던 일정과 채팅 초안은 그대로 두고 「마지막으로 확인한 내용」이라 알리며, 다시 불러오면 알림이 사라진다", async ({ page }) => {
  await openTrip(page);
  await openPane(page, "채팅");
  await page.locator("#trip-chat-message").fill("초안으로 남겨 둘 질문");                              // 보내지 않은 초안
  let failing = true;
  await page.route((url) => url.pathname.endsWith(`/v1/web/trips/${TRIP_ID}`), async (route) => {
    if (route.request().method() === "GET" && failing) await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "서버가 잠깐 불안정해요" } }) });
    else await route.continue();
  });
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));                  // 탭이 앞으로 돌아온 것과 같다: 여행을 다시 읽는다
  const banner = page.getByRole("alert").filter({ hasText: "최신 여행 정보를 불러오지 못했어요" });
  await expect(banner).toBeAttached();
  await expect(page.locator("#trip-chat-message")).toHaveValue("초안으로 남겨 둘 질문");             // 쓰던 초안도 그대로
  failing = false;
  await openPane(page, "일정");                                                                // 알림은 일정 칸 맨 위에 있다
  await banner.getByRole("button", { name: "다시 불러오기" }).click();
  await expect(banner).toHaveCount(0);
  await expect(page.locator("#trip-pane-schedule").getByText("경복궁 관람")).toBeVisible();          // 보이던 일정은 그대로
});

test("여행이 사라졌거나(404) 세션이 끝났으면 다시 읽을 때 옛 일정을 그리지 않고 오류 화면을 보인다", async ({ page }) => {
  await openTrip(page);
  await page.route((url) => url.pathname.endsWith(`/v1/web/trips/${TRIP_ID}`), async (route) => {
    if (route.request().method() === "GET") await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: "그 여행을 찾을 수 없어요" } }) });
    else await route.continue();
  });
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.locator("#trip-pane-schedule").getByText("경복궁 관람")).toHaveCount(0);        // 지워진 여행(남의 계정의 옛 여행)이 캐시로 남지 않는다
  await expect(page.getByText("그 여행을 찾을 수 없어요")).toBeVisible();
});

test("선택·알림을 읽지 못해도(서버 500) 여행 화면은 그대로 뜨고, 읽지 못했다고 알린다", async ({ page, request }) => {
  await mockServer(request).scenario({ fail: "proposals" });
  await openTrip(page);
  await expect(tripScreen(page).getByText("경복궁 관람")).toBeVisible();
  const notices = await openNotices(page);
  await expect(notices.getByRole("alert").filter({ hasText: "선택과 알림을 읽지 못했어요" })).toBeVisible();
  await expect(notices.getByRole("tab", { name: /살펴볼 점/ })).toBeVisible();     // 다른 칸은 영향받지 않는다
});

test("없는 여행 주소는 빈 화면이 아니라 이유와 다시 시도 단추를 보인다", async ({ page, request }) => {
  await mockServer(request).scenario({ trip: "missing" });
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(page.getByRole("button", { name: "다시 불러오기" })).toBeVisible();
  await expect(page.locator("main")).not.toBeEmpty();
});

test("서버에 연결이 안 되면 그 사실을 말하고, 화면은 비지 않는다", async ({ page }) => {
  await page.route("**/v1/web/**", (route) => route.abort());
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(page.getByText("서버에 연결하지 못했어요")).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 불러오기" })).toBeVisible();
});

test("주요 화면을 여는 동안 브라우저 콘솔에 오류가 하나도 없다(없는 리소스·예외)", async ({ page, request }) => {
  const server = mockServer(request);
  // bell "on": the server this screen is built for has the change bell. An older server answers its address with a 404,
  // which the browser logs by itself — that one is expected there (the screen falls back to re-reading every 30 s).
  // The same for the route lines (`routeShapes: "on"`): a server without `route-shapes` answers 404 and the browser logs it — the screen then draws pins only.
  // And for the consent record (`consents: "on"`): an older server without it answers 404 and the browser logs that (the screen then keeps the browser's own copy).
  await server.scenario({ proposals: "open", bell: "on", routeShapes: "on", consents: "on", retention: "on" });
  const problems: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") problems.push(`console: ${message.text()}`); });
  page.on("pageerror", (error) => problems.push(`exception: ${error.message}`));
  page.on("response", (response) => { if (response.status() >= 400) problems.push(`${response.status()} ${response.request().method()} ${response.url()}`); });
  page.on("requestfailed", (request) => { if (request.failure()?.errorText !== "net::ERR_ABORTED") problems.push(`request failed: ${request.url()} ${request.failure()?.errorText}`); });

  for (const path of ["/", "/start", "/trips/new", `/trips/${TRIP_ID}`, `/trips/${TRIP_ID}/results`]) {
    // ★not "networkidle": the trip screen keeps the change bell open, so the network never goes idle there
    await page.goto(path, { waitUntil: "load" });
    await page.waitForTimeout(1_500);
  }
  expect(problems).toEqual([]);
});

test("여행 화면을 열면 채팅 모델 예열을 서버에 한 번 청하고(사용자 키로), 키가 없는 첫 화면에서는 청하지 않는다", async ({ page, request }) => {
  const server = mockServer(request);
  await start(page);
  await page.goto(`/trips/${TRIP_ID}`);
  await expect(tripScreen(page)).toBeVisible();
  await expect.poll(async () => (await server.received("POST", "/v1/web/warmup")).length).toBe(1);
  expect((await server.received("POST", "/v1/web/warmup"))[0]).toMatchObject({ session: "known-session", csrf: "csrf-known", key: null });

  await server.reset();
  const fresh = await page.context().browser()!.newContext();
  const other = await fresh.newPage();
  await other.goto(`${APP}/`);
  await other.waitForTimeout(1500);
  expect(await server.received("POST", "/v1/web/warmup")).toHaveLength(0);
  await fresh.close();
});

// 2026-09-30: the server's "this trip changed" bell. The screen re-reads what changed at once, instead of every 30 s.
test("서버가 「이 여행 바뀜」 신호를 보내면 새로고침 없이 새 선택이 바로 뜬다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ bell: "on" });
  await openTrip(page);
  await openNotices(page);
  await expect(page.getByRole("region", { name: /선택이 필요해요/ })).toHaveCount(0);
  await server.scenario({ proposals: "open" });
  // the stream opens a moment after the screen: ring until it is there
  await expect.poll(() => server.ring(["proposal"])).toBeGreaterThan(0);
  await expect(page.getByRole("region", { name: /선택이 필요해요/ })).toContainText("대체 식당 A");
});

test("신호 연결이 끊겼다 다시 붙으면 그 사이 바뀐 것을 전부 다시 읽는다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ bell: "on" });
  await openTrip(page);
  await openNotices(page);
  await expect.poll(() => server.ring(["notice"])).toBeGreaterThan(0);
  // changed while the line is down — no bell reaches the screen for this one
  await server.scenario({ proposals: "open" });
  expect(await server.hangup()).toBeGreaterThan(0);
  await expect.poll(async () => (await server.received("GET", "/events")).length).toBeGreaterThan(1);
  await expect(page.getByRole("region", { name: /선택이 필요해요/ })).toContainText("대체 식당 A", { timeout: 10_000 });
  expect((await server.received("GET", "/events")).length).toBeGreaterThan(1);
});

test("신호가 없는 옛 서버면 다시 붙으려고 조르지 않고 30초 다시 읽기로 돌아간다", async ({ page, request }) => {
  const server = mockServer(request);
  await openTrip(page);
  await expect.poll(async () => (await server.received("GET", "/events")).length).toBe(1);
  await page.waitForTimeout(3_000);
  expect((await server.received("GET", "/events")).length).toBe(1);
});

// 2026-09-30 (teammate question Q-05): the server answered, only re-reading the plan failed.
test("채팅: 서버가 답한 뒤 일정 다시 읽기만 실패하면 답은 보이고 「못 보냈다」고 하지 않으며, 잠시 뒤 다시 읽는다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ rereadFails: 1 });
  await openTrip(page);
  await openPane(page, "채팅");
  const chat = page.locator("#trip-pane-chat");
  await chat.getByRole("button", { name: "하루 요약" }).click();
  await expect(chat.locator("article[data-role=assistant]").last()).toContainText("서버 답: 2026-10-01 하루 일정을 요약해 주세요.");
  await expect(chat.getByRole("status").filter({ hasText: "답은 받았어요" })).toBeVisible();
  await expect(chat.getByRole("alert").filter({ hasText: "메시지를 보내지 못했어요" })).toHaveCount(0);
  // the plan loads again by itself, and the note goes away
  await expect(chat.getByRole("status").filter({ hasText: "답은 받았어요" })).toHaveCount(0);
  await expect(tripScreen(page)).toBeVisible();
  expect((await server.received("POST", "/messages")).length).toBe(1);
});

test("채팅: 보내기가 실패해 「다시 보내기」를 누르면 같은 요청 번호로 가서 서버가 두 번 처리하지 않는다", async ({ page, request }) => {
  const server = mockServer(request);
  await server.scenario({ fail: "messages" });
  await openTrip(page);
  await openPane(page, "채팅");
  const chat = page.locator("#trip-pane-chat");
  await chat.getByRole("button", { name: "하루 요약" }).click();
  const failed = chat.getByRole("alert").filter({ hasText: "메시지를 보내지 못했어요" });
  await expect(failed).toBeVisible();
  await server.scenario({ fail: "" });
  await failed.getByRole("button", { name: "다시 보내기" }).click();
  await expect(chat.locator("article[data-role=assistant]").last()).toContainText("서버 답: 2026-10-01 하루 일정을 요약해 주세요.");
  const ids = (await server.received("POST", "/messages")).map((entry) => entry.body?.request_id);
  expect(ids).toHaveLength(2);
  expect(ids[1]).toBe(ids[0]);
});

// 2026-09-30 user decision: where the customer is comes from the browser's Geolocation API, asked only on a press.
test("채팅: 서버가 현재 위치가 필요하다고 하면 버튼을 누를 때만(그리고 위치 동의가 있을 때만) 브라우저 위치를 얻어 같은 질문을 다시 보낸다", async ({ page, request, context }) => {
  const server = mockServer(request);
  await alsoAgree(page, { location: true });                                                     // ★`[2026-10-05]` 위치로 묻는 것은 선택 동의(`location`)를 한 사람만 - 동의 없는 쪽은 consent.spec
  await context.grantPermissions(["geolocation"], { origin: APP });
  await context.setGeolocation({ latitude: 37.5704, longitude: 126.9921, accuracy: 25 });
  await openTrip(page);
  await openPane(page, "채팅");
  const chat = page.locator("#trip-pane-chat");
  await page.locator("#trip-chat-message").fill("여기서 경복궁 어떻게 가?");
  await page.locator("#trip-chat-message").press("Enter");
  await expect(chat.locator("article[data-role=assistant]").last()).toContainText("현재 위치를 알려 주시면");
  // nothing about the position has left the browser yet
  expect((await server.received("POST", "/messages"))[0].body).not.toHaveProperty("location");

  await chat.getByRole("button", { name: "내 위치 알려 주고 다시 묻기" }).click();
  await expect(chat.locator("article[data-role=assistant]").last()).toContainText("지금 계신 곳(37.5704, 126.9921)에서 도보 12분이에요.");
  const [first, second] = await server.received("POST", "/messages");
  expect(second.body).toMatchObject({ message: "여기서 경복궁 어떻게 가?", location: { lat: 37.5704, lng: 126.9921, accuracy_m: 25 } });
  expect(second.body?.request_id).not.toBe(first.body?.request_id);
  await expect(chat.getByRole("button", { name: "내 위치 알려 주고 다시 묻기" })).toHaveCount(0);
});

test("「공유하기」의 「파일로 내려받기」는 계획서 링크와 같은 주소에 download=1 이 붙고, 서버는 그것을 파일(attachment)로 내려 준다", async ({ page, request }) => {
  await start(page);
  await openTrip(page);
  const menu = await openTitle(page);
  const open = menu.getByRole("link", { name: "여행계획서 열기" });
  await menu.getByRole("button", { name: "공유하기" }).click();
  const download = page.getByRole("dialog", { name: "여행계획서 공유" }).getByRole("link", { name: /파일로 내려받기/ });
  await expect(open).toBeVisible();
  const openHref = new URL((await open.getAttribute("href"))!);
  const downloadHref = new URL((await download.getAttribute("href"))!);
  expect(downloadHref.origin + downloadHref.pathname).toBe(openHref.origin + openHref.pathname);   // 같은 계획서
  expect(downloadHref.searchParams.get("t")).toBe(openHref.searchParams.get("t"));                  // 같은 토큰(로그인 없이 열린다)
  expect(downloadHref.searchParams.get("download")).toBe("1");
  expect(openHref.searchParams.get("download")).toBeNull();                                         // 여는 링크는 그대로 — 파일로 받지 않는다
  const answer = await request.get(downloadHref.toString());
  expect(answer.headers()["content-disposition"]).toMatch(/^attachment; filename\*=UTF-8''triPilot-/);
  expect((await request.get(openHref.toString())).headers()["content-disposition"]).toBeUndefined();
});

