"""대체 식당 후보를 원장에서 가져온다(2026-10-01).

대체 계산은 코어 `places`(공용 + 그 여행 전용)에서만 후보를 찾았다. 우리 DB 의 공용 식당은 1곳뿐이라 「문 닫았어요」에
후보가 0개였다 — 원장에는 반경 700m 안에 36곳이 있었는데. 계산 직전에 근처 원장 가게를 **그 여행 전용 장소**로
들여놓고 원장과 바로 잇는다. 공용 표는 건드리지 않는다(일정 접수 · 다른 여행에 영향이 없다).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.travel_ops.dining.ledger import dining_state, dining_states, nearby_shops
from app.modules.travel_ops.dining.nearby import add_nearby
from app.modules.travel_ops.itinerary import Item, TripStore
from app.modules.travel_ops.itinerary_changes import NoChange, plan_closed

from tests.integration.dining.conftest import MIGRATIONS
from tests.integration.dining.test_dining_judgment import add_hours
from tests.integration.dining.test_dining_runtime_link import PLACES

KST = timezone(timedelta(hours=9))
HERE = (37.5777, 126.9715)                   # 토속촌 근처


@pytest.fixture(scope="module")
def core(conn):
    conn.execute(PLACES)
    conn.execute("ALTER TABLE places ADD COLUMN IF NOT EXISTS weather_sensitive boolean")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS places_trip_name_kind_uq "
                 "ON places (tenant_id, trip_scope, name, kind) WHERE trip_scope IS NOT NULL")
    conn.execute(open(os.path.join(MIGRATIONS, "202_dining_matcher.sql"), encoding="utf-8").read())
    # 판정 때 잇기(220)는 아직 안 이어진 장소에서만 돈다. 들여놓은 장소는 이미 이어져 있지만 같은 DB 를 맞춘다
    conn.execute(open(os.path.join(MIGRATIONS, "220_dining_runtime_link.sql"), encoding="utf-8").read())
    return conn


@pytest.fixture
def world(core, place):
    """시험마다 테넌트 하나와 원장 가게들. 끝나면 코어 행 · 연결을 지운다."""
    tenant = f"nearby-{uuid.uuid4().hex}"
    loads: list[str] = []

    def shop(name: str, lat: float, lng: float, *, status="active", synthetic=False, content_id=None) -> str:
        uid = place(name, lat, lng)
        core.execute("UPDATE dining.dn_place SET name_ko = %s, record_status = %s, is_synthetic = %s, "
                     "road_address = '서울특별시 종로구 1' WHERE place_uid = %s", (name, status, synthetic, uid))
        if content_id:
            load_id = str(uuid.uuid4())
            core.execute("INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
                         "row_count, status) VALUES (%s, 'tourapi_kor_food', now(), 'test', 'test', 1, 'loaded')",
                         (load_id,))
            core.execute("INSERT INTO dining.dn_source_record (load_id, source_code, external_id, place_uid, "
                         "match_status, raw_json) VALUES (%s, 'tourapi_kor_food', %s, %s, 'auto', '{}'::jsonb)",
                         (load_id, content_id, uid))
            loads.append(load_id)
        return uid

    yield tenant, shop
    core.execute("DELETE FROM dining.dn_core_place_link WHERE tenant_id = %s", (tenant,))
    core.execute("DELETE FROM places WHERE tenant_id = %s", (tenant,))
    for load_id in loads:
        core.execute("DELETE FROM dining.dn_source_record WHERE load_id = %s", (load_id,))
        core.execute("DELETE FROM dining.dn_load_meta WHERE load_id = %s", (load_id,))


def _n(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def _meal(place: dict) -> Item:
    at = datetime(2026, 10, 2, 12, 0, tzinfo=KST)
    return Item(item_id=uuid.uuid4(), seq=1, kind="dining", title=place["name"],
                place_id=uuid.UUID(place["place_id"]), starts_at=at, ends_at=at + timedelta(hours=1), place=place)


# ── 원장 쪽: 근처 가게 읽기 ─────────────────────────────────────────
def test_nearby_shops_are_ordered_by_distance_with_the_tourapi_id(core, world):
    tenant, shop = world
    near, nearer = _n("가까운집"), _n("더가까운집")
    shop(near, 37.5790, 126.9715, content_id="c-near")            # 약 140m
    shop(nearer, 37.5780, 126.9715)                                 # 약 30m
    shop(_n("먼집"), 37.5900, 126.9715)                             # 약 1.4km

    got = nearby_shops(core, tenant, [HERE], radius_m=700, limit=10)
    names = [s["name"] for s in got]

    assert names.index(nearer) < names.index(near)
    assert next(s for s in got if s["name"] == near)["content_id"] == "c-near"
    assert all(s["distance_m"] <= 700 for s in got)


def test_closed_and_synthetic_shops_are_not_offered(core, world):
    tenant, shop = world
    closed, fake = _n("폐업집"), _n("합성집")
    shop(closed, 37.5778, 126.9716, status="closed")
    shop(fake, 37.5778, 126.9716, synthetic=True)

    names = {s["name"] for s in nearby_shops(core, tenant, [HERE], radius_m=700, limit=50)}

    assert closed not in names and fake not in names


def test_a_shop_already_linked_to_a_visible_place_is_not_offered_again(core, world):
    tenant, shop = world
    name = _n("이미있는집")
    uid = shop(name, 37.5778, 126.9716)
    core_id = str(uuid.uuid4())
    core.execute("INSERT INTO dining.dn_core_place_link (tenant_id, core_place_id, place_uid, linked_by) "
                 "VALUES (%s, %s, %s, 'test')", (tenant, core_id, uid))

    names = {s["name"] for s in nearby_shops(core, tenant, [HERE], radius_m=700, limit=50,
                                             visible_core_ids=[core_id])}

    assert name not in names


# ── 들여놓기: 그 여행 전용 장소 + 원장 연결 ─────────────────────────
def test_nearby_shops_become_this_trips_places_and_are_judged_by_the_ledger(core, world):
    tenant, shop = world
    name = _n("대체식당")
    uid = shop(name, 37.5780, 126.9716, content_id="c-alt")
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = {"place_id": str(uuid.uuid4()), "name": "토속촌삼계탕", "kind": "dining",
                "latitude": HERE[0], "longitude": HERE[1]}

    places = add_nearby(core, store, trip_id, [_meal(original)], [original])

    added = next(p for p in places if p["name"] == name)
    assert added["kind"] == "dining" and added["trip_scope"] == str(trip_id)
    assert added["attributes"] == {"source": "dining_ledger", "source_content_id": "c-alt", "dining_place_uid": uid}
    # 공용 표에는 넣지 않는다
    assert core.execute("SELECT count(*) FROM places WHERE tenant_id = %s AND trip_scope IS NULL",
                        (tenant,)).fetchone()[0] == 0
    # 원장과 이미 이어져 있어 판정이 바로 원장으로 간다
    assert dining_state(core, tenant, added["place_id"], "2026-10-02T12:00:00+09:00")["linked"] is True


def test_adding_twice_does_not_duplicate(core, world):
    tenant, shop = world
    name = _n("한번만")
    shop(name, 37.5780, 126.9716)
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = {"place_id": str(uuid.uuid4()), "name": "토속촌삼계탕", "kind": "dining",
                "latitude": HERE[0], "longitude": HERE[1]}

    first = add_nearby(core, store, trip_id, [_meal(original)], [original])
    second = add_nearby(core, store, trip_id, [_meal(original)], first)

    assert [p["name"] for p in second].count(name) == 1
    assert core.execute("SELECT count(*) FROM places WHERE tenant_id = %s AND name = %s",
                        (tenant, name)).fetchone()[0] == 1


def test_a_shop_with_the_same_name_as_a_visible_place_is_skipped(core, world):
    tenant, shop = world
    name = _n("같은이름")
    shop(name, 37.5780, 126.9716)
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = {"place_id": str(uuid.uuid4()), "name": name, "kind": "dining",
                "latitude": HERE[0], "longitude": HERE[1]}

    places = add_nearby(core, store, trip_id, [_meal(original)], [original])

    assert [p["name"] for p in places].count(name) == 1


def test_without_a_meal_nothing_is_added(core, world):
    tenant, shop = world
    shop(_n("근처집"), 37.5780, 126.9716)
    store, trip_id = TripStore(tenant), uuid.uuid4()
    activity = {"place_id": str(uuid.uuid4()), "name": "경복궁", "kind": "activity",
                "latitude": HERE[0], "longitude": HERE[1]}
    item = _meal(activity)
    item.kind = "activity"

    assert add_nearby(core, store, trip_id, [item], [activity]) == [activity]


def test_an_unlinked_original_with_a_different_ledger_name_is_not_imported_as_its_own_alternative(core, world):
    tenant, shop = world
    canonical = _n("원장 식당")
    shop(canonical, *HERE, content_id="same-restaurant")
    real_alternative = _n("실제 대체 식당")
    shop(real_alternative, HERE[0] + .0001, HERE[1])
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = store.adopt_places(core, trip_id, [{
        "name": canonical + " 본점", "kind": "dining", "latitude": HERE[0], "longitude": HERE[1],
        "attributes": {"source": "tour_api", "source_content_id": "same-restaurant"},
    }])[0]
    assert core.execute("SELECT count(*) FROM dining.dn_core_place_link WHERE tenant_id = %s",
                        (tenant,)).fetchone()[0] == 0

    places = add_nearby(core, store, trip_id, [_meal(original)], [original])

    assert canonical not in {p["name"] for p in places}
    assert real_alternative in {p["name"] for p in places}


def test_same_named_shops_link_the_actual_persisted_coordinates_and_uid(core, world):
    tenant, shop = world
    name = _n("동명 식당")
    near_uid = shop(name, HERE[0] + .0001, HERE[1])
    shop(name, HERE[0] + .0005, HERE[1])
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = {"place_id": str(uuid.uuid4()), "name": "원래 식당", "kind": "dining",
                "latitude": HERE[0], "longitude": HERE[1]}

    places = add_nearby(core, store, trip_id, [_meal(original)], [original])

    adopted = next(p for p in places if p["name"] == name)
    linked_uid = core.execute("SELECT place_uid::text FROM dining.dn_core_place_link "
                              "WHERE tenant_id = %s AND core_place_id = %s",
                              (tenant, adopted["place_id"])).fetchone()[0]
    assert linked_uid == adopted["attributes"]["dining_place_uid"] == near_uid
    assert adopted["latitude"] == pytest.approx(HERE[0] + .0001)


def test_an_already_visible_alias_of_the_matched_original_is_removed_even_without_new_shops(core, world):
    tenant, shop = world
    canonical = _n("OriginalRestaurant")
    uid = shop(canonical, *HERE)
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original, alias = [store.adopt_places(core, trip_id, [row])[0] for row in [
        {"name": canonical.replace("OriginalRestaurant", "Original Restaurant"), "kind": "dining",
         "latitude": HERE[0], "longitude": HERE[1], "attributes": {}},
        {"name": canonical, "kind": "dining", "latitude": HERE[0], "longitude": HERE[1],
         "attributes": {"source": "dining_ledger", "dining_place_uid": uid}},
    ]]
    # 일정 항목과 후보 목록은 DB에서 따로 읽은 dict다.
    item = _meal({**original, "attributes": {}})

    places = add_nearby(core, store, trip_id, [item], [original, alias])

    assert places == [original]


def test_the_first_request_can_choose_the_31st_shop_when_the_first_30_are_closed_at_arrival(core, world):
    tenant, shop = world
    for index in range(30):
        uid = shop(_n("점심에는 닫힌 식당"), HERE[0] + .00001 * (index + 1), HERE[1])
        add_hours(core, uid, 5, [(840, 1200, None)])
    valid_name = _n("점심 영업 식당")
    valid_uid = shop(valid_name, HERE[0] + .003, HERE[1])
    add_hours(core, valid_uid, 5, [(600, 1200, None)])
    store, trip_id = TripStore(tenant), uuid.uuid4()
    original = {"place_id": str(uuid.uuid4()), "name": "원래 식당", "kind": "dining",
                "latitude": HERE[0], "longitude": HERE[1]}
    item = _meal(original)

    places = add_nearby(core, store, trip_id, [item], [original])
    plan = plan_closed(trip={}, items=[item], places=places, at=item.starts_at,
                       message="휴무", request_id=None,
                       state_lookup=lambda slots: dining_states(core, tenant, slots))

    assert not isinstance(plan, NoChange)
    assert plan.replacements[item.item_id].place["name"] == valid_name
