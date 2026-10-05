# -*- coding: utf-8 -*-
"""`app/modules/travel_ops/activity/db_search/place_candidates.py` 를 **실 PostgreSQL** 로 돌린다.

두 가지를 본다.
  1. 시험용 테넌트에 행 몇 개를 직접 넣고 조회 → SQL 이 실제로 맞는지(jsonb `->>` 포함).
  2. 적재된 실데이터(`demo` 테넌트, `scripts/load_place_catalog_csv.py`)가 있으면
     창경궁(126511)으로 후보가 나오는지. 적재 전이면 건너뛴다.

★DB 에 못 붙으면 **건너뛴다**(실패가 아니다) — `.env` 의 `ACOP_DATABASE_URL` 로 붙는다.
"""
from __future__ import annotations

import json
from uuid import uuid4

import psycopg
import pytest

from app.infrastructure.db.session import database_dsn, get_connection
from app.modules.travel_ops.activity.alternatives import RADIUS_MAX_KM, bounding_box
from app.modules.travel_ops.activity.db_search.place_candidates import find_place_candidates


def _reachable() -> str | None:
    try:
        with psycopg.connect(database_dsn(), connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('place_catalog'), "
                        "(SELECT count(*) FROM information_schema.columns "
                        " WHERE table_name='place_catalog' AND column_name='large_class_code')")
            table, has_large = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — 어떤 이유든 못 붙으면 건너뛴다
        return f"DB 에 연결할 수 없다: {type(exc).__name__}"
    if table is None or not has_large:
        return "place_catalog(011·017) 가 없다 — `python -m app.infrastructure.db.migrate` 먼저"
    return None


_SKIP = _reachable()
pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")

INSERT = ("INSERT INTO place_catalog (tenant_id, source, content_id, content_type_id, "
          "area_code, title, latitude, longitude, large_class_code, raw_json) "
          "VALUES (%s,%s,%s,'12','1',%s,%s,%s,%s,%s)")


def _raw(cid, title, l1, sgg, closed):
    return json.dumps({"contentid": cid, "title": title, "lclsSystm1": l1,
                       "lclsSystm2": l1 + "01", "lclsSystm3": l1 + "010100",
                       "sigungucode": sgg, "closed_days": closed,
                       "business_hours": "09:00~18:00"}, ensure_ascii=False)


@pytest.fixture()
def tenant():
    tenant = "test_" + uuid4().hex
    rows = [
        # cid,      title,        lat,     lon,      l1,   sgg,  closed
        ("o1",     "원래 장소",   37.5796, 126.9770, "HS", "23", "매주 화요일"),
        ("same_l1", "같은 대분류", 37.60,   127.05,   "HS", "1",  "연중무휴"),
        ("same_gu", "같은 자치구", 37.58,   126.98,   "VE", "23", "연중무휴"),
        ("neither", "둘 다 다름",  37.50,   127.10,   "SH", "1",  "연중무휴"),
        ("no_xy",   "좌표 없음",   None,    None,     "HS", "5",  None),
        # 브랜드 출처 — 같은 카탈로그의 다른 source. 후보에도 원래 장소에도 들어야 한다.
        ("OYtest1", "올영 테스트점", 37.56,  126.98,   "SH", "23", "연중무휴"),
        ("OYtest2", "올영 다른점",   37.57,  126.99,   "SH", "23", "연중무휴"),
    ]
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        for cid, title, lat, lon, l1, sgg, closed in rows:
            source = "oliveyoung" if cid.startswith("OY") else "tour_api"
            cur.execute(INSERT, (tenant, source, cid, title, lat, lon, l1, _raw(cid, title, l1, sgg, closed)))
    try:
        yield tenant
    finally:
        with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM place_catalog WHERE tenant_id=%s", (tenant,))


def test_pool_is_within_the_10km_bounding_box(tenant):
    """★분류와 무관하게 10km 사각형 안만 — `neither`(약 14km)와 좌표 없는 `no_xy` 는 빠진다."""
    pool = find_place_candidates(get_connection, tenant, "o1")

    assert pool["origin"]["title"] == "원래 장소"
    assert pool["origin"]["closed_days"] == "매주 화요일"
    assert sorted(c["contentid"] for c in pool["candidates"]) == ["OYtest1", "OYtest2", "same_gu", "same_l1"]
    assert pool["source"].startswith("place_catalog:tour_api")
    assert pool["confirmed_at"]


def test_brand_store_as_origin_finds_brand_candidates(tenant):
    pool = find_place_candidates(get_connection, tenant, "OYtest1")
    assert pool["origin"]["title"] == "올영 테스트점"
    assert "OYtest2" in [c["contentid"] for c in pool["candidates"]]


def test_null_coordinates_are_not_in_the_pool(tenant):
    """좌표가 없으면 근처인지 모른다 — 범위 비교에서 빠진다(원본 자리표시 좌표로 되살리지 않는다)."""
    pool = find_place_candidates(get_connection, tenant, "o1")
    assert "no_xy" not in [c["contentid"] for c in pool["candidates"]]


def test_unknown_origin_and_other_tenant_are_unknown(tenant):
    assert find_place_candidates(get_connection, tenant, "없는ID") is None
    assert find_place_candidates(get_connection, "other_" + uuid4().hex, "o1") is None


def test_loaded_demo_data_finds_alternatives_for_changgyeonggung():
    """적재된 실데이터 확인. wiki/teams/activity.md 실측: 창경궁(126511)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM place_catalog WHERE tenant_id='demo' AND source='tour_api'")
        loaded = cur.fetchone()[0]
    if not loaded:
        pytest.skip("demo 테넌트에 적재된 데이터가 없다 — load_place_catalog_csv 먼저")

    pool = find_place_candidates(get_connection, "demo", "126511")
    assert pool is not None, "창경궁(126511)이 카탈로그에 없다"
    assert pool["origin"]["lclsSystm1"] == "HS"
    assert pool["candidates"], "후보가 0건이다"
    lat0, lat1, lng0, lng1 = bounding_box(float(pool["origin"]["mapy"]), float(pool["origin"]["mapx"]),
                                          RADIUS_MAX_KM)
    assert all(lat0 <= float(c["mapy"]) <= lat1 and lng0 <= float(c["mapx"]) <= lng1
               for c in pool["candidates"])
