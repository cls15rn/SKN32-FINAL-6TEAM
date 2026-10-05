# -*- coding: utf-8 -*-
"""`db_search/place_by_name.py` 의 SQL 을 **실 PostgreSQL** 로 돌린다. DB 에 못 붙으면 건너뛴다."""
from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.infrastructure.db.session import database_dsn, get_connection
from app.modules.travel_ops.activity.db_search.place_by_name import find_place_by_name


def _reachable() -> str | None:
    try:
        with psycopg.connect(database_dsn(), connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('place_catalog')")
            if cur.fetchone()[0] is None:
                return "place_catalog 가 없다 — migrate 먼저"
    except Exception as exc:  # noqa: BLE001
        return f"DB 에 연결할 수 없다: {type(exc).__name__}"
    return None


_SKIP = _reachable()
pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")

INSERT = ("INSERT INTO place_catalog (tenant_id, source, content_id, content_type_id, area_code, title, "
          "latitude, longitude, large_class_code, raw_json) VALUES (%s,%s,%s,'12','1',%s,37.5,127.0,'HS','{}')")


@pytest.fixture()
def tenant():
    tenant = "test_" + uuid4().hex
    rows = [("tour_api", "g1", "경복궁"), ("tour_api", "s1", "서울숲"), ("tour_api", "s2", "서울 숲(북쪽)"),
            ("daiso", "DS1", "다이소 강남점"), ("daiso", "DS2", "다이소 홍대점"),
            ("tour_api", "p1", "100%_할인 마켓"), ("tour_api", "h1", "롯데-월드")]
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        for source, cid, title in rows:
            cur.execute(INSERT, (tenant, source, cid, title))
    try:
        yield tenant
    finally:
        with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM place_catalog WHERE tenant_id=%s", (tenant,))


def test_exact_name_is_found(tenant):
    out = find_place_by_name(get_connection, tenant, "경복궁")
    assert out["status"] == "found" and out["content_id"] == "g1" and out["matched_title"] == "경복궁"


def test_spacing_is_ignored(tenant):
    assert find_place_by_name(get_connection, tenant, "서울  숲")["content_id"] == "s1"


def test_branch_stores_with_same_base_name_are_ambiguous(tenant):
    out = find_place_by_name(get_connection, tenant, "다이소")
    assert out["status"] == "ambiguous" and out["match_count"] == 2


def test_specific_branch_is_found(tenant):
    assert find_place_by_name(get_connection, tenant, "다이소 강남점")["content_id"] == "DS1"


def test_unknown_name_is_not_found(tenant):
    assert find_place_by_name(get_connection, tenant, "듣도보도못한곳")["status"] == "not_found"


def test_like_wildcards_in_the_name_are_literal(tenant):
    assert find_place_by_name(get_connection, tenant, "100%_할인 마켓")["content_id"] == "p1"
    assert find_place_by_name(get_connection, tenant, "%%")["status"] == "not_found"


def test_hyphen_and_slash_in_title_are_ignored(tenant):
    assert find_place_by_name(get_connection, tenant, "롯데월드")["content_id"] == "h1"


def test_other_tenant_sees_nothing(tenant):
    assert find_place_by_name(get_connection, "other_" + uuid4().hex, "경복궁")["status"] == "not_found"
