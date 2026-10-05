# -*- coding: utf-8 -*-
"""`app/modules/travel_ops/activity/db_search/place_candidates.py` — `read.place_candidates` 의 DB 조회.

가짜 연결로 SQL 인자와 행 → 계약 모양 변환을 본다(실 DB 없이).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.tools.read_tools import ReadToolbox, ToolContext
from app.modules.travel_ops.activity.alternatives import RADIUS_MAX_KM, bounding_box
from app.modules.travel_ops.activity.db_search.place_candidates import (
    DEFAULT_SOURCES, ORIGIN_SQL, POOL_SQL, find_place_candidates)

UTC = timezone.utc
OLD = datetime(2026, 9, 20, tzinfo=UTC)
NEW = datetime(2026, 9, 28, tzinfo=UTC)


def _record(cid, *, title, l1="HS", sgg="23", lon=126.98, lat=37.58,
            closed="연중무휴", fetched=NEW):
    raw = {"contentid": cid, "title": title, "lclsSystm1": l1, "lclsSystm2": l1 + "01",
           "lclsSystm3": l1 + "010100", "sigungucode": sgg,
           "mapx": "117.99", "mapy": "19.69",        # ★원본 좌표 — 쓰지 않아야 한다
           "closed_days": closed, "business_hours": "09:00~18:00"}
    return (cid, "12", title, lat, lon, l1, raw, fetched)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.executed.append((sql, params))
        self._result = self.conn.origin if sql == ORIGIN_SQL else self.conn.pool

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConnection:
    def __init__(self, origin, pool):
        self.origin, self.pool = origin, pool
        self.executed: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)


ORIGIN = _record("126511", title="창경궁", closed="매주 월요일", fetched=OLD)
NEAR = _record("c1", title="성균관 명륜당")
NO_COORD = _record("c2", title="좌표 없음", lon=None, lat=None)


def test_returns_origin_and_candidates_in_the_csv_shape():
    conn = FakeConnection([ORIGIN], [NEAR, NO_COORD])
    pool = find_place_candidates(lambda: conn, "t1", "126511")

    assert pool["origin"]["contentid"] == "126511"
    assert pool["origin"]["closed_days"] == "매주 월요일"
    assert [c["contentid"] for c in pool["candidates"]] == ["c1", "c2"]
    first = pool["candidates"][0]
    assert first == {"contentid": "c1", "title": "성균관 명륜당", "contenttypeid": "12",
                     "lclsSystm1": "HS", "lclsSystm2": "HS01", "lclsSystm3": "HS010100",
                     "sigungucode": "23", "brand": None, "mapx": "126.98", "mapy": "37.58",
                     "closed_days": "연중무휴", "business_hours": "09:00~18:00"}
    assert pool["source"] == "place_catalog:" + "+".join(DEFAULT_SOURCES)


def test_brand_comes_from_raw_json():
    rec = _record("OY1", title="올영")
    rec[6]["brand"] = "올리브영"
    conn = FakeConnection([rec], [rec])
    pool = find_place_candidates(lambda: conn, "t1", "OY1")
    assert pool["origin"]["brand"] == "올리브영" and pool["candidates"][0]["brand"] == "올리브영"


def test_nulled_coordinates_stay_unknown():
    """★적재 때 NULL 로 넣은 좌표를 raw_json 원본(자리표시값)으로 되살리지 않는다."""
    conn = FakeConnection([ORIGIN], [NO_COORD])
    candidate = find_place_candidates(lambda: conn, "t1", "126511")["candidates"][0]
    assert candidate["mapx"] is None and candidate["mapy"] is None


def test_confirmed_at_is_the_oldest_fetch():
    conn = FakeConnection([ORIGIN], [NEAR])
    assert find_place_candidates(lambda: conn, "t1", "126511")["confirmed_at"] == OLD.isoformat()


def test_pool_is_narrowed_by_the_max_radius_bounding_box():
    """★거리 1단계 — SQL 은 원래 장소 좌표 기준 10km 사각형으로만 거른다(분류·시군구로 좁히지 않는다)."""
    conn = FakeConnection([ORIGIN], [])
    find_place_candidates(lambda: conn, "t1", "126511")
    (sql1, p1), (sql2, p2) = conn.executed
    assert (sql1, p1) == (ORIGIN_SQL, ("t1", list(DEFAULT_SOURCES), "126511"))
    assert sql2 == POOL_SQL and "sigungucode" not in POOL_SQL
    assert p2 == ("t1", list(DEFAULT_SOURCES), "126511", *bounding_box(37.58, 126.98, RADIUS_MAX_KM))


def test_origin_without_coordinates_reads_no_pool():
    conn = FakeConnection([_record("126511", title="창경궁", lon=None, lat=None)], [NEAR])
    pool = find_place_candidates(lambda: conn, "t1", "126511")
    assert pool["candidates"] == [] and len(conn.executed) == 1


def test_pool_reads_brand_sources_too():
    """★TourAPI 만 보면 브랜드 매장(OY/DS/AB/MS)이 후보에도, 원래 장소에도 못 든다."""
    assert {"tour_api", "oliveyoung", "daiso", "artbox", "musinsa"} <= set(DEFAULT_SOURCES)
    assert "source = ANY(%s)" in ORIGIN_SQL and "source = ANY(%s)" in POOL_SQL


def test_sources_can_be_narrowed():
    conn = FakeConnection([ORIGIN], [])
    pool = find_place_candidates(lambda: conn, "t1", "126511", sources=("tour_api",))
    assert conn.executed[0][1] == ("t1", ["tour_api"], "126511")
    assert pool["source"] == "place_catalog:tour_api"


def test_unknown_origin_is_unknown():
    conn = FakeConnection([], [NEAR])
    assert find_place_candidates(lambda: conn, "t1", "999") is None
    assert len(conn.executed) == 1      # ★후보는 읽지 않는다


def test_no_content_id_does_not_open_a_connection():
    def boom():
        raise AssertionError("열면 안 된다")
    assert find_place_candidates(boom, "t1", "  ") is None


def test_read_tool_passes_the_tenant_scope():
    conn = FakeConnection([ORIGIN], [NEAR])
    scope = ToolContext("tenant-x", uuid4(), uuid4(), ["activity"])
    pool = ReadToolbox(lambda: conn).place_candidates(scope, content_id="126511")
    assert pool["origin"]["contentid"] == "126511"
    assert conn.executed[0][1][0] == "tenant-x"
