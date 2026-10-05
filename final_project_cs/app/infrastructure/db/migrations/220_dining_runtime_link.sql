-- 판정할 때 코어 장소를 원장 가게와 잇는다(2026-10-01).
--
-- 왜 필요한가.
--   연결(dn_core_place_link)은 rebuild.py 가 link_core_places 를 부를 때 한 번만 만들어졌다.
--   여행 장소는 등록할 때마다 places 에 새로 생기므로(029 · 여행 전용 행), 원장을 세운 뒤의 장소는
--   영영 이어지지 않았고 판정은 늘 「모름」이었다. 그래서 판정하는 순간, 아직 없으면 이어 본다.
--
-- 어떻게 잇는가.
--   1  관광공사 콘텐츠 ID 가 같다 — 계획 읽기가 관광공사로 찾은 장소는 그 ID 를 남긴다
--      (places.source_content_id 또는 attributes.source_content_id). 원장의 관광공사 가게도 같은 ID 를
--      dn_source_record.external_id 로 갖는다. 같은 가게이므로 이름 · 거리를 보지 않는다.
--      한 가게로만 정해질 때만 잇는다.
--   2  ID 가 없으면 매칭기(202)와 같은 규칙 — 식당(kind='dining')이고 80m 안, 이름이 같거나
--      유사도 0.75 이상인 후보가 하나뿐일 때만. 둘 이상이면 잇지 않는다(다른 식당의 영업시간으로
--      판정하게 된다).
--   잇지 못하면 NULL. 판정은 지금처럼 「모름」이다.
--
-- 코어 표를 읽기만 한다. 쓰는 곳은 dining.dn_core_place_link 뿐이다(202 와 같은 경계).

CREATE OR REPLACE FUNCTION dining.link_core_place(p_tenant_id text, p_core_place_id uuid)
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_uid     uuid;
    v_cid     text;
    v_by      text;
    v_n       int;
BEGIN
    SELECT l.place_uid INTO v_uid
    FROM dining.dn_core_place_link l
    WHERE l.tenant_id = p_tenant_id AND l.core_place_id = p_core_place_id;
    IF v_uid IS NOT NULL THEN
        RETURN v_uid;
    END IF;

    SELECT coalesce(p.source_content_id, p.attributes->>'source_content_id') INTO v_cid
    FROM places p
    WHERE p.tenant_id = p_tenant_id AND p.place_id = p_core_place_id;

    IF v_cid IS NOT NULL THEN
        SELECT count(DISTINCT s.place_uid), min(s.place_uid::text)::uuid INTO v_n, v_uid
        FROM dining.dn_source_record s
        WHERE s.source_code = 'tourapi_kor_food' AND s.external_id = v_cid;
        IF v_n = 1 THEN
            v_by := 'runtime:content_id';
        ELSE
            v_uid := NULL;
        END IF;
    END IF;

    IF v_uid IS NULL THEN
        SELECT count(*), min(v.place_uid::text)::uuid INTO v_n, v_uid
        FROM dining.v_link_candidate v
        WHERE v.tenant_id = p_tenant_id AND v.core_place_id = p_core_place_id
          AND (v.name_exact OR v.name_sim >= 0.75);
        IF v_n = 1 THEN
            v_by := 'runtime:matcher';
        ELSE
            RETURN NULL;
        END IF;
    END IF;

    INSERT INTO dining.dn_core_place_link (tenant_id, core_place_id, place_uid, linked_by)
    VALUES (p_tenant_id, p_core_place_id, v_uid, v_by)
    ON CONFLICT ON CONSTRAINT dn_core_place_link_pkey DO NOTHING;

    SELECT l.place_uid INTO v_uid
    FROM dining.dn_core_place_link l
    WHERE l.tenant_id = p_tenant_id AND l.core_place_id = p_core_place_id;
    RETURN v_uid;
END;
$$;

COMMENT ON FUNCTION dining.link_core_place IS
    '코어 장소 하나를 원장 가게와 잇는다. 이미 이어져 있으면 그 가게, 관광공사 콘텐츠 ID 가 같은 가게가 하나면 그 가게, '
    '아니면 매칭기 규칙(80m · 이름)으로 하나만 정해질 때 그 가게. 못 정하면 NULL 이고 아무것도 쓰지 않는다.';
