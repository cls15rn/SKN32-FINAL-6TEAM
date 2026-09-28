-- sql/mobility_schema.sql — 이동·동선(Mobility) 모듈 저장소
-- 대상: SQLite (팀 공용 RDB 가 정해지면 판정 이력만 이관한다. 참조 데이터는 재생성이 더 싸다)
-- 적재: python scripts/load_mobility_db.py
-- 근거 문서: 인수인계 03_저장소_구현.md · 역순서_결과_v1 · 행선지필드_역전_v1 · 코어계약_요약_v0.2
--
-- ── 이 스키마가 지키는 것 ─────────────────────────────────────────────────
-- 아래는 전부 **실제로 틀렸던 것을 고친 자리**다. 주석을 지우지 말 것 —
-- 컬럼 타입 하나를 되돌리면 그때 그 버그가 그대로 돌아온다.
--
--  ① 시각은 전부 **분 단위 INTEGER**다. TIME·TEXT 로 잡지 않는다.
--     지하철 `dep_time` 에 24:50, 버스 막차에 27:30 이 들어간다.
--     `datetime.strptime(t, "%H:%M:%S")` 는 '24:50:00' 에서 죽고, 00:50 으로 되돌리면
--     그 행이 하루의 가장 이른 시각이 되어 첫차·막차가 둘 다 뒤집힌다(C4 가 잡은 버그).
--     렌더링은 modules/mobility/timeutil.py 의 fmt_min / fmt_wall 한 자리에서만 한다.
--
--  ② 출발 시각은 **NULL 을 허용**한다. 시·종착역은 출발이 없다.
--     소스가 '0'·'000000' 으로 주는데 0시로 저장하면 막차 판정이 뒤집히고,
--     24시로 저장하면 가짜 막차가 생긴다(2,527행이 실제로 그랬다).
--     "값이 없다"를 0 으로 채우지 않는다.
--
--  ③ 방향의 정본은 `dir` 이 아니라 **행선지**다.
--     인천2호선·신림선은 같은 dir 에 양 끝 행선지가 섞여 있다(충돌 간선 19/26, 7/10).
--     `dir` 은 소스가 준 값 그대로 보관만 하고, 판정은 dest_station_nm + mob_line_edge 로 한다.
--
--  ④ `mob_verdict_evidence.source_type` 에 CHECK 를 건다.
--     카카오 응답('tool_result')을 저장할 수 없게 **스키마로 막는다**. 약관 방침을
--     문서가 아니라 제약으로 강제하는 자리다. 새 값이 필요하면 02·04번 방과 합의 후 넓힌다
--     (2026-09-11 이슈 근거를 'external' 로 냈다가 적재에서 거부된 적이 있다 → 'case_event' 로 정리).
--
--  ⑤ 카카오 유래 값은 **컬럼으로 만들지 않는다** — 소요·도보 거리·환승 횟수·정류장·좌표·요금.
--     컬럼이 없으면 실수로도 못 넣는다. mob_line_edge.travel_min 과 mob_transfer_walk 는
--     우리 시간표 관측과 공개 자료라 해당 없다.
--
--  ⑥ 요일축은 표마다 **값 집합이 다르다.** 같은 이름의 컬럼이라고 같은 도메인이 아니다.
--     시간표 ('weekday','saturday','holiday') · 혼잡도 ('weekday','saturday','sunday').
--     CHECK 를 각각 건다.
--
--  ⑦ FK 를 **일부러 안 거는 곳이 둘** 있다. 아래 해당 표에 이유를 적었다.

PRAGMA foreign_keys = ON;

-- ════════════════════════════════════════════════════════════════════════
--  A. 참조 데이터 — 재생성 가능. 백업 대상이 아니다.
--     재적재는 이 표들만 비우고 다시 넣는다.
-- ════════════════════════════════════════════════════════════════════════

-- A1. 지하철 통합 시간표 (463,326행) ─────────────────────────────────────
--     TAGO + 서울 열린데이터광장(OA-101) 보충 + 지선 접속역 보충.
CREATE TABLE IF NOT EXISTS mob_timetable (
    id                  INTEGER PRIMARY KEY,
    line                TEXT    NOT NULL,          -- '02호선' — 열린데이터광장 LINE_NUM 표기로 통일
    station_key         TEXT    NOT NULL,          -- '02호선|강변'
    station_cd          TEXT,                      -- 열린데이터광장 역코드. 못 이은 행은 NULL
    station_nm          TEXT    NOT NULL,
    station_nm_en       TEXT,
    dir                 TEXT    NOT NULL CHECK (dir IN ('U','D')),
                                                   -- ★ 소스가 준 값. 방향의 정본이 아니다(위 ③)
    day_type            TEXT    NOT NULL CHECK (day_type IN ('weekday','saturday','holiday')),
    dep_min             INTEGER,                   -- ★ 분 단위. 24 시 이상 가능(1490 = 24:50). NULL = 출발 없음(위 ①②)
    arr_min             INTEGER,
    dest_station_nm     TEXT,                      -- 행선지 — ★ 역명으로 정규화한 값. 조인이 되는 쪽
    dest_station_nm_src TEXT,                      -- 소스 원표기. 추적용이고 조인에 쓰지 않는다
    orig_station_nm     TEXT,                      -- 시발역(정규화). OA-101 의 SUBWAYSNAME 이 여기다
    orig_station_nm_src TEXT,
    train_no            TEXT,                      -- OA-101 만 준다. 열차 복원에 쓴다
    express             TEXT,
    source              TEXT    NOT NULL,          -- 'tago_subway' | 'seoul_opendata_OA-101'
    source_station_id   TEXT    NOT NULL,
    fetched_at          TEXT    NOT NULL,          -- 확인 시각. Evidence.observed_at 이 된다
    fetched_at_precision TEXT   NOT NULL CHECK (fetched_at_precision IN ('day','second'))
);
--  ★ orig_station_nm 이 따로 있는 이유 (2026-09-11)
--    열린데이터광장 OA-101 의 `SUBWAYSNAME` 은 **행선지가 아니라 시발역**이다.
--    TRAIN_NO 로 열차를 복원해 세면 첫 역 일치 2,079건 · 끝 역 일치 0건이다.
--    행선지로 읽고 있던 동안 2호선 막차 행선지가 101개 조합에서 뒤집혀 있었다
--    (도림천 평일 막차가 '신도림행' → 실제 '까치산행').
--    진짜 행선지는 같은 열차에서 '출발 시각 없고 도착 시각만 있는 행'의 역으로 복원한다.
--    두 필드를 한 컬럼에 섞지 않는다 — 섞으면 같은 사고가 반복된다.
--    회귀 검사: consistency_check.py C6.
--  ★ 행선지는 **정규화해서 넣는다.** 소스가 '하남검단산역'·'불암산(당고개)'·'신창(순천향대)'
--    처럼 표기 변형을 준다. 원표기 그대로 두면 52,844행(11%)이 mob_station_coord 와
--    안 붙어서 "이 열차가 목적지를 지나는가"를 SQL 로 못 푼다 — DB 로 옮긴 의미가 없어진다.
--    정규화 규칙은 line_station_order_v1.json 의 dest_alias 와 같다(괄호 제거, 끝의 '역' 제거).
--    원표기는 dest_station_nm_src 에 남긴다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_timetable
    ON mob_timetable (source, source_station_id, day_type, dir, dep_min, arr_min, dest_station_nm, train_no);
    --  수집이 중단·재개되는 구조라 중복 적재를 막아야 한다.
    --  ※ SQLite 는 UNIQUE 에서 NULL 을 서로 다른 값으로 본다. dep_min 이 NULL 인 행
    --    (시·종착역)은 이 인덱스로 안 걸러지므로 적재 스크립트가 한 번 더 거른다.
CREATE INDEX IF NOT EXISTS ix_timetable_lookup
    ON mob_timetable (line, station_nm, dir, day_type, dep_min);
    --  판정기의 주 질의 — "이 역 이 방향 이 요일에서 t 이후 다음 출발".
CREATE INDEX IF NOT EXISTS ix_timetable_station_key ON mob_timetable (station_key);
    --  혼잡도·역 목록과 대조할 때. 없으면 소스 간 점검 질의가 전수 스캔이 된다.
CREATE INDEX IF NOT EXISTS ix_timetable_dest
    ON mob_timetable (line, station_nm, day_type, dest_station_nm);
    --  막차 후보를 행선지로 거를 때.

-- A2. 노선별 역 순서 — 간선 (777) ────────────────────────────────────────
--     "이 열차가 내 목적지를 지나는가"를 푸는 표. 02번 방 막차 판정의 뼈대다.
CREATE TABLE IF NOT EXISTS mob_line_edge (
    id                  INTEGER PRIMARY KEY,
    line                TEXT    NOT NULL,
    station_a           TEXT    NOT NULL,          -- (a,b) 는 정렬해 넣는다. 무향 간선이다
    station_b           TEXT    NOT NULL,
    edge_source         TEXT    NOT NULL,          -- 'fr_seq' | 'fr_sub' | 'manual:<이유>'
    grade               TEXT    NOT NULL,          -- '확정' | '추정:…' | '근거없음'
    travel_min          REAL,                      -- 0.1분 단위. NULL = 근거없음(46/777)
    travel_min_source   TEXT CHECK (travel_min_source IN ('observed','official')),
    travel_min_grade    TEXT CHECK (travel_min_grade IN ('확정','추정')),
    distance_m          INTEGER,                   -- 서울교통공사 공식 역간거리(270개 간선)
    dir_a_to_b          TEXT CHECK (dir_a_to_b IN ('U','D')),  -- a→b 로 달리는 열차의 dir 라벨
    dir_b_to_a          TEXT CHECK (dir_b_to_a IN ('U','D')),
    note                TEXT
);
--  ★ travel_min 은 출처를 반드시 같이 본다.
--    'observed' = 우리가 출발시각 차로 관측한 값. **정차시간이 들어 있다.**
--    'official' = 서울교통공사 역간거리·소요시간표. **주행시간만**이라 평균 0.5분 작다.
--    승객이 겪는 값은 observed 쪽이라 관측이 있으면 관측을 쓰고, 없을 때만 official 로 메운다.
--    250개 구간 대조에서 둘의 절대중앙 차이가 0.50분, ±1분 이내 100% 였다.
--  ★ grade 가 '근거없음'인 간선을 지나는 경로는 판정도 근거없음으로 낸다.
--    구조(FR_CODE)는 알지만 그 구간에 시간표 표본이 없다는 뜻이다 — 이어져 있다고 단정하지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_line_edge ON mob_line_edge (line, station_a, station_b);
CREATE INDEX IF NOT EXISTS ix_line_edge_a ON mob_line_edge (line, station_a);
CREATE INDEX IF NOT EXISTS ix_line_edge_b ON mob_line_edge (line, station_b);

-- A3. 역 좌표 + 노선 내 순서 (793) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_station_coord (
    station_key         TEXT    PRIMARY KEY,       -- '01호선|명학'
    line                TEXT    NOT NULL,
    station_nm          TEXT    NOT NULL,
    station_nm_en       TEXT,
    station_cd          TEXT,
    fr_code             TEXT,                      -- 공식 역번호. 노선 순서의 정본이다
    fr_order            INTEGER,                   -- FR_CODE 정렬 순번(노선 안에서)
    is_spur             INTEGER NOT NULL DEFAULT 0 CHECK (is_spur IN (0,1)),
    has_timetable       INTEGER NOT NULL DEFAULT 0 CHECK (has_timetable IN (0,1)),
    lat                 REAL,                      -- 좌표 미확보 6역은 NULL
    lng                 REAL,
    operator            TEXT,
    source              TEXT,
    fetched_at          TEXT
);
--  ★ 역 순서는 FR_CODE 가 정본이다. 시간표에서 유도한 순서는 틀린다 —
--    열차가 여러 차량기지에서 따로 출발해서 min(출발시각)이 같은 열차의 것이 아니다.
--    (3호선 하행에서 대곡 다음이 구파발로 튀고 압구정이 9번째로 올라왔다.)
--    시간표는 검증용으로만 쓴다.
CREATE INDEX IF NOT EXISTS ix_station_line_order ON mob_station_coord (line, fr_order);
CREATE INDEX IF NOT EXISTS ix_station_nm ON mob_station_coord (station_nm);

-- A4. 환승역 도보 거리 (213쌍 · 74역) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_transfer_walk (
    id                  INTEGER PRIMARY KEY,
    station_nm          TEXT    NOT NULL,
    from_line           TEXT    NOT NULL,
    to_line             TEXT    NOT NULL,
    distance_m          REAL    NOT NULL,
    walk_min            REAL    NOT NULL,          -- 거리 ÷ 우리 실측 1.04 m/s
    grade               TEXT    NOT NULL,          -- distance_m 확정 · walk_min 추정
    checked_at          TEXT    NOT NULL           -- 소스 기준일(2025-12-31)
);
--  ★ 소스의 소요시간(src_min)은 담지 않는다. 소스는 1.2 m/s 로 환산한 값인데
--    우리는 실측 1.04 m/s 를 쓴다. 남의 보행속도 가정을 표에 굳히면
--    나중에 어느 쪽 값인지 구분이 안 된다. 거리만 받아 우리가 나눈다.
--  ★ 서울교통공사 관할 환승만 있다. 코레일·공항철도끼리의 환승은 소스에 없다 → 근거없음.
CREATE UNIQUE INDEX IF NOT EXISTS ux_transfer_walk ON mob_transfer_walk (station_nm, from_line, to_line);

-- A5. 지하철 혼잡도 (65,169) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_congestion (
    id                  INTEGER PRIMARY KEY,
    station_key         TEXT    NOT NULL,
    station_cd          TEXT,
    station_nm          TEXT    NOT NULL,
    line                TEXT    NOT NULL,
    branch              TEXT,                      -- 본선/지선 구분. 시간표에는 없는 축이다
    src_station_cd      TEXT    NOT NULL,
    dir                 TEXT    NOT NULL CHECK (dir IN ('U','D')),
    dir_raw             TEXT,                      -- '상선'/'하선' 원표기
    day_type            TEXT    NOT NULL CHECK (day_type IN ('weekday','saturday','sunday')),
                                                   -- ★ 시간표와 값 집합이 다르다(위 ⑥)
    slot_min            INTEGER NOT NULL,          -- 30분 슬롯 시작(분). 24:30 = 1470 까지
    congestion          REAL,                      -- ★ NULL 허용 — 아래
    reason              TEXT CHECK (reason IN ('terminus_direction','after_last_train','no_train_in_window')),
    grade               TEXT    NOT NULL,
    source              TEXT    NOT NULL,
    source_id           TEXT    NOT NULL,
    data_basis_date     TEXT,
    fetched_at          TEXT    NOT NULL
);
--  ★ congestion 을 NOT NULL 로 잡고 0 을 채우면 안 된다. 0 은 혼잡도 0% 가 아니라
--    '그 방향으로 출발하는 열차가 없다'는 뜻이고, 0 을 값으로 두면 그 구간이
--    **가장 한산한 구간**으로 뒤집힌다. 원인을 reason 에 남긴다.
--    이 성질이 dir 매핑 검증(C1)의 근거이기도 하다 — 전 시간대 0 인 조합은
--    시간표에서도 편수가 0이어야 한다(26 조합 중 25 일치).
CREATE UNIQUE INDEX IF NOT EXISTS ux_congestion ON mob_congestion (src_station_cd, dir_raw, day_type, slot_min);
CREATE INDEX IF NOT EXISTS ix_congestion_lookup ON mob_congestion (station_key, dir, day_type, slot_min);

-- A6. 서울 시내버스 노선 (21) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_bus_route (
    route_id            TEXT    PRIMARY KEY,
    route_nm            TEXT    NOT NULL,
    route_type          TEXT,
    route_type_nm       TEXT,
    route_type_grade    TEXT,
    corp_nm             TEXT,
    st_station_nm       TEXT,
    ed_station_nm       TEXT,
    length_km           REAL,
    term_min            INTEGER,                   -- ★ NULL 허용 — 아래
    first_min           INTEGER,                   -- 분 단위
    last_min            INTEGER,                   -- 27:30 = 1650 같은 값이 들어온다
    crosses_midnight    INTEGER NOT NULL DEFAULT 0 CHECK (crosses_midnight IN (0,1)),
    grade_service_window TEXT,
    grade_wait          TEXT,
    source              TEXT,
    source_id           TEXT,
    fetched_at          TEXT
);
--  ★ term_min 이 NULL 인 것은 배차 0분이 아니라 **배차 개념이 없다**는 뜻이다
--    (예약제·출퇴근 전용). 소스가 0 으로 주는 것을 그대로 넣으면 대기 0분이 되어
--    "바로 탄다"가 된다.
CREATE INDEX IF NOT EXISTS ix_bus_route_nm ON mob_bus_route (route_nm);

-- A7. 버스 정류장 (1,397행 · 고유 906 · 좌표 1,027) ──────────────────────
CREATE TABLE IF NOT EXISTS mob_bus_stop (
    id                  INTEGER PRIMARY KEY,
    route_id            TEXT    NOT NULL REFERENCES mob_bus_route(route_id),
    seq                 INTEGER NOT NULL,
    station_id          TEXT    NOT NULL,
    ars_id              TEXT,
    station_nm          TEXT    NOT NULL,
    lat                 REAL,
    lng                 REAL,
    direction           TEXT,
    sect_dist_m         INTEGER,
    transfer_yn         TEXT,
    source              TEXT,
    source_id           TEXT,
    fetched_at          TEXT
);
--  한 정류장이 여러 노선에 나오므로 (route_id, seq) 가 키다. station_id 는 중복된다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_bus_stop ON mob_bus_stop (route_id, seq);
CREATE INDEX IF NOT EXISTS ix_bus_stop_station ON mob_bus_stop (station_id);
CREATE INDEX IF NOT EXISTS ix_bus_stop_geo ON mob_bus_stop (lat, lng);
    --  대안 열거(F3)의 stops_near() 가 좌표로 훑는다.

-- A8. 버스 표정속도 ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_bus_speed (
    route_nm            TEXT    PRIMARY KEY,
    route_type_nm       TEXT,
    speed_kmh           REAL,                      -- 우리 실측(관측 기반)
    stat_kmh            REAL,                      -- 교통카드 통계의 유형값
    by_hour_kmh         TEXT,                      -- JSON. 시간대별 실측
    observed_km         REAL,
    observed_min        INTEGER,
    grade               TEXT    NOT NULL,
    source_id           TEXT,
    built_at            TEXT
);
--  ★ 인계 목록의 9개에서 하나 늘렸다(담당 결정). 판정기가 버스 승차 소요를 낼 때 읽는
--    참조 데이터이고, 규칙이 아니라 관측값이라 rules json 이 아니라 여기가 맞다.
--  ★ 실측값이 유형값을 덮는다. 같은 순환선인데 01A 15.3 · 01B 17.8 로 갈린다 —
--    유형 단위로는 못 담는 차이다. 그래서 노선 단위로 둔다.

-- A9. 공항 리무진 출발 (8,949) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_airport_bus_departure (
    id                  INTEGER PRIMARY KEY,
    route_no            TEXT    NOT NULL,
    route_key           TEXT    NOT NULL,
    terminal            TEXT    NOT NULL,          -- 'T1' | 'T2'
    dir                 TEXT    NOT NULL,          -- 'from_airport' | 'to_airport'
    day_type            TEXT    NOT NULL,
    dep_min             INTEGER,
    first_min           INTEGER,
    last_min            INTEGER,
    area                TEXT,
    bus_class           TEXT,
    fare_adult          INTEGER,                   -- 운영사 공시 요금. 카카오 유래가 아니다
    operator            TEXT,
    ride_location       TEXT,
    timetable_available INTEGER NOT NULL DEFAULT 0 CHECK (timetable_available IN (0,1)),
    grade               TEXT    NOT NULL,
    source              TEXT    NOT NULL,
    fetched_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_airport_bus ON mob_airport_bus_departure (route_no, terminal, dir, day_type, dep_min);

-- A10. 공휴일 (46) ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_holiday (
    holiday_date        TEXT    PRIMARY KEY,       -- 'YYYY-MM-DD'
    name                TEXT,
    source              TEXT,
    fetched_at          TEXT
);
--  day_type_of() 의 정본. 요일축 판정이 여기서 갈린다.

-- ════════════════════════════════════════════════════════════════════════
--  B. 모듈 산출 — 되돌릴 수 없다. 백업 대상.
--     팀 공용 RDB 가 정해지면 **이 둘만 이관한다.**
-- ════════════════════════════════════════════════════════════════════════

-- B1. 구간 판정 이력 ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_leg_verdict (
    verdict_id          TEXT    PRIMARY KEY,       -- 'mob:{case_id}:{leg_id}:{decided_at}'
    case_id             TEXT    NOT NULL,
    task_id             TEXT,
    run_id              TEXT,
    leg_id              TEXT    NOT NULL,
    schedule_version    INTEGER,
    decided_at          TEXT    NOT NULL,
    verdict             TEXT    NOT NULL CHECK (verdict IN ('ok','fail','unknown','rejected_by_limit')),
                                                   -- 계약 decisions[].verdict 는 ok/fail/unknown 3종.
                                                   -- rejected_by_limit 은 모듈 내부 구분이며
                                                   -- 코어로 낼 때 fail + relax 로 접는다(설계서 판정 값 4종).
    evidence_grade      TEXT    NOT NULL CHECK (evidence_grade IN ('확정','추정','근거없음')),
    from_name           TEXT,
    to_name             TEXT,
    depart_at_min       INTEGER,
    arrive_by_min       INTEGER,
    arrive_est_min      INTEGER,                   -- 도착 예상. 소요를 모르면 NULL('성립 + 도착 미상')
    reasons_json        TEXT,                      -- JSON 배열
    relax_json          TEXT,                      -- JSON 배열 — 완화 조건
    estimate_json       TEXT,                      -- JSON — duration_min·transfers·grade 등
    stage_assumed       INTEGER NOT NULL DEFAULT 0 CHECK (stage_assumed IN (0,1)),
    rules_version       TEXT,                      -- 'rules_v0.3'
    timetable_built_at  TEXT                       -- 어느 판 시간표로 냈는지
);
--  ★ 참조 데이터로 FK 를 걸지 않는다(일부러). 시간표는 개정 때 통째로 교체되므로
--    FK 를 걸면 재적재마다 지난 판정이 깨진다. 판정 이력은 그 시점의 사실이고
--    참조 데이터의 현재 상태와 무관하게 남아야 한다.
--    대신 timetable_built_at 으로 "어느 판을 보고 낸 판정인지"를 남긴다.
--  ★ 재확인(F2)이 이 표를 읽어 "지난번과 달라졌나"를 비교한다. 이력이 없으면 F2 가 성립하지 않는다.
CREATE INDEX IF NOT EXISTS ix_verdict_case ON mob_leg_verdict (case_id, leg_id, decided_at DESC);

-- B2. 판정 근거 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mob_verdict_evidence (
    verdict_id          TEXT    NOT NULL REFERENCES mob_leg_verdict(verdict_id) ON DELETE CASCADE,
    evidence_id         TEXT    NOT NULL,          -- 'mob:{leg_id}:{kind}:{n}' — 결정적. ★ 한 결과 안에서만 유일하다
    source_type         TEXT    NOT NULL CHECK (source_type IN ('db','policy','case_event')),
    source_id           TEXT    NOT NULL,          -- 'tago_subway@2026-09-09' · 'mobility_rules@v0.3'
    claim               TEXT    NOT NULL,
    value_json          TEXT,
    confidence          REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    observed_at         TEXT    NOT NULL,          -- 시간표는 수집일, 규칙은 개정일
    PRIMARY KEY (verdict_id, evidence_id)
);
--  ★ PK 가 복합키인 이유(2026-09-13). evidence_id 'mob:L3:timetable:1' 은 **Case 가 달라도 같은 문자열**이다.
--    단독 PK 로 두면 두 번째 Case 의 적재가 PK 충돌로 터진다. evidence_id 는 한 결과(TeamResult) 안에서만
--    유일하면 되고(계약 검증기가 보는 범위가 그것이다), 길게 만들면 answer·evidence 예산에 불리하다.
--  ★ value_json 은 mobility_scripts/mobility_checks/contract/mob_evidence_value_v1.schema.json 로 검증한 뒤 적재한다.
--    저장하는 kind 는 timetable·rule·walk·bus·issue·prev_verdict 여섯. verdict·run·alt 는 저장하지 않고
--    mob_leg_verdict 행에서 접을 때 생성한다(접기 설계 v1 §15).
--  ★★ source_type 의 CHECK 가 이 스키마에서 가장 중요한 한 줄이다.
--     계약의 Evidence.source_type 은 6종이지만 우리는 셋만 허용한다.
--     빠진 셋 중 **'tool_result' 가 카카오 응답**이고, 2026-09-09 약관 확인에서
--     원값·가공값 모두 저장 금지로 정리됐다. 문서로만 두면 언젠가 누가 넣는다.
--     'customer_message'·'remote_agent' 는 이 모듈이 만들 일이 없다.
--     ※ 새 source_type 이 필요하면 넓히기 전에 02·04번 방과 합의할 것.
--       2026-09-11 에 이슈 근거를 'external' 로 냈다가 적재에서 통째로 거부된 적이 있다.
--       판정은 맞는데 근거만 사라지는 가장 나쁜 형태였다 → 'case_event' 로 정리했다.
--  ★ 근거 등급 '근거없음'은 Evidence 를 만들지 않는다. 계약의 Evidence 는 출처 있는 주장이라
--    "없다"를 Evidence 로 만들면 근거 대조에서 확정과 섞인다.
--    mob_leg_verdict.evidence_grade='근거없음' + reasons_json 으로 남긴다.
CREATE INDEX IF NOT EXISTS ix_evidence_verdict ON mob_verdict_evidence (verdict_id);

-- ════════════════════════════════════════════════════════════════════════
--  C. 적재 메타 — 어느 판으로 채웠는지
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS mob_load_meta (
    table_name          TEXT    PRIMARY KEY,
    row_count           INTEGER NOT NULL,
    src_file            TEXT,
    src_built_at        TEXT,
    loaded_at           TEXT    NOT NULL
);
