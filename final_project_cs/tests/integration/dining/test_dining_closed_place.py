"""폐업한 가게는 「모름」이 아니라 「닫힘」이다.

폐업 대조에서 사람이 폐업으로 확인한 가게(record_status = 'closed')도 판정은 영업규칙만 봤다.
규칙이 없으면 「모름」, 옛 관광공사 규칙이 남아 있으면 「영업」으로 답해, 일정에 폐업한 식당이
있어도 경고하지 못했다(잇텐고 · 2026-10-01).
"""
from __future__ import annotations

from datetime import datetime

from tests.integration.dining.test_dining_judgment import add_hours

NOON = datetime.fromisoformat("2026-10-05T12:00:00+09:00")      # 월요일


def open_at(conn, uid):
    return conn.execute("SELECT dining.open_at_slot(%s, %s, %s)",
                        (uid, NOON, NOON.replace(hour=13))).fetchone()[0]


def test_closed_place_with_old_hours_is_closed(conn, place):
    uid = place()
    add_hours(conn, uid, 1, [(600, 1320, None)])              # 월 10:00-22:00 — 폐업 전 규칙
    conn.execute("UPDATE dining.dn_place SET record_status = 'closed' WHERE place_uid = %s", (uid,))

    assert open_at(conn, uid) is False


def test_closed_place_without_hours_is_closed_not_unknown(conn, place):
    uid = place()
    conn.execute("UPDATE dining.dn_place SET record_status = 'closed' WHERE place_uid = %s", (uid,))

    assert open_at(conn, uid) is False


def test_open_place_still_follows_its_hours(conn, place):
    uid = place()
    add_hours(conn, uid, 1, [(600, 1320, None)])

    assert open_at(conn, uid) is True
