# -*- coding: utf-8 -*-
"""식당은 요식 원장으로 — 일정 생성기가 원장에서 고르고, 등록이 원장으로 판정하고, 등록한 식당을 원장과 잇는다. `[2026-10-02]`

★사용자 결정 — 식당은 관광공사 API 를 실시간으로 부르지 않는다. 일반 판정 · 비교는 DB 에 적재된 원장으로만.
★이 파일은 자기 테넌트에 **활동만** 심고, 식당은 원장(`dining.dn_place`)에 직접 세운다 — 그래서 초안의 식당은
  원장에서만 나올 수 있다. 원장은 테넌트가 없는 표라 세운 가게는 끝나면 지운다.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.core.settings as settings_module
from app.infrastructure.db.session import get_connection
from app.modules.travel_ops.dining.ledger import slot_verdicts
from app.modules.travel_ops.trip_api import build_trip_router
from app.presentation import security
from app.presentation.api.app import create_app

START = date(2026, 10, 5)            # 월요일
KST_OFFSET = "+09:00"

ACTIVITIES = [
    ("가든 공원", 37.5771, 126.9801), ("고궁 뜰", 37.5758, 126.9768), ("하늘 박물관", 37.5750, 126.9770),
]
#: (이름, 여는 분, 닫는 분). ★생성기는 식당을 **첫 활동에서 가까운 순**으로 고른다(`_near_dining`) — 「가」를 첫 활동
#:   (가든 공원) 바로 옆에 세워 점심 칸에 먼저 들어가게 한다. 그런데 17시에 연다
SHOPS = [("가 저녁만 하는 집", 17 * 60, 22 * 60), ("나 종일 식당", 10 * 60, 22 * 60),
         ("다 종일 식당", 10 * 60, 22 * 60), ("라 종일 식당", 10 * 60, 22 * 60)]


def _seed_shop(cur, name: str, opens: int, closes: int, lat: float, lng: float) -> str:
    uid = str(uuid.uuid4())
    cur.execute("INSERT INTO dining.dn_place (place_uid, name_ko, area, record_status, is_synthetic, lat, lng, "
                "road_address) VALUES (%s, %s, '시험', 'active', false, %s, %s, '서울특별시 종로구 시험로 1')",
                (uid, f"{name} {uid[:6]}", lat, lng))
    for weekday in range(1, 8):
        rule = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO dining.dn_hours_rule (rule_id, place_uid, source_code, entered_by, verified_at, rule_kind, "
            "weekday, coverage, break_state, extract_method, rules_version, valid_from) VALUES "
            "(%s, %s, 'synthetic_scenario', 'test', now(), 'weekly', %s, 'intervals', 'none', 'synthetic', 'test', "
            "%s)", (rule, uid, weekday, START - timedelta(days=30)))
        cur.execute("INSERT INTO dining.dn_hours_interval (rule_id, seq, open_min, close_min, last_order_min, "
                    "last_order_state) VALUES (%s, 1, %s, %s, NULL, 'unknown')", (rule, opens, closes))
    return uid


@pytest.fixture()
def api(monkeypatch):
    original = settings_module.get_settings()
    tenant = "dnledger_" + uuid.uuid4().hex[:12]
    test_settings = original.model_copy(update={"tenant_id": tenant})
    monkeypatch.setattr(settings_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(security, "get_settings", lambda: test_settings)
    customer = uuid.uuid4()
    shops: dict[str, str] = {}
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dining.dn_place WHERE NOT is_synthetic AND record_status <> 'closed'")
        if cur.fetchone()[0]:
            pytest.skip("원장에 실제 가게가 있는 DB 다 — 시험이 세운 가게만으로 초안을 짤 수 없다(CI DB 에서 돈다)")
        cur.execute("INSERT INTO tenants (tenant_id,name) VALUES (%s,%s)", (tenant, "dining ledger"))
        cur.execute("INSERT INTO customers (customer_id,tenant_id,external_id) VALUES (%s,%s,%s)",
                    (customer, tenant, "dining-ledger-customer"))
        for name, lat, lon in ACTIVITIES:
            cur.execute("INSERT INTO places (tenant_id,name,kind,latitude,longitude,attributes) "
                        "VALUES (%s,%s,'activity',%s,%s,%s)",
                        (tenant, name, lat, lon, json.dumps({"district": "종로구", "hours": ["09:00", "18:00"]})))
        for index, (name, opens, closes) in enumerate(SHOPS):
            shops[name] = _seed_shop(cur, name, opens, closes, 37.5771 - index * 0.0004, 126.9801)

    client = TestClient(create_app(
        classifier=lambda _m: {"intent": "other", "issue_code": "other", "sentiment": "neutral"},
        domain_routers=[build_trip_router(check_factory=lambda: (lambda **_k: {"verdict": "clear"}))]))

    def auth(scope):
        return {"Authorization": "Bearer " + security._development_key(scope, original.secret_key)}

    yield {"client": client, "auth": auth, "tenant": tenant, "customer": customer, "shops": shops}

    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        uids = list(shops.values())
        cur.execute("DELETE FROM dining.dn_core_place_link WHERE place_uid = ANY(%s::uuid[])", (uids,))
        cur.execute("DELETE FROM dining.dn_hours_interval i USING dining.dn_hours_rule r "
                    "WHERE i.rule_id = r.rule_id AND r.place_uid = ANY(%s::uuid[])", (uids,))
        cur.execute("DELETE FROM dining.dn_hours_rule WHERE place_uid = ANY(%s::uuid[])", (uids,))
        cur.execute("DELETE FROM dining.dn_place WHERE place_uid = ANY(%s::uuid[])", (uids,))
        for sql in ("DELETE FROM itinerary_items WHERE tenant_id=%s", "DELETE FROM itinerary_versions WHERE tenant_id=%s",
                    "DELETE FROM outbox WHERE tenant_id=%s", "DELETE FROM trips WHERE tenant_id=%s",
                    "DELETE FROM places WHERE tenant_id=%s", "DELETE FROM customers WHERE tenant_id=%s",
                    "DELETE FROM tenants WHERE tenant_id=%s"):
            cur.execute(sql, (tenant,))


def _plan(api, **override):
    body = {"request_id": override.pop("request_id", "dn-plan-1"), "customer_id": str(api["customer"]),
            "city": "서울", "start_date": START.isoformat(), "days": 1, "party_size": 2, "locale": "ko",
            "constraints": {"breakfast": False}}
    body.update(override)
    return api["client"].post("/v1/trips/plan", json=body, headers=api["auth"]("trip:write"))


def _meals(draft):
    places = {place["key"]: place for place in draft["places"]}
    return [(item, places[item["place"]]) for item in draft["items"] if item["kind"] == "dining"]


# ── 생성기 ──────────────────────────────────────────────────────────
def test_the_planner_takes_meals_from_the_ledger_and_every_meal_is_open_then(api):
    response = _plan(api)
    assert response.status_code == 200, response.text
    meals = _meals(response.json()["draft"])
    assert len(meals) == 2
    assert all(place["attributes"]["source"] == "dining_ledger" for _, place in meals)
    assert {place["attributes"]["dining_place_uid"] for _, place in meals} <= set(api["shops"].values())
    with get_connection() as conn:
        got = slot_verdicts(conn, [{"seq": item["seq"], "place_uid": place["attributes"]["dining_place_uid"],
                                    "at": datetime.fromisoformat(item["starts_at"]),
                                    "until": datetime.fromisoformat(item["ends_at"])} for item, place in meals])
    assert all(verdict["open_at_slot"] is True for verdict in got.values()), got


def test_a_lunch_at_a_dinner_only_shop_is_swapped_by_the_ledger(api):
    """★가장 가까운 「가 저녁만 하는 집」이 점심 칸에 먼저 들어간다 — 원장이 닫혔다고 해서 바꾼다."""
    response = _plan(api)
    assert response.status_code == 200, response.text
    body = response.json()
    lunch = next(item for item, _ in _meals(body["draft"]) if item["starts_at"].startswith(f"{START}T12"))
    assert not lunch["title"].startswith("가 저녁만 하는 집")
    assert any("dining_closed_at_slot" in step for step in body["checks"]["repairs"]), body["checks"]


def test_registering_the_plan_links_its_meals_to_the_ledger(api):
    """★등록한 식당을 원장 가게와 잇는다 — 그래야 새벽 확인 · 하루 점검이 그 가게를 원장으로 본다."""
    response = _plan(api, register=True, request_id="dn-plan-reg")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "registered"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT l.place_uid::text FROM dining.dn_core_place_link l JOIN places p "
                    "ON p.place_id = l.core_place_id WHERE p.tenant_id = %s AND l.tenant_id = %s",
                    (api["tenant"], api["tenant"]))
        linked = {row[0] for row in cur.fetchall()}
    assert len(linked) == 2 and linked <= set(api["shops"].values())


# ── 등록 ────────────────────────────────────────────────────────────
def _register(api, uid: str, hhmm: str):
    starts = f"{START}T{hhmm}:00{KST_OFFSET}"
    ends = (datetime.fromisoformat(starts) + timedelta(hours=1)).isoformat()
    body = {"request_id": f"dn-reg-{hhmm}", "customer_id": str(api["customer"]), "title": "원장 식당 등록",
            "locale": "ko", "party_size": 2,
            "places": [{"key": "meal", "name": "원장 식당", "kind": "dining", "lat": 37.5752, "lon": 126.9776,
                        "attributes": {"source": "dining_ledger", "dining_place_uid": uid}}],
            "items": [{"seq": 1, "kind": "dining", "title": "점심", "place": "meal",
                       "starts_at": starts, "ends_at": ends}]}
    return api["client"].post("/v1/trips", json=body, headers=api["auth"]("trip:write"))


def test_registration_refuses_a_meal_the_ledger_says_is_closed(api):
    response = _register(api, api["shops"]["가 저녁만 하는 집"], "12:00")
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert [v["code"] for v in error["violations"]] == ["dining_closed_at_slot"]


def test_registration_accepts_a_meal_the_ledger_says_is_open(api):
    response = _register(api, api["shops"]["가 저녁만 하는 집"], "18:00")
    assert response.status_code in (200, 201), response.text
