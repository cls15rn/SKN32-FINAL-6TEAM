---
type: contract
title: 항로 지킴이·위험·안전·지도
description: 호출 주체와 책임별 REST 요청·응답 계약
status: draft
domain: travel
---

# 항로 지킴이·위험·안전·지도

### 항로 지킴이 — `POST /v1/web/trips/{trip_id}/guardian` · 여행 조회의 `guardian` · 알림 `[결정 2026-10-06 사용자]`

`[실측]` 구현 `app/domains/travel_ops/components/planning/guardian.py` · `entry/trip_api.py` · 기록 `trip_guardian_changes`(마이그레이션 050) · 시험 `tests/e2e/test_trip_guardian.py` 25건. 기획 `wiki/records/plans/2026-10-05_설문_계획서에서_읽고_로딩에서_묻기_기획.md` 「★★ 결정 2026-10-06」.
**항로 지킴이** = 일정이 꼬이면 서버가 **알아서 대체안을 적용**하고 알리는 모드(되돌릴 수 있다). 새 판정 값이 아니라 설문 15번 `on_disruption` 을 이름 붙은 모드로 바꾼 것이다 — **켜짐 = `replace` · 꺼짐 = `ask_first`**, 판정(`pending.decide` · `survey.auto_on_disruption`)은 그대로 이 값을 읽는다.

**`POST /v1/web/trips/{trip_id}/guardian`** `{enabled: true|false, via: "card"|"header"|"notice"|"settings"}` → `{enabled, since, via}`
- ★**쿠키 세션으로만** 부른다 — 사용자 키 · 에이전트 키는 **403 `cookie_session_required`**, 인증이 없으면 401, CSRF 가 틀리면 403 `csrf_failed`. 계획서 링크 토큰으로는 열리지 않는다(자동 변경은 일정을 실제로 바꾸는 권한이라서). 게스트 세션도 쿠키 세션이라 부를 수 있다(게스트 여행은 감시 대상이 아니라 켜도 아무 일이 없다).
- 켜기와 끄기는 **같은 입구**다. 둘 다 `constraints.survey.on_disruption` 을 **명시**(`replace` · `ask_first`)해 쓰고 `survey_answered` 에 넣는다 — 「직접 고른 것」이다(2026-09-29 규칙). ★건너뛰기(끄고 진행)는 **값을 안 보내는 것이 아니라 `ask_first` 를 보낸다** — 미응답은 기본 `replace` 로 읽혀 휴무 · 교통 통제가 자동 적용된다.
- 본인 여행이 아니거나 없으면 404(같은 응답). `enabled` 는 JSON 참 · 거짓만(글자 「yes」는 422). **이미 그 상태로 답해 둔 여행에 같은 값을 보내면 아무것도 바꾸지 않고** 현재 값을 돌려준다(「언제부터」가 밀리지 않는다).
- 누가 · 언제 · 어디서(`via`)가 **추가만 하는 표**에 남는다(`trip_guardian_changes` — 고치기는 트리거가 막고, 여행을 지우면 같이 지워진다).

**여행 조회의 `guardian`**(`GET /v1/web/trips/{id}` · `/v1/trips/{id}` · 등록 응답) `{enabled, since, via}` — 상단 아이콘이 읽는다. `since`·`via` 는 **마지막으로 바뀐** 시각 · 곳. 기록은 없는데 등록 때 카드가 이미 답했으면 `since` = 여행 만든 시각 · `via: "registration"`(조회에서만 나오는 값). **한 번도 답하지 않은 여행**(옛 여행 · 에이전트가 만든 여행)은 `{enabled: false, since: null, via: null}` — 꺼짐이고, 문제가 생기면 지금 규칙대로 움직인다(바뀐 동작 없음).

**알림 `payload.guardian`** — 알림 문장은 건드리지 않고 **구조값**을 싣는다(시나리오용 여행 버전 `TripStore.enqueue_*` 와 Case 버전 `itinerary_actions` 둘 다 같은 규칙 `guardian.annotate`).
- 우리가 **자동으로 바꾼** 알림(`change_notice` + `rollback`)이고 **켜져 있으면** `{changed: true}` — 화면은 「항로 지킴이가 바꿨어요 · 되돌리기」. ★켜 두지 않은 여행의 자동 변경(옛 여행의 기본 `replace`)에는 싣지 않는다 — 항로 지킴이가 한 일이 아니다. 고객이 고른 변경에도 없다.
- 「다른 안」을 보내며 먼저 묻는 알림(`proposal_request` · 이유 `ask_first`)이고 **꺼져 있으면** `{offer: {label: "항로 지킴이 켜기", via: "notice", path: "/trips/{id}?guardian=on", url}}`. `url` = `web_origin` + `path`(웹 주소 미설정이면 null). ★**링크만으로는 켜지지 않는다** — 웹이 확인 패널을 띄운 뒤 위 `POST …/guardian`(`via: "notice"`)을 부른다. 고정한 일정(`protected`) · 안전 알림 · 「바꿀까요?」(실내외 모름) · 대안이 없는 알림에는 싣지 않는다 — 켜도 그 경우는 먼저 묻거나 달라지지 않는다.
- 디스코드 · 텔레그램 글(`notify/discord.py` `_compose`)에는 계획서 링크 **앞**에 줄이 붙는다 — 「항로 지킴이가 바꿨어요. 마음에 안 들면 웹에서 되돌릴 수 있어요.」 · 「항로 지킴이를 켜면 다음부터 알아서 바꿔 드려요: {url}」(`url` 이 있을 때만). 이 값이 없는 알림의 문구는 한 글자도 안 바뀐다.

`[결정 2026-10-06 사용자]` **재난 · 지진은 항로 지킴이가 켜져 있든 아니든 「다른 곳으로 바꾸기」가 아니라 「일정 정지 + 대피 안내」로 다룬다** — 아래 「재난 시 일정 정지」. 정지 대상이 아닌 일반 사건(휴무 · 통제 · 날씨)은 기존 규칙(`pending.decide`) 그대로이고, 고정한 일정(`protected`)은 켜져도 먼저 묻는다.
(전에는 카드 · 기획 문서가 「켜 두어도 재난 · 지진은 먼저 물어요」라고 적었지만 코드는 바로 바꿨다 — 2026-09-24 결정. 둘 다 이번 결정으로 **대체**됐다. 카드 문구는 「켜 두어도 고정한 일정은 먼저 물어봐요 · 재난 · 지진이 나면 일정을 멈추고 안전 안내를 보내요」로 맞춘다 — 웹 몫.)

### 일정 위험 점검 · 이동 판정 — `GET /v1/web/trips/{trip_id}/risks` · `POST /v1/web/moves/judge` `[2026-10-06 사용자 요청 — MCP 읽기 도구]`

`[실측]` 구현 `components/watch/risk_report.py`(보고) · `components/itinerary/move_judge.py`(판정) · `ports/data_sources/{ratelimit,source_budget,base}.py`(캐시만 읽기 · 낮은 우선순위 몫) · `entry/trip_api.py` · MCP 도구 `tripilot_check_trip_risks` · `tripilot_judge_move`([mcp-tools.md](../mcp-tools.md)) · 시험 `tests/e2e/test_mcp_read_tools.py` 17건 · `tests/unit/travel/{test_risk_report,test_move_judge,test_cache_only_sources}.py` 37건 · `tests/integration/db/test_source_budget_db.py` 몫 6건(합 60). 둘 다 **읽기 전용**(아무것도 쓰지 않는다)이고 사용자 키 · 쿠키, **본인 여행만**(남의 것 · 없는 것 · 없는 항목은 같은 404, 키 없음 401).

**`GET /v1/web/trips/{trip_id}/risks`** `?item_id=&within_hours=&fresh=&at=` → `{trip_id, version, at, mode: "cached"|"fresh", scope: "upcoming"|"item", horizon_hours, items[], skipped[], summary{problem, clear, unknown, checked}, notes[], categories[]}`
- `items[]` = `{item_id, seq, kind, title, starts_at, ends_at, place, status: "problem"|"clear"|"unknown", problems[], cautions[], unknown_categories[], categories[], checked_at, oldest_confirmed_at}`. `problems[]` = 점검 6종이 가리킨 사건(`category` · `label` · `kind` · `text` · `source` · 발표/확인 시각 · `summary`). `categories[]` = 6종마다 `{category, label, status: ok|not_applicable|unknown, source, confirmed_at, reason}`. `unknown_categories[]` = `{category, label, code: not_cached|source_failed|not_connected|missing|partial|too_early, reason}`(`missing` = 점검 응답에 그 종류가 없다 — 빠진 종류는 항상 확인 불가로 채운다 · `partial` = 소스 일부만 답했다(교통 ITS · UTIC 중 한쪽) · `too_early` = 시작까지 `snapshot_hours`(3) 넘게 남아 지금 상태만 확인됨 · `not_cached` 는 캐시에 없는지 소스가 실패했는지 가르지 못한다). `reason` 은 **고정 문장**이다(점검기의 사유는 환경변수 · 파일 이름이 들어 있어 내보내지 않는다). 점검은 `max(항목 시작, at)` 으로 한다(이미 시작한 항목은 지금 기준). 동시 처리 상한을 넘으면 503 `busy`(`Retry-After: 5`) · `at` 이 2000~2100년 밖이면 422. 장소(좌표)를 모르는 항목은 `status=unknown` · `code=no_place`.
- ★**`clear` 는 6종 모두 확인했을 때만**이다 — 하나라도 못 확인했으면 `unknown`(문제를 못 찾았어도). 문제가 하나라도 찾아졌으면 `problem`(못 확인한 종류는 `unknown_categories` 에 따로). 「해당 없음」은 확인 불가가 아니다.
- **기본(`fresh=false`)은 공유 응답 캐시만 읽는다** — 바깥에 새로 묻지 않고 하루 한도(D-017 · `source_budget`)를 안 쓰며 횟수도 안 센다. 캐시에 없는 종류는 `unknown`(`not_cached`). `fresh=true` 는 캐시에 없는 것을 새로 부르되 **낮은 우선순위**(소스의 하루 · 이번 달 한도의 `fresh_share` 까지만 쓴다 — 같은 SQL 안에서 비교 · DB 를 못 읽으면 안 부른다) · 한 번에 `fresh_max_items` 개까지 · `risk_check` 로 센다(제한이 켜져 있으면 429 `usage_limit` · 503 `service_daily_cap`). 캐시 읽기도 `risk_read` 로 센다(서버 조회 부하).
- `within_hours`(최대 48 · 기본 `horizon_hours`) · `at`(기준 시각 — 시험 · 재생용, 없으면 지금) · `item_id`(그 항목만, 시간 범위 무시). 점검기가 조립 안 된 서버는 503 `risk_check_unavailable`.

**`POST /v1/web/moves/judge`** `{origin, destination, depart_at?, arrive_by?, trip_id?}` — `origin` · `destination` = `{name?, lat, lon}` 또는 `{item_id}`(+ 위 `trip_id`, 본인 여행만). 시각은 둘 중 하나(둘 다면 422 `invalid_request`, 없으면 지금 출발, 시간대 없으면 서울 시각). 좌표 · 항목을 못 읽으면 422 `invalid_place` · `trip_id_required`.
- → `{status: "judged"|"no_route"|"unavailable"|"out_of_scope", grade: "timetable"|"estimate"|"none", grade_label, grade_note?, mode, from, to, depart_at, arrive_at, requested_depart_at, wait_min, eta_min, route{id, label, uses, walk_m, fare_krw, grade}, alternatives[](../각각 grade), left_out[], estimate{straight_line_m, walk_minutes_estimate, note}, reason{code, reason, earliest?}, searched?, basis{mode, timetable_built_at, rules_version, timetable_stale}, checked_at}` (상태마다 있는 칸만).
- `judged` = 판정기가 경로를 냈다 — `grade` 는 **고른 경로**로 가른다: 열차(버스가 없다)면 `timetable`(시간표 판정 · ★실시간 운행 확정이 아니다), 도보 · 택시 · 버스가 낀 경로면 `estimate`(열차 시간표가 아니라 거리 · 도로 길찾기 · 버스 배차 추정 — `grade_note`). `status=undetermined` = 판정기가 판단하지 못했다(데이터 없음 · 확인 못 함 · 택시 길찾기 불통 — **갈 방법이 없다는 뜻이 아니다**). `depart_at` 결과의 `earliest_not_guaranteed` = 첫 결과로 대신했다(가장 이른 출발이라는 보장 없음). 출발 시각을 주면 도착 목표를 출발 +90 → +180 → +360분으로 넓혀 찾는다(못 찾으면 `no_route` + `searched`). 판정기가 예외를 던지면 `unavailable`(`engine_error`). `unavailable` = 판정기가 꺼져 있거나 못 쓴다 — **직선 거리 어림만**(경로 · 시각을 지어내지 않는다). `no_route` = 판정기가 「갈 방법이 없다」고 했다 — 이유 그대로, 어림값을 덧붙이지 않는다. `out_of_scope` = 서울 밖(`grade=none`).
- 이 서버의 시간표 · 도로 그래프와 자체 길찾기 서버(GraphHopper)만 쓴다 — **바깥 유료 소스를 부르지 않는다**(코드 확인). 횟수는 `move_judge` 로 센다(제한이 켜져 있을 때만 막는다).

### 재난 시 일정 정지 — `POST /v1/web/trips/{trip_id}/safety/resume` · 여행 조회의 `safety` · 안전 알림 `[결정 2026-10-06 사용자]`

`[실측]` 구현 `components/planning/safety.py`(분류) · `components/watch/safety_pause.py`(정지 · 알림 · 해제) · `components/places/shelters.py`(대피 장소) · `scripts/load_safety_shelters.py`(자료 적재) · 저장 `trip_safety_pauses` · `safety_shelters`(마이그레이션 052) · 설정 `travel.safety`. 설계 · 조사 · 한계 `wiki/records/plans/2026-10-06_재난_일정정지와_피난안내_설계.md`.

**규칙** — 재난이 난 시각에 그 지역에 여행객이 있었다면(오늘이 여행 기간 + 오늘 일정에 적힌 장소에서 사건이 점검됨) **그날(KST) 남은 일정을 정지**한다(★`[결정 2026-10-06 사용자]` **자정에 풀리지 않는다** — 사용자가 다시 시작해야 풀린다. `safety.until` 은 그날 정지도 `null` 이다. 서버는 사용자가 다치셨는지 · 안전한 곳에 있는지 모르고, 저절로 재개해 틀리는 쪽이 멈춰 있어 틀리는 쪽보다 대가가 크다). ★`[결정 2026-10-06]` **아직 시작하지 않은 여행도 심각한 사건이면 여행 전체를 정지한다**(사용자가 다시 시작할 때까지 — 가기 전에 현지 상황을 모른다. 「그날 정지」 단계 사건은 시작 전 여행을 멈추지 않는다 · 끝난 여행은 점검하지 않는다). 전쟁 · 공습 · 활화산 폭발 같은 심각한 사건은 **여행 전체**를 정지한다(사용자가 다시 시작할 때까지). 정지는 **일정 항목을 고치거나 지우지 않는다** — 표시(`trip_safety_pauses`)만 쓰고, 감시 · 안내 반복이 그 여행을 건너뛴다. 날씨 재해 · 안전안내 단계 · 교통통제 · 실종 · 훈련 · 해제 문자는 정지하지 않는다.

**여행 조회의 `safety`**(`GET /v1/web/trips/{id}` · `/v1/trips/{id}`) — 정지가 없으면 `{paused: false}`, 있으면 `{paused: true, level: "day"|"trip", label, since, until, day, released, phase: "in_progress"|"upcoming", resume: {label: "일정 다시 시작", path: "/safety/resume"}}`(`phase=upcoming` = 시작 전 여행이라 멈춤). 여럿이면 여행 전체가 앞선다. `items[].paused` — 사건 시각 이후에 끝나는 항목(그날 정지는 그날 항목만)이 `true`.

**안전 알림** — `type: "safety_alert"` · `kind: "safety_pause_day"|"safety_pause_trip"` · `reason: "safety_pause"` · `guidance`(웹 알림 목록 `GET …/notices` 의 `safety` 칸). 글은 안전을 앞세운다: ①「⚠️ 안전 알림 — {재난}. 오늘 남은 일정을 정지했어요」 ②공식 안내(국민재난안전포털)를 먼저 따르고 위급하면 119 ③재난문자 원문(≤200자) ④**표에 있는** 가까운 대피 장소(`guidance.shelters[]` = `{type, name, address, distance_m, walk_minutes_estimate, underground, capacity, latitude, longitude, source}` · 가까운 순 · 반경 5 km · 최대 3) ⑤「일정 다시 시작」 안내.
- ★기준점은 **일정에 적힌 장소**다(`guidance.reference` = `{basis: "planned_place", place, latitude, longitude, note}`) — 실제 위치가 아니라고 글에 밝힌다. 거리는 직선거리, 걷는 시간은 4 km/h 추정이다.
- `guidance.shelter_status`: `ok` · `none_nearby`(자료는 있는데 반경 안에 없다) · `no_data`(자료 표가 비었다 — 「아직 불러오지 못했어요 + 공식 안내」) · `no_reference_place`(일정 장소 좌표를 모른다) · `not_applicable`(화재 · 산불처럼 대피 장소 목록 없이 공식 안내만). **표에 없는 대피 장소는 만들지 않는다.**
- ★`guidance.phase`: `in_progress`(여행 중) · `upcoming`(아직 시작하지 않은 여행). `upcoming` 은 **대피 장소를 싣지 않고**(`shelters: []` · `shelter_status: not_applicable`) 「아직 시작하지 않은 여행이지만 현지 상황을 몰라 정지했어요 · 가기 전에 공식 안내로 확인하세요」 글이 나간다. 여행이 시작하는 날이 와도 사용자가 풀기 전에는 계속 멈춰 있다.
- 종류: 지진 → `quake_outdoor`(옥외대피장소) · 전쟁 · 폭발 · 테러 · 화산 → `civil_defense`(민방위 대피시설).
- 공식 해제가 오면 **한 번만** `type: "guidance"` · `kind: "safety_release"`(「다치신 곳은 없으신가요? 해제됐다는 공식 안내가 나왔어요 · 일정 다시 시작」 — 안부가 먼저)가 나간다. 정지는 사용자가 풀 때까지 그대로다.
- ★`[결정 2026-10-06 사용자]` **정지가 이어지는 동안 관련 안내를 계속 보낸다**(`travel.safety.reminder`): ①**재안내** — 처음 안내 뒤 `interval_hours`(6)마다 최대 `max_count`(4)번. 해제 전에는 처음 안내(공식 안내 · 가까운 대피 장소)를 다시 `type: "safety_alert"` · `kind: "safety_pause_reminder"`(`reminder: n`), 해제 뒤에는 `type: "guidance"` · `kind: "safety_release_reminder"`(「아직 정지 중이에요」). 글은 「🔔 다치신 곳은 없으신가요?」로 시작한다. ②**다음 날 아침 질문** — 멈춘 날의 다음 날 `morning_ask_hour`(9시) 이후 첫 점검에서 정지마다 **한 번** `type: "guidance"` · `kind: "safety_morning_ask"`(「좋은 아침이에요. 지금은 괜찮으신가요? … 이어가시려면 일정 다시 시작」). **묻기만 하고 저절로 재개하지 않는다.** 끝난 여행에는 묻지 않는다. 사용자가 다시 시작하면 둘 다 멈춘다. 값은 우리가 고른 것이다(측정하지 않았다).

**`POST /v1/web/trips/{trip_id}/safety/resume`**(몸통 없음) → `{resumed: 닫은 정지 수, safety, recovery}`(★`recovery` = 이번에 푼 재난의 **상황 꾸러미**, 풀 것이 없었으면 `null`. 아래 「재난 뒤 다시 시작」) — 사용자 키 · 쿠키(쿠키는 CSRF 필요). 본인 여행만(남의 것 · 없는 것 404). 정지가 없으면 `resumed: 0`(오류가 아니다). 일정은 안 바뀐다. **같은 사건으로 다시 정지하지 않는다**(사건 지문 `event_key`) — 새 사건이 오면 다시 정지한다. 그날 정지도 자정에 자동 해제되지 않으며, 고객이 명시적으로 다시 시작해야 일정 안내와 관리가 재개된다. 여행 종료 후에는 남은 일정 관리·재안내를 끝내고 정지 이력만 보존한다.

`[미확인]` 재난문자의 재해구분명 · 긴급단계명 전체 목록을 못 구했다 — 분류 값은 우리가 고른 것이고(`travel.safety`) 본문 낱말 규칙을 함께 둔다. 실제 재난문자로 확인하기 전까지 오탐 · 미탐이 가능하다. 대피 장소 자료는 **아직 적재 전**이다(`scripts/load_safety_shelters.py` — 파일을 받는 것은 사용자 허락 뒤).

### 지도 경로선 — `/v1/web/trips/{trip_id}/route-shapes` · `/v1/web/trip-intakes/{intake_id}/route-shapes` `[2026-10-04]`

이동 하나마다 지도에 그릴 GeoJSON `LineString` 을 우리 도로 그래프(OSM)로 계산해 내린다 — 외부 길찾기 API 를 부르지 않는다. 읽기 전용, 사용자 키/쿠키, **본인 것만**(남의 것은 404).

| | 등록된 여행 | 접수 확인 화면(등록 전) |
|---|---|---|
| 경로 | `GET /v1/web/trips/{trip_id}/route-shapes` | `GET /v1/web/trip-intakes/{intake_id}/route-shapes` |
| 응답 | `{trip_id, detail, shapes[], attribution}` | `{intake_id, revision, detail, shapes[], attribution}` |
| 원천 | 여행 항목의 `route_def` | **저장된 확인 검사**(`items[]` · `moves[]`) — 없으면 이동을 다시 계산하지 않고 `shapes: []` |
| `shapes[]` | `{item_id, from_item_id, to_item_id, from, to, mode, line, walks, source, grade, distance_m, note}` | 같은 모양, **`item_id` 없음**. `from_item_id`·`to_item_id` 는 확인 화면 `review.items[].id`(「0-3」) |

- `mode`: walk · bike · taxi · subway · bus · mixed · unknown. `source`: `local_road_graph`(도로 그래프) · `stations`(탄 역 좌표 순서 + 양 끝 걸음) · `straight_line`(직선 — 이유는 `note`). `grade`: 추정 · 근거없음(확정은 없다).
- 좌표 없는 장소가 낀 이동은 건너뛴다. 접수용은 저장된 검사에 탄 역 정보(`uses`)가 없는 **옛 검사**의 지하철·대중교통을 직선 + `note` 로 내린다 — **새로 접수한** 검사부터 역 좌표를 따라 그린다(고쳐도 바뀌지 않은 이동은 저장된 값을 다시 쓰므로 옛 검사는 그대로 직선).
- `attribution`(「지도 데이터 © OpenStreetMap contributors (ODbL)」)은 선을 그릴 때 화면이 보여야 한다.
- ★`[2026-10-05]` **점 줄이기와 확대용 상세 선(`?detail=true`).** 기본 응답(`detail=false`)은 **모양을 지키며**(Douglas–Peucker, 원본이 줄인 선에서 **최대 2 m**) 점을 줄이고 한 선 최대 240점이다. 전에는 점을 **고르게 건너뛰어** 모퉁이가 잘려 확대하면 직선처럼 보였다 — 이 PC 도로 그래프 실측: 택시 8 km 최대 8.1 m · 15 km 최대 14.0 m 벗어남 → 지금은 둘 다 2.0 m 안(점 수는 333→75 · 365→145로 오히려 줄었다). `?detail=true` 는 원본에 가깝게(오차 0.5 m · 한 선 최대 5,000점) 내려 확대용으로 쓴다. 응답의 `detail` 이 어느 쪽인지 말한다. ★도로 그래프(`local_road_graph`) 경로는 원본이 OSM 모양점을 보존한다(선분 중앙 10~30 m, 이 PC 실측 4경로) — 확대해서도 곧아 보이는 것은 **지하철(`stations`)과 버스·혼합이 직선으로 내려가는 구간**이다(아래 한계).
- ★`[2026-10-05]` **지하철은 탄 역~내린 역 사이의 모든 역을 잇는다.** 일정에는 탄·갈아탄·내린 역만 담기므로(`plan.uses_of`), 사이 역은 호선별 역 순서(`LineOrder.path`)로 채우고 좌표는 역 좌표표로 얻는다 — 전에는 그 사이가 직선 한 줄이라 확대해도 같았다. 좌표를 못 찾는 역은 건너뛰고 선은 끊기지 않는다. **실제 선로·터널 곡선은 아니다**(곡선 자료 없음) — `source=stations` · `grade=추정` · `note` 가 그렇게 말한다. 순환선은 짧은 쪽으로 이으므로 운행 방향과 다를 수 있다(`note` 에 적는다). 계산기가 꺼져 있으면 노선 순서를 못 읽어 일정에 담긴 역만 잇고, `note` 가 「중간 역을 못 채웠다」고 말한다.
- ★`rides[]`(지하철·혼합이 `source=stations` 일 때만): 탄 구간마다 `{line, from, to, stations[], count, filled}` — 화면이 「지하철 3호선 · 경복궁→을지로3가 · 4개 역 · 역 사이는 직선」처럼 밝힐 수 있게 한다. `filled=false` 면 중간 역을 못 채운 구간이다. 버스·택시·걷기·직선에는 이 칸이 없다. `[2026-10-12 추가]` 선택 편성의 종류가 확인되면 선택 필드 `service: local | express`를 전달해 일반/급행을 표시한다. 저장 문맥 `station_services`와 구간 순서를 맞추며 종류 혼재·누락·미확인은 필드를 생략한다. 역 개수 `count`는 출발역을 포함한다. 소비자 지도의 출발역 제외 정차 수와 같은 정의로 비교하지 않는다.
- ★한계(2026-10-05): 버스는 정류장 목록이 일정에 없어 항상 직선이다(일정 `route_def` 에 탄·내린 정류장을 같이 저장하는 일이 먼저 — 진행 예정).
- ★`[2026-10-15 사용자 결정 — ST4F-495]` **`walks` — 걷는 구간의 위치.** `[[처음, 끝], …]` 은 `line.coordinates` 의 번호(0부터, 양 끝 포함)다. 화면은 이 구간을 걸음 모양(회색 점)으로, 나머지는 그 이동의 수단 모양으로 그린다 — **화면이 걸음 구간을 짐작해 자르지 않게** 서버가 경계를 준다(지하철 이동의 선 하나에 양 끝 걸음이 이어 붙어 있어 화면은 경계를 모른다).
  - 걷기 이동(`mode=walk`, 도로 그래프)은 선 전체 `[[0, 마지막]]`.
  - 지하철 · 버스 · 혼합(`source` 가 `stations` · `bus_stops` · `transit_stops`)은 출발 장소→첫 역·정류장, 갈아타는 걸음, 마지막 역·정류장→도착 장소. 한 걸음 구간은 **앞 탑승 조각의 마지막 점**(첫 걸음은 선의 처음)부터 **다음 탑승 조각의 첫 점**(마지막 걸음은 선의 끝)까지 — 탑승 조각과 끝점을 나눠 가져 선이 끊기지 않는다. 길찾기로 못 그려 직선으로 이은 걸음 조각도 걸음이다(`note` 가 「걸음은 직선」이라 말한다).
  - 직선 대체(`straight_line`) · 택시 · 자전거는 `[]`. 점을 줄일 때(`detail` 어느 쪽이든) 경계 점은 지우지 않으며 번호는 **줄인 뒤의 선** 기준이다.
  - 웹: 이 칸이 없는 응답(이 칸을 만들기 전 서버)은 걷기 이동만 걸음으로 그린다.

### 여행 화면 「지금」 시각 표시 — 여행 조회의 `now_label` `[2026-10-15 사용자 결정]`

`[2026-10-15 사용자 결정 — ST4F-494]` 여행 화면 일정 줄의 빨간 「지금」 점 옆에 「지금 HH:MM」 시각을 보일지를 운영자가 정한다. 시험 기간에 확인하려고 **기본 켜짐**, 배포 때 관리 콘솔에서 끈다.

**여행 조회의 `now_label`**(`GET /v1/web/trips/{id}` · `/v1/trips/{id}` — 여행을 돌려주는 응답 모두, 맨 위 칸) — `true` 면 화면이 시각 꼬리표를 보이고, `false` 면 점과 채워지는 줄만 보인다(점은 이 값과 상관없다). `[2026-10-15 사용자 지시]` 등록한 여행의 수정 모드 목록도 같은 값을 따른다.
- 값: 관리 콘솔 「운영 설정」 의 스위치 `travel.trip_screen.now_label_enabled`(운영자가 바꾼 값이 먼저) → 없거나 못 읽으면 가드레일 `travel.trip_screen.now_label_enabled`(기본 `true`). 참/거짓이 아닌 값은 바꾼 값으로 보지 않는다. 읽는 곳은 `settings_hook.trip_now_label_enabled(tenant)` 하나.
- 웹: 이 칸이 없는 응답(이 칸을 만들기 전 서버)은 켜짐으로 본다 — 사용자가 정한 기본값이다.

## 관계

- [REST 안내](index.md) — 계약 지도
