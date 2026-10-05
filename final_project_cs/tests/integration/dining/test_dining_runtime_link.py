"""여행에 새로 들어온 코어 장소가 판정할 때 원장 가게와 이어지는지.

연결(`dn_core_place_link`)은 `rebuild.py` 가 돌 때 한 번만 만들어졌다. 여행 장소는 등록할 때마다
새로 생기므로 원장을 다시 세운 뒤의 장소는 영영 이어지지 않았고, 판정은 늘 「모름」이었다(2026-10-01).
그래서 판정할 때 이어 본다 — 관광공사 콘텐츠 ID 가 같으면 그대로, 없으면 매칭기와 같은 규칙으로.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

import pytest

from app.modules.travel_ops.dining.ledger import dining_state

from tests.integration.dining.conftest import MIGRATIONS

AT = "2026-10-02T12:00:00+09:00"

#: 코어 `places` 에서 매칭이 읽는 칸만. 코어 DB 에 붙어 시험하면 이미 있는 표를 쓴다.
PLACES = """
CREATE TABLE IF NOT EXISTS places (
    place_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    latitude double precision,
    longitude double precision,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_name text,
    source_content_id text,
    trip_scope uuid
)"""


@pytest.fixture(scope="module")
def linkable(conn):
    """코어 places 와 매칭기(202) · 판정 때 잇기(220)를 올린다. conftest 는 202 를 뺀다."""
    conn.execute(PLACES)
    for name in ("202_dining_matcher.sql", "220_dining_runtime_link.sql"):
        conn.execute(open(os.path.join(MIGRATIONS, name), encoding="utf-8").read())
    return conn


@pytest.fixture
def world(linkable, place):
    """시험마다 테넌트 하나. 만든 코어 장소 · 원문 · 연결을 끝나면 지운다."""
    tenant = f"runtime-link-{uuid.uuid4().hex}"
    loads: list[str] = []

    def core(name: str, lat: float, lng: float, *, kind: str = "dining", content_id: str | None = None) -> str:
        attrs = {"source": "tour_api", "source_content_id": content_id} if content_id else {}
        return str(linkable.execute(
            "INSERT INTO places (tenant_id, name, kind, latitude, longitude, attributes, trip_scope) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING place_id",
            (tenant, name, kind, lat, lng, json.dumps(attrs), uuid.uuid4())).fetchone()[0])

    def tourapi(place_uid: str, content_id: str) -> None:
        load_id = str(uuid.uuid4())
        linkable.execute(
            "INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
            "row_count, status) VALUES (%s, 'tourapi_kor_food', %s, 'test', 'test', 1, 'loaded')",
            (load_id, datetime.now()))
        linkable.execute(
            "INSERT INTO dining.dn_source_record (load_id, source_code, external_id, place_uid, match_status, "
            "raw_json) VALUES (%s, 'tourapi_kor_food', %s, %s, 'auto', '{}'::jsonb)",
            (load_id, content_id, place_uid))
        loads.append(load_id)

    def link_of(core_id: str):
        return linkable.execute(
            "SELECT place_uid::text, linked_by FROM dining.dn_core_place_link "
            "WHERE tenant_id = %s AND core_place_id = %s", (tenant, core_id)).fetchone()

    yield tenant, core, tourapi, link_of, place
    linkable.execute("DELETE FROM dining.dn_core_place_link WHERE tenant_id = %s", (tenant,))
    linkable.execute("DELETE FROM places WHERE tenant_id = %s", (tenant,))
    for load_id in loads:
        linkable.execute("DELETE FROM dining.dn_source_record WHERE load_id = %s", (load_id,))
        linkable.execute("DELETE FROM dining.dn_load_meta WHERE load_id = %s", (load_id,))


def test_tourapi_place_links_by_content_id_even_when_far_and_renamed(linkable, world):
    tenant, core, tourapi, link_of, place = world
    uid = place("토속촌삼계탕", 37.5781, 126.9706)
    cid = f"t{uuid.uuid4().hex[:10]}"
    tourapi(uid, cid)
    # 이름도 좌표도 다르다 — 콘텐츠 ID 만 같다. 같은 관광공사 가게이니 이어야 한다.
    core_id = core("토속촌 삼계탕(경복궁점)", 37.5900, 126.9900, content_id=cid)

    state = dining_state(linkable, tenant, core_id, AT)

    assert state["linked"] is True
    assert link_of(core_id) == (uid, "runtime:content_id")


def test_place_without_content_id_links_by_same_name_nearby(linkable, world):
    tenant, core, _, link_of, place = world
    uid = place("부민옥", 37.5680, 126.9790)
    name = linkable.execute("SELECT name_ko FROM dining.dn_place WHERE place_uid = %s", (uid,)).fetchone()[0]
    core_id = core(name, 37.56815, 126.97910)          # 약 20m

    assert dining_state(linkable, tenant, core_id, AT)["linked"] is True
    assert link_of(core_id) == (uid, "runtime:matcher")


def test_two_nearby_candidates_are_not_linked(linkable, world):
    tenant, core, _, link_of, place = world
    a, b = place("쌍둥이집", 37.5500, 127.0000), place("쌍둥이집", 37.55005, 127.00005)
    linkable.execute("UPDATE dining.dn_place SET name_ko = '쌍둥이집' WHERE place_uid IN (%s, %s)", (a, b))
    core_id = core("쌍둥이집", 37.55002, 127.00002)

    assert dining_state(linkable, tenant, core_id, AT)["linked"] is False
    assert link_of(core_id) is None


def test_same_name_far_away_is_not_linked(linkable, world):
    tenant, core, _, link_of, place = world
    uid = place("소울", 37.5432, 126.9876)               # 용산 — 미쉐린 「소울」
    name = linkable.execute("SELECT name_ko FROM dining.dn_place WHERE place_uid = %s", (uid,)).fetchone()[0]
    core_id = core(name, 37.5800, 126.9690)              # 종로 자하문로 — 4km

    assert dining_state(linkable, tenant, core_id, AT)["linked"] is False
    assert link_of(core_id) is None
