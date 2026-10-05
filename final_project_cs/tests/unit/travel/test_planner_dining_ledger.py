# -*- coding: utf-8 -*-
"""일정 생성기의 식당은 요식 원장에서 고르고 원장으로 판정한다. `[2026-10-02]`

★사용자 결정 — 식당은 관광공사 API 를 실시간으로 부르지 않는다. 일반 판정 · 비교는 DB 에 적재된 원장으로만,
  실시간 비교가 필요하면 구글이다. 관광공사는 긴 주기 갱신 때만 쓴다.
★전에는 생성기가 식당 후보를 관광공사 카탈로그 · 실시간 조회(`fill_from_tour_api`)에서 받고 영업시간도 관광공사
  원문을 실시간으로 읽었다(`enrich_hours`). 등록은 원장으로 판정하니 생성기가 통과시킨 초안이 등록에서 걸렸다.
★DB 없이 돈다 — 원장은 흉내(`FakeLedger`)를 넘긴다. 실제 SQL 은 `tests/integration/dining` 이 본다.
"""
from __future__ import annotations

from datetime import date, datetime, time

from app.modules.travel_ops import planner
from app.modules.travel_ops.planner import Cand, PlanRequest

KST = planner.KST
DAY = date(2026, 10, 5)


class FakeLedger:
    """원장 흉내 — 가게마다 「그 시각에 여는가」를 정해 둔다. 시각은 보지 않는다(시험이 정한 값이 답이다)."""

    def __init__(self, shops=(), states=None):
        self._shops, self.states = list(shops), dict(states or {})
        self.asked: list = []

    def shops(self):
        return list(self._shops)

    def verdicts(self, slots):
        self.asked.append(slots)
        return {slot["seq"]: {"open_at_slot": self.states.get(slot.get("place_uid")), "closed": False}
                for slot in slots if slot.get("place_uid")}

    def open_among(self, uids, at, until):
        return {uid: self.states.get(uid) for uid in uids}


def _act(name, **attributes):
    return Cand(key=f"k_{name}", name=name, kind="activity", lat=37.575, lon=126.977,
                attributes={"hours": ["09:00", "18:00"], "district": "종로구", **attributes},
                origin="places", rank_hint=0)


def _shop(uid, name, **attributes):
    return Cand(key=f"dn_{uid}", name=name, kind="dining", lat=37.5752, lon=126.9775,
                attributes={"source": "dining_ledger", "dining_place_uid": uid, "district": "종로구", **attributes},
                origin="dining_ledger", rank_hint=0)


REQUEST = PlanRequest(city="서울", start_date=DAY, days=1, party_size=2)


# ── 관광공사에서 식당을 받지 않는다 ─────────────────────────────────
def test_restaurants_are_not_taken_from_the_live_tour_api():
    class Source:
        def area_page(self, area_code, page, rows):
            return {"items": [
                {"content_id": "1", "content_type_id": "12", "title": "고궁", "latitude": 37.5, "longitude": 127.0},
                {"content_id": "2", "content_type_id": "39", "title": "삼계탕집", "latitude": 37.5, "longitude": 127.0}]}

    filled, calls = planner.fill_from_tour_api([], source=Source())
    assert [(cand.name, cand.kind) for cand in filled] == [("고궁", "activity")]


def test_restaurants_are_not_read_from_the_place_catalog(monkeypatch):
    from app.infrastructure.travel import catalog_sync

    monkeypatch.setattr(catalog_sync.PlaceCatalogSync, "enabled", staticmethod(lambda: True))
    seen = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            seen.append(params)

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

    planner.load_candidates(Conn(), tenant_id="t")
    catalog_types = seen[-1][1]                       # place_catalog 의 content_type_id = ANY(%s)
    assert "39" not in catalog_types and "12" in catalog_types


def test_restaurant_hours_are_not_read_from_the_tour_api():
    palace = Cand(key="k_고궁", name="고궁", kind="activity", lat=37.575, lon=126.977,
                  attributes={"source": "tour_api", "source_content_id": "1"}, origin="place_catalog")
    stats = planner.enrich_hours([palace, _shop("u1", "삼계탕집", source_content_id="2"),
                                  _shop("u2", "만두집", source_content_id="3")],
                                 source=None, chat=None, now=datetime.now(KST))
    assert stats["asked"] == 1                        # 활동 하나만 — 식당은 묻지 않는다


# ── 원장 가게가 후보가 된다 ─────────────────────────────────────────
def test_ledger_shops_become_dining_candidates_keyed_by_the_ledger_id():
    cands = planner.ledger_candidates([
        {"place_uid": "u1", "name": "토속촌삼계탕", "lat": 37.578, "lng": 126.971,
         "address": "서울특별시 종로구 자하문로5길 5", "content_id": "123"},
        {"place_uid": "u2", "name": "비건 키친", "lat": 37.55, "lng": 126.92,
         "address": "서울특별시 마포구 와우산로 1", "content_id": None},
        {"place_uid": "u3", "name": "좌표 없는 집", "lat": None, "lng": None, "address": None, "content_id": None}])
    assert [(c.key, c.kind, c.origin, c.district) for c in cands] == [
        ("dn_u1", "dining", "dining_ledger", "종로구"), ("dn_u2", "dining", "dining_ledger", "마포구")]
    assert cands[0].attributes["dining_place_uid"] == "u1" and cands[0].attributes["source"] == "dining_ledger"
    assert cands[0].attributes["source_content_id"] == "123"
    assert "source_content_id" not in cands[1].attributes      # 관광공사 ID 가 없는 가게도 후보다


# ── 원장으로 판정하고 고친다 ────────────────────────────────────────
def _day(*shops):
    return planner.build_day(DAY, [_act("고궁")], list(shops), seq_from=1)


def test_the_planner_check_sees_the_ledger():
    shut = _shop("u_shut", "닫힌 집")
    items = _day(shut)
    ledger = FakeLedger(states={"u_shut": False})
    found = planner._check(items, {c.key: c for c in (_act("고궁"), shut)}, REQUEST, ledger=ledger)
    assert [v.code for v in found] == ["dining_closed_at_slot"]


def test_a_meal_the_ledger_says_is_closed_is_swapped_for_one_the_ledger_says_is_open():
    shut, unknown, open_ = _shop("u_shut", "닫힌 집"), _shop("u_unknown", "모르는 집"), _shop("u_open", "여는 집")
    ledger = FakeLedger(states={"u_shut": False, "u_unknown": None, "u_open": True})
    items = _day(shut)
    chosen = {c.key: c for c in (_act("고궁"), shut)}
    used = set(chosen)
    violations = planner._check(items, chosen, REQUEST, ledger=ledger)

    done = planner.repair(items, chosen, violations, spares=[unknown, open_], used=used,
                          constraints={}, party_size=2, ledger=ledger)

    meal = next(item for item in items if item["kind"] == "dining")
    assert meal["place"] == "dn_u_open"                         # 모르는 곳보다 연다고 확인된 곳이 먼저다
    assert done and "여는 집" in done[0]
    assert planner._check(items, chosen, REQUEST, ledger=ledger) == []


def test_when_no_spare_is_known_open_an_unknown_one_is_taken():
    shut, unknown = _shop("u_shut", "닫힌 집"), _shop("u_unknown", "모르는 집")
    ledger = FakeLedger(states={"u_shut": False, "u_unknown": None})
    items = _day(shut)
    chosen = {c.key: c for c in (_act("고궁"), shut)}
    violations = planner._check(items, chosen, REQUEST, ledger=ledger)
    planner.repair(items, chosen, violations, spares=[unknown], used=set(chosen),
                   constraints={}, party_size=2, ledger=ledger)
    assert next(item for item in items if item["kind"] == "dining")["place"] == "dn_u_unknown"


def test_a_closed_meal_with_only_closed_spares_is_not_pretended_fixed():
    shut, also = _shop("u_shut", "닫힌 집"), _shop("u_also", "또 닫힌 집")
    ledger = FakeLedger(states={"u_shut": False, "u_also": False})
    items = _day(shut)
    chosen = {c.key: c for c in (_act("고궁"), shut)}
    violations = planner._check(items, chosen, REQUEST, ledger=ledger)
    assert planner.repair(items, chosen, violations, spares=[also], used=set(chosen),
                          constraints={}, party_size=2, ledger=ledger) == []


def test_without_a_ledger_the_planner_checks_as_before():
    shut = _shop("u_shut", "닫힌 집")
    items = _day(shut)
    assert planner._check(items, {c.key: c for c in (_act("고궁"), shut)}, REQUEST) == []
    assert items[1]["starts_at"].time() == time(12, 0)
