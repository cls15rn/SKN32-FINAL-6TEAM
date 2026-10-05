"""등록 판정이 쓰는 원장 판정 — 관광공사 ID 와 방문 시각으로 「그 시각에 여는가」. `[2026-10-02]`

일정 접수는 원장에서 찾은 식당을 관광공사 ID 와 함께 넘긴다(`source_content_id`). 등록할 때는 아직 코어 장소가 없어서
`dining_state`(코어 place_id 로 찾는다)를 쓸 수 없다. 그 ID 로 원장 가게를 바로 찾아 `dining.open_at_slot` 으로 본다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.modules.travel_ops.dining.ledger import slot_verdicts

KST = timezone(timedelta(hours=9))
MONDAY = date(2026, 9, 21)


def at(hhmm: str, day_offset: int = 0) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(MONDAY + timedelta(days=day_offset), datetime.min.time(),
                            tzinfo=KST).replace(hour=hour, minute=minute)


@pytest.fixture
def shop(conn, place):
    """관광공사 출처 가게 하나 — 월요일 11:00~15:00 · 17:00~21:00 영업(브레이크 15~17)."""
    loads: list[str] = []

    def make(*, status: str = "active", hours: bool = True) -> str:
        uid = place("시험삼계탕")
        conn.execute("UPDATE dining.dn_place SET record_status = %s WHERE place_uid = %s", (status, uid))
        cid = f"t{uuid.uuid4().hex[:10]}"
        load_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
            "row_count, status) VALUES (%s, 'tourapi_kor_food', %s, 'test', 'test', 1, 'loaded')",
            (load_id, datetime.now()))
        conn.execute(
            "INSERT INTO dining.dn_source_record (load_id, source_code, external_id, place_uid, match_status, "
            "raw_json) VALUES (%s, 'tourapi_kor_food', %s, %s, 'auto', '{}'::jsonb)", (load_id, cid, uid))
        loads.append(load_id)
        if hours:
            rule_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO dining.dn_hours_rule (rule_id, place_uid, source_code, entered_by, verified_at, "
                "rule_kind, weekday, coverage, break_state, extract_method, rules_version, valid_from) VALUES "
                "(%s, %s, 'synthetic_scenario', 'test', now(), 'weekly', 1, 'intervals', 'present', 'synthetic', "
                "'test', %s)", (rule_id, uid, MONDAY - timedelta(days=30)))
            for seq, (opens, closes) in enumerate(((11 * 60, 15 * 60), (17 * 60, 21 * 60)), 1):
                conn.execute(
                    "INSERT INTO dining.dn_hours_interval (rule_id, seq, open_min, close_min, last_order_min, "
                    "last_order_state) VALUES (%s, %s, %s, %s, NULL, 'unknown')", (rule_id, seq, opens, closes))
        return cid

    yield make
    for load_id in loads:
        conn.execute("DELETE FROM dining.dn_source_record WHERE load_id = %s", (load_id,))
        conn.execute("DELETE FROM dining.dn_load_meta WHERE load_id = %s", (load_id,))


def _one(conn, cid, start, end=None):
    return slot_verdicts(conn, [{"seq": 1, "content_id": cid, "at": start, "until": end}]).get(1)


def test_open_hours_are_open(conn, shop):
    assert _one(conn, shop(), at("12:00"), at("13:00")) == {"open_at_slot": True, "closed": False}


def test_after_closing_is_closed(conn, shop):
    assert _one(conn, shop(), at("21:30"), at("22:30")) == {"open_at_slot": False, "closed": False}


def test_inside_the_break_is_closed(conn, shop):
    assert _one(conn, shop(), at("15:30"), at("16:30"))["open_at_slot"] is False


def test_a_day_without_a_rule_is_unknown_not_closed(conn, shop):
    assert _one(conn, shop(), at("12:00", day_offset=1))["open_at_slot"] is None    # 화요일 규칙 없음


def test_a_shut_down_place_is_closed_and_says_so(conn, shop):
    assert _one(conn, shop(status="closed"), at("12:00")) == {"open_at_slot": False, "closed": True}


def test_a_tour_id_the_ledger_does_not_have_is_left_out(conn):
    assert slot_verdicts(conn, [{"seq": 1, "content_id": "t-없는-아이디", "at": at("12:00"), "until": None}]) == {}


# ── 원장 가게 ID 로 — 관광공사 ID 가 없는 가게도 본다 ───────────────
def _uid_of(conn, cid):
    return str(conn.execute("SELECT place_uid FROM dining.dn_source_record WHERE external_id = %s",
                            (cid,)).fetchone()[0])


def test_the_ledger_id_is_judged_the_same_way(conn, shop):
    uid = _uid_of(conn, shop())
    got = slot_verdicts(conn, [{"seq": 1, "place_uid": uid, "at": at("12:00"), "until": at("13:00")},
                               {"seq": 2, "place_uid": uid, "at": at("15:30"), "until": at("16:30")}])
    assert got == {1: {"open_at_slot": True, "closed": False}, 2: {"open_at_slot": False, "closed": False}}


def test_a_shop_without_a_tour_id_is_judged_by_its_ledger_id(conn, place):
    uid = place("비건키친")                                  # 관광공사 원문 행이 없다
    assert slot_verdicts(conn, [{"seq": 1, "place_uid": uid, "at": at("12:00"), "until": None}]) == {
        1: {"open_at_slot": None, "closed": False}}          # 규칙이 없으니 모름 — 그래도 원장 가게다


def test_an_unknown_ledger_id_is_left_out(conn):
    assert slot_verdicts(conn, [{"seq": 1, "place_uid": str(uuid.uuid4()), "at": at("12:00"), "until": None}]) == {}


# ── 생성기가 쓰는 것 ────────────────────────────────────────────────
def test_planner_shops_are_every_open_real_shop_with_coordinates(conn, shop, place):
    from app.modules.travel_ops.dining.ledger import planner_shops

    with_tour = _uid_of(conn, cid := shop())
    no_tour = place("비건키친")
    shut = _uid_of(conn, shop(status="closed"))
    fake = place("합성식당")
    no_coord = place("좌표없음")
    conn.execute("UPDATE dining.dn_place SET is_synthetic = false, road_address = '서울특별시 종로구 1' "
                 "WHERE place_uid = ANY(%s::uuid[])", ([with_tour, no_tour, shut, no_coord],))
    conn.execute("UPDATE dining.dn_place SET lat = NULL, lng = NULL WHERE place_uid = %s", (no_coord,))

    got = {row["place_uid"]: row for row in planner_shops(conn)}
    assert with_tour in got and no_tour in got
    assert shut not in got and fake not in got and no_coord not in got
    assert got[with_tour]["content_id"] == cid and got[no_tour]["content_id"] is None
    assert got[no_tour]["address"] == "서울특별시 종로구 1"


def test_open_among_answers_for_each_shop_at_that_time(conn, shop, place):
    from app.modules.travel_ops.dining.ledger import open_among

    open_ = _uid_of(conn, shop())
    unknown = place("규칙없는집")
    assert open_among(conn, [open_, unknown], at("12:00"), at("13:00")) == {open_: True, unknown: None}
    assert open_among(conn, [open_], at("15:30"), at("16:30")) == {open_: False}
    assert open_among(conn, [], at("12:00"), None) == {}
