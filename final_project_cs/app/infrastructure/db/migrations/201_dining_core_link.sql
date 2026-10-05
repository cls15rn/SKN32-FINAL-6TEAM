-- 요식 DB 코어 연결분
-- 대상: D-15 연결 표, 그리고 코어가 읽을 판정 조회 뷰
-- 작성 2026-09-20. A-COP role-core1 브랜치의 실제 스키마를 읽고 맞췄다.
--
-- 근거로 삼은 코어 파일
--   migrations/010_domain_travel.sql       places 표
--   migrations/013_places_source_identity.sql  공급자 신원 칸
--   migrations/014_itinerary.sql           trips, itinerary_versions, itinerary_items, places.attributes
--   modules/travel_ops/replan.py           dining_fits(), open_during()
--   modules/travel_ops/dining.py           places.open_at_slot 을 읽는 판정 경로
--
-- 원칙 (DN-C1)
--   코어 표에 외래키를 걸지 않는다. 아래 연결 표의 core_place_id 도 논리 참조다.
--   코어 스키마가 바뀌어도 요식 표가 깨지지 않게 하려는 것이다.

-- D-15 코어 장소 연결
-- 코어 places.place_id 는 uuid, tenant_id 는 text 로 확인했다 (010).
CREATE TABLE IF NOT EXISTS dining.dn_core_place_link (
    tenant_id     text NOT NULL,
    core_place_id uuid NOT NULL,
    place_uid     uuid NOT NULL REFERENCES dining.dn_place(place_uid),
    linked_by     text NOT NULL,
    linked_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, core_place_id)
);

CREATE INDEX IF NOT EXISTS dn_core_place_link_place_idx
    ON dining.dn_core_place_link (place_uid);

COMMENT ON TABLE dining.dn_core_place_link IS
    '코어 places 한 행과 요식 원장 장소의 연결. 코어 표에 FK 를 걸지 않으므로 논리 참조다.';
COMMENT ON COLUMN dining.dn_core_place_link.core_place_id IS
    '코어 places.place_id. 코어의 source_name 과 source_content_id 는 공급자 신원 그대로 두고 건드리지 않는다.';


-- 판정에 쓸 수 있는 영업 규칙만 추린 뷰 (DN-C7)
-- 폐기된 규칙과 검수되지 않은 LLM 추출을 여기서 걸러낸다.
CREATE OR REPLACE VIEW dining.v_hours_rule_active AS
SELECT r.*
FROM dining.dn_hours_rule r
WHERE r.retired_at IS NULL
  AND (r.extract_method <> 'llm' OR r.verified_at IS NOT NULL);

CREATE OR REPLACE VIEW dining.v_closure_rule_active AS
SELECT c.*
FROM dining.dn_closure_rule c
WHERE c.retired_at IS NULL
  AND (c.extract_method <> 'llm' OR c.verified_at IS NOT NULL);


-- 방문일의 영업 구간
-- 요일을 받아 그날 적용되는 구간만 돌려준다.
-- 코어의 dining_fits() 는 요일 개념이 없어 hours 를 한 쌍만 본다. 그 한 쌍을 여기서 고른다.
CREATE OR REPLACE FUNCTION dining.day_intervals(
    p_place_uid uuid,
    p_visit_date date
)
RETURNS TABLE (
    seq              smallint,
    open_min         smallint,
    close_min        smallint,
    last_order_min   smallint,
    last_order_state text,
    break_state      text,
    coverage         text,
    source_code      text,
    extract_method   text
)
LANGUAGE sql
STABLE
AS $$
    SELECT i.seq, i.open_min, i.close_min, i.last_order_min, i.last_order_state,
           r.break_state, r.coverage, r.source_code, r.extract_method
    FROM dining.v_hours_rule_active r
    JOIN dining.dn_hours_interval i ON i.rule_id = r.rule_id
    WHERE r.place_uid = p_place_uid
      AND r.rule_kind = 'weekly'
      AND r.weekday = EXTRACT(ISODOW FROM p_visit_date)::smallint
      AND r.valid_from <= p_visit_date
      AND (r.valid_to IS NULL OR r.valid_to >= p_visit_date)
    ORDER BY i.seq
$$;

COMMENT ON FUNCTION dining.day_intervals IS
    '공휴일 규칙(rule_kind=holiday)은 아직 반영하지 않는다. 공휴일 달력 공통화가 정해지면 분기를 넣는다.';


-- 두 좌표 사이 거리(미터). PostGIS 를 쓰지 않으므로 직접 계산한다.
--
-- 022 에 있던 것을 옮겼다. 022 는 코어 places 표가 있어야 올라가는데
-- 대체 후보(027)도 이 함수를 쓴다. 코어 없이 원장만 세우는 경우가 있으므로
-- 코어와 무관한 계산은 코어에 매이지 않은 자리에 둔다.
CREATE OR REPLACE FUNCTION dining.distance_m(
    p_lat1 double precision, p_lng1 double precision,
    p_lat2 double precision, p_lng2 double precision
)
RETURNS double precision
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 2 * 6371000 * asin(sqrt(
        power(sin(radians(p_lat2 - p_lat1) / 2), 2)
        + cos(radians(p_lat1)) * cos(radians(p_lat2))
          * power(sin(radians(p_lng2 - p_lng1) / 2), 2)
    ))
$$;


-- 방문일이 휴무인가
-- 공휴일과 명절은 날짜를 여기서 판단하지 않는다. 달력이 붙기 전까지 false 로 둔다.
CREATE OR REPLACE FUNCTION dining.is_closed_on(
    p_place_uid uuid,
    p_visit_date date
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM dining.v_closure_rule_active c
        WHERE c.place_uid = p_place_uid
          AND c.valid_from <= p_visit_date
          AND (c.valid_to IS NULL OR c.valid_to >= p_visit_date)
          AND (
                (c.pattern_kind = 'weekly'
                     AND c.weekday = EXTRACT(ISODOW FROM p_visit_date)::smallint)
             OR (c.pattern_kind = 'monthly_nth'
                     AND c.weekday = EXTRACT(ISODOW FROM p_visit_date)::smallint
                     AND ((EXTRACT(DAY FROM p_visit_date)::int - 1) / 7 + 1) = ANY (c.nth))
             OR (c.pattern_kind = 'date'
                     AND c.closed_date = p_visit_date)
          )
    )
$$;

COMMENT ON FUNCTION dining.is_closed_on IS
    'public_holiday 와 named_holiday 는 여기서 판단하지 않는다. 날짜를 모르기 때문이며, 공휴일 달력이 붙으면 추가한다.';


-- 코어 형식으로 내보내는 뷰
-- 코어 places.attributes 는 hours 와 break 를 "HH:MM" 두 개짜리 배열로 쓴다 (replan.py open_during).
-- 요식 원장의 분 단위 값을 그 형식으로 바꾼다.
-- 구간이 두 개인 날은 첫 구간 시작과 마지막 구간 끝을 hours 로 하고, 그 사이를 break 로 낸다.
CREATE OR REPLACE FUNCTION dining.core_attributes(
    p_place_uid uuid,
    p_visit_date date
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_rows   record;
    v_open   smallint;
    v_close  smallint;
    v_n      int;
    v_b1     smallint;
    v_b2     smallint;
    v_result jsonb;
BEGIN
    IF dining.is_closed_on(p_place_uid, p_visit_date) THEN
        RETURN jsonb_build_object('closed', true);
    END IF;

    SELECT count(*), min(open_min), max(close_min)
      INTO v_n, v_open, v_close
      FROM dining.day_intervals(p_place_uid, p_visit_date);

    IF v_n = 0 THEN
        -- 모름이다. 코어는 hours 가 없으면 None 으로 읽고 "연다" 로 해석하지 않는다.
        RETURN '{}'::jsonb;
    END IF;

    -- 자정을 넘는 날은 코어 형식으로 표현할 수 없다.
    -- 코어 open_during() 은 두 값을 같은 날짜에 붙이므로 "02:00" 을 새벽이 아니라
    -- 그날 오전으로 읽어 항상 거짓이 된다. 24:00 으로 잘라 내보내면 실제보다 좁게 잡혀
    -- 멀쩡한 집을 거르게 된다. 둘 다 틀리므로 모름으로 남긴다 (DN-C5).
    -- 이 장소의 정확한 판정은 dining.open_at_slot 이 따로 제공한다.
    IF v_close >= 1440 THEN
        RETURN '{}'::jsonb;
    END IF;

    v_result := jsonb_build_object(
        'hours',
        jsonb_build_array(dining.min_to_hhmm(v_open), dining.min_to_hhmm(v_close))
    );

    IF v_n = 2 THEN
        SELECT max(close_min) INTO v_b1
          FROM dining.day_intervals(p_place_uid, p_visit_date) WHERE seq = 1;
        SELECT min(open_min) INTO v_b2
          FROM dining.day_intervals(p_place_uid, p_visit_date) WHERE seq = 2;
        v_result := v_result || jsonb_build_object(
            'break', jsonb_build_array(dining.min_to_hhmm(v_b1), dining.min_to_hhmm(v_b2))
        );
    END IF;

    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION dining.min_to_hhmm(p_min smallint)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lpad(((p_min / 60) % 24)::text, 2, '0') || ':' || lpad((p_min % 60)::text, 2, '0')
$$;

COMMENT ON FUNCTION dining.core_attributes IS
    '코어가 읽는 형식으로 바꾼다. 구간이 셋 이상인 날은 break 를 내지 않는다 — 코어 형식이 한 쌍만 표현할 수 있어서다. 그런 날은 요식 판정 결과를 직접 쓴다.';


-- ──────────────────────────────────────────────────────────────
-- open_at_slot 계산
--
-- 코어 places.open_at_slot 은 "그 일정 시각에 여는가" 를 담는 불리언이고 NULL 은 모름이다.
-- modules/travel_ops/dining.py 가 이 칸 하나만 읽어 답을 만든다. 스스로 계산하지 않는다.
-- 그런데 이 칸을 채우는 코드가 코어에 없다 — scripts/seed_travel.py 가 시연용으로 넣을 뿐이고,
-- infrastructure/travel/tour_api.py 는 answers_open_at_slot=False 로 원문만 넘긴다.
-- 요식 원장이 그 자리를 채운다.
--
-- 판정 규칙은 2026-09-18 에 정한 것을 따른다.
--   라스트오더 값이 없으면 판정에서 빼고 위반으로 만들지 않는다 (P-02).
--   종료 60분 이내는 경고이며 불리언에 담지 않는다. 경고는 판정 결과로 따로 낸다.
--   브레이크 시작과 식사 종료가 같으면 통과시킨다 (P-01).
-- ──────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION dining.open_at_slot(
    p_place_uid uuid,
    p_starts_at timestamptz,
    p_ends_at   timestamptz DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_date      date;
    v_prev      date;
    v_start_min int;
    v_end_min   int;
    v_today_has boolean;
    v_prev_has  boolean;
BEGIN
    -- 0. 폐업으로 확인된 가게는 닫힘이다(2026-10-01). 영업규칙만 보면 규칙이 없을 때 「모름」,
    --    옛 규칙이 남았을 때 「영업」으로 답해 폐업한 식당을 경고하지 못했다.
    IF EXISTS (SELECT 1 FROM dining.dn_place WHERE place_uid = p_place_uid AND record_status = 'closed') THEN
        RETURN false;
    END IF;

    v_date      := (p_starts_at AT TIME ZONE 'Asia/Seoul')::date;
    v_prev      := v_date - 1;
    v_start_min := EXTRACT(HOUR   FROM p_starts_at AT TIME ZONE 'Asia/Seoul')::int * 60
                 + EXTRACT(MINUTE FROM p_starts_at AT TIME ZONE 'Asia/Seoul')::int;
    v_end_min   := CASE WHEN p_ends_at IS NULL THEN v_start_min
                        ELSE v_start_min + (EXTRACT(EPOCH FROM (p_ends_at - p_starts_at)) / 60)::int
                   END;

    SELECT EXISTS (SELECT 1 FROM dining.day_intervals(p_place_uid, v_date))
      INTO v_today_has;
    SELECT EXISTS (SELECT 1 FROM dining.day_intervals(p_place_uid, v_prev) WHERE close_min > 1440)
      INTO v_prev_has;

    -- 1. 전날 시작해 자정을 넘긴 구간에 먼저 맞춰 본다 (기능정의서 F-01 처리 1).
    --    새벽 한 시의 방문은 그날 규칙이 아니라 전날 영업의 연장이다.
    IF v_prev_has AND NOT dining.is_closed_on(p_place_uid, v_prev) THEN
        IF EXISTS (
            SELECT 1
            FROM dining.day_intervals(p_place_uid, v_prev) d
            WHERE d.close_min > 1440
              AND v_start_min + 1440 >= d.open_min
              AND v_end_min   + 1440 <= d.close_min
              AND (d.last_order_state <> 'present'
                   OR v_start_min + 1440 <= d.last_order_min)
        ) THEN
            RETURN true;
        END IF;
    END IF;

    -- 2. 그날이 휴무면 거짓이다.
    IF dining.is_closed_on(p_place_uid, v_date) THEN
        RETURN false;
    END IF;

    -- 3. 그날 구간을 하나도 모르면 모름이다. 없음으로 바꾸지 않는다 (DN-C5).
    IF NOT v_today_has THEN
        RETURN NULL;
    END IF;

    RETURN EXISTS (
        SELECT 1
        FROM dining.day_intervals(p_place_uid, v_date) d
        WHERE v_start_min >= d.open_min
          AND v_end_min   <= d.close_min
          AND (d.last_order_state <> 'present'
               OR v_start_min <= d.last_order_min)
    );
END;
$$;

COMMENT ON FUNCTION dining.open_at_slot IS
    '코어 places.open_at_slot 에 넣을 값. 모르면 NULL 을 돌려주고 거짓으로 바꾸지 않는다. 전날 자정을 넘긴 구간을 먼저 본다.';


-- 종료 임박 경고
-- 라스트오더 값이 없는 구간에서 식사 종료가 영업 종료 60 분 안쪽이면 참이다.
-- 실측 근거: 라스트오더를 표기한 77 곳의 종료와 라스트오더 간격 중앙값 50 분, 60 분이 85.7% 를 덮는다.
CREATE OR REPLACE FUNCTION dining.needs_last_order_check(
    p_place_uid uuid,
    p_starts_at timestamptz,
    p_ends_at   timestamptz DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_date      date;
    v_start_min int;
    v_end_min   int;
BEGIN
    v_date      := (p_starts_at AT TIME ZONE 'Asia/Seoul')::date;
    v_start_min := EXTRACT(HOUR   FROM p_starts_at AT TIME ZONE 'Asia/Seoul')::int * 60
                 + EXTRACT(MINUTE FROM p_starts_at AT TIME ZONE 'Asia/Seoul')::int;
    v_end_min   := CASE WHEN p_ends_at IS NULL THEN v_start_min
                        ELSE v_start_min + (EXTRACT(EPOCH FROM (p_ends_at - p_starts_at)) / 60)::int
                   END;

    RETURN EXISTS (
        SELECT 1
        FROM dining.day_intervals(p_place_uid, v_date) d
        WHERE d.last_order_state <> 'present'
          AND v_start_min >= d.open_min
          AND v_end_min   <= d.close_min
          AND d.close_min - v_end_min <= 60
    )
    OR EXISTS (
        SELECT 1
        FROM dining.day_intervals(p_place_uid, v_date - 1) d
        WHERE d.close_min > 1440
          AND d.last_order_state <> 'present'
          AND v_start_min + 1440 >= d.open_min
          AND v_end_min   + 1440 <= d.close_min
          AND d.close_min - (v_end_min + 1440) <= 60
    );
END;
$$;


-- 코어로 내보내는 한 줄인 dining.core_place_state 는 여기서 만들지 않는다.
-- 명절 경고 칸이 늘어나면서 돌려주는 모양이 바뀌었고, 그 함수가 쓰는
-- needs_holiday_check 는 023 에서 만들어진다. 한 함수를 두 파일이 서로 다르게
-- 정의하면 마이그레이션을 다시 돌릴 때 반환 형이 충돌한다. 정의는 023 한 곳에만 둔다.
