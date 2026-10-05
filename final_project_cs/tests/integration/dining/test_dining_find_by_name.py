"""일정 접수의 장소 찾기가 액티비티 CSV 에서 식당을 못 찾을 때 원장에서 찾는다(2026-10-01).

일정 접수는 액티비티 CSV(관광지 1,586곳)만 봐서 「토속촌삼계탕」 같은 식당을 하나도 못 찾았다.
원장의 관광공사 가게를 같은 모양(관광공사 결과)으로 돌려준다 — 외부 API 를 부르지 않는다.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.modules.travel_ops.dining.ledger import find_place_by_name


@pytest.fixture
def shop(conn, place):
    """관광공사 출처 가게 하나. 이름은 고유하게 붙이고 원문 행을 단다."""
    loads: list[str] = []

    def make(name: str, *, lat=37.5777, lng=126.9715, address="서울특별시 종로구 자하문로5길 5",
             content_id: str | None = None, status: str = "active") -> tuple[str, str]:
        uid = place(name, lat, lng)
        conn.execute("UPDATE dining.dn_place SET name_ko = %s, road_address = %s, record_status = %s, "
                     "is_synthetic = false WHERE place_uid = %s", (name, address, status, uid))
        cid = content_id or f"t{uuid.uuid4().hex[:10]}"
        load_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
            "row_count, status) VALUES (%s, 'tourapi_kor_food', %s, 'test', 'test', 1, 'loaded')",
            (load_id, datetime.now()))
        conn.execute(
            "INSERT INTO dining.dn_source_record (load_id, source_code, external_id, place_uid, match_status, "
            "raw_json) VALUES (%s, 'tourapi_kor_food', %s, %s, 'auto', '{}'::jsonb)", (load_id, cid, uid))
        loads.append(load_id)
        return uid, cid

    yield make
    for load_id in loads:
        conn.execute("DELETE FROM dining.dn_source_record WHERE load_id = %s", (load_id,))
        conn.execute("DELETE FROM dining.dn_load_meta WHERE load_id = %s", (load_id,))


def _name() -> str:
    return f"시험삼계탕{uuid.uuid4().hex[:6]}"


def test_finds_tourapi_shop_in_the_tour_result_shape(conn, shop):
    name = _name()
    _, cid = shop(name)

    found = find_place_by_name(conn, name)

    assert found == {"content_id": cid, "content_type_id": "39", "matched_title": name,
                     "latitude": pytest.approx(37.5777), "longitude": pytest.approx(126.9715),
                     "address": "서울특별시 종로구 자하문로5길 5"}


def test_name_match_ignores_spaces_and_punctuation(conn, shop):
    name = _name()
    shop(name)

    assert find_place_by_name(conn, f" {name[:3]} {name[3:]} ")["matched_title"] == name


def test_unknown_name_is_none(conn):
    assert find_place_by_name(conn, _name()) is None


def test_two_shops_with_the_same_name_are_not_guessed(conn, shop):
    name = _name()
    shop(name)
    shop(name, lat=37.50, lng=127.04)

    assert find_place_by_name(conn, name) is None


def test_closed_shop_is_not_offered(conn, shop):
    name = _name()
    shop(name, status="closed")

    assert find_place_by_name(conn, name) is None


def test_shop_outside_seoul_is_not_offered(conn, shop):
    name = _name()
    shop(name, address="경기도 고양시 덕양구 1")

    assert find_place_by_name(conn, name) is None


def test_shop_without_tourapi_record_is_not_offered(conn, place):
    # 관광공사 콘텐츠 ID 가 없으면 관광공사 결과로 내보낼 수 없다(미쉐린만 있는 가게 등).
    name = _name()
    uid = place(name)
    conn.execute("UPDATE dining.dn_place SET name_ko = %s, road_address = '서울특별시 중구 1', "
                 "is_synthetic = false WHERE place_uid = %s", (name, uid))

    assert find_place_by_name(conn, name) is None


def test_missing_dining_schema_is_none_not_an_error():
    class Broken:
        def cursor(self):
            raise RuntimeError('relation "dining.dn_place" does not exist')

        def transaction(self):
            import contextlib
            return contextlib.nullcontext()

    assert find_place_by_name(Broken(), "토속촌삼계탕") is None
