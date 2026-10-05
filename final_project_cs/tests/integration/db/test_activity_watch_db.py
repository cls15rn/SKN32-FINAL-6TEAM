# -*- coding: utf-8 -*-
"""`activity/watch.py` 의 `due_activities` 를 **실 PostgreSQL** 로 돌린다 — 3시간 창 · 5분 간격.

시험용 테넌트에 `places`·`activities` 행을 넣고, 한 회차 돌린 뒤 지운다. DB 에 못 붙으면 건너뛴다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from app.infrastructure.db.session import database_dsn, get_connection
from app.modules.travel_ops.activity.watch_runner import run_activity_disaster


def _reachable() -> str | None:
    try:
        with psycopg.connect(database_dsn(), connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('activities'), to_regclass('watch_observations'), "
                        "(SELECT count(*) FROM information_schema.columns "
                        " WHERE table_name='activities' AND column_name='place_id')")
            a, w, has_place = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        return f"DB 에 연결할 수 없다: {type(exc).__name__}"
    if a is None or w is None or not has_place:
        return "activities(014·015)·watch_observations(012) 가 없다 — migrate 먼저"
    return None


_SKIP = _reachable()
pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


class _Disaster:
    name = "disaster_msg"

    def __init__(self):
        self.calls = 0

    def near(self, lat, lng, *, within):
        self.calls += 1
        return {"for_region": [{"serial": "S1", "step": "안전안내"}]}


class _Sources:
    def __init__(self):
        self.disaster = _Disaster()


@pytest.fixture()
def tenant():
    tenant = "test_" + uuid4().hex
    now = datetime.now(UTC)
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("INSERT INTO places (tenant_id, name, kind, latitude, longitude) "
                    "VALUES (%s,'시험 장소','activity',37.57,126.98) RETURNING place_id", (tenant,))
        place_id = cur.fetchone()[0]
        for name, delta in (("1시간 뒤", timedelta(hours=1)), ("2시간 50분 뒤", timedelta(hours=2, minutes=50)),
                            ("4시간 뒤", timedelta(hours=4)), ("이미 지남", -timedelta(hours=1))):
            cur.execute("INSERT INTO activities (tenant_id, name, activity_time, place_id) VALUES (%s,%s,%s,%s)",
                        (tenant, name, now + delta, place_id))
    try:
        yield tenant
    finally:
        with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM watch_observations WHERE tenant_id=%s", (tenant,))
            cur.execute("DELETE FROM activities WHERE tenant_id=%s", (tenant,))
            cur.execute("DELETE FROM places WHERE tenant_id=%s", (tenant,))


def test_only_activities_within_three_hours_are_checked(tenant):
    sources = _Sources()
    out = run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources)
    assert out["checked"] == 2 and sources.disaster.calls == 2 and out["fatal"] == 0


def test_same_activity_is_not_checked_again_within_five_minutes(tenant):
    sources = _Sources()
    run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources)
    again = run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources)
    assert again["checked"] == 0 and sources.disaster.calls == 2


def test_checked_again_once_the_gap_has_passed(tenant):
    sources = _Sources()
    run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources)
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE watch_observations SET observed_at = now() - interval '6 minutes' "
                    "WHERE tenant_id=%s", (tenant,))
    again = run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources)
    assert again["checked"] == 2


def test_window_and_gap_can_be_swapped(tenant):
    sources = _Sources()
    out = run_activity_disaster(connection_factory=get_connection, tenant_id=tenant, sources=sources,
                                within_hours=5, min_gap_minutes=0)
    assert out["checked"] == 3
