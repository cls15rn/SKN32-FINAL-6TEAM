"""요식 원장을 코어 모양으로 돌려주는 함수의 회귀 시험.

DB 없이 돈다. 커서를 가짜로 세워 「무엇을 물었고 무엇을 돌려주는가」만 본다.
판정 자체는 SQL 함수가 하고 그쪽은 별도로 시험한다. 여기서 지키는 것은
모양과 경계다 — 모르는 것을 모름으로 두는가, 아는 값을 덮지 않는가.

ledger.py 는 경로로 읽는다. app.modules.travel_ops 를 import 하면
다른 팀 모듈과 openai 까지 딸려 와서 이 시험이 그것들에 매이게 된다.
ledger.py 자체는 psycopg 도 import 하지 않으므로 경로로 읽는 편이 맞다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pytest

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LEDGER = os.path.join(ROOT, "app", "modules", "travel_ops", "dining", "ledger.py")

_spec = importlib.util.spec_from_file_location("dining_ledger", LEDGER)
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

CORE_ID = "11111111-1111-4111-8111-111111111111"
PLACE_UID = "d8d8d54d-2235-58f5-9895-f630f1dcdb0f"
CONFIRMED = datetime(2026, 9, 21, 16, 9, tzinfo=KST)


class FakeCursor:
    """물어본 SQL 을 보고 정해둔 답을 돌려준다."""

    def __init__(self, plan: dict):
        self.plan = plan
        self.asked: list[str] = []
        self._row = None

    def execute(self, sql, params=None):
        self.asked.append(sql)
        if "dn_core_place_link" in sql:
            self._row = self.plan.get("link")
        elif "link_core_place" in sql:                   # 판정 때 잇기(220) — 정해둔 답이 없으면 못 이음
            self._row = self.plan.get("auto_link", (None,))
        elif "core_place_state" in sql:
            self._row = self.plan.get("state")
        elif "meets_condition" in sql:
            code = params[1]
            self._row = (self.plan.get("conditions", {}).get(code),)
        else:                                            # pragma: no cover
            raise AssertionError(f"뜻밖의 조회: {sql}")

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConn:
    def __init__(self, plan: dict):
        self.cursor_obj = FakeCursor(plan)

    def cursor(self):
        return self.cursor_obj

    def transaction(self):
        return contextlib.nullcontext()


def linked(state=None, conditions=None) -> FakeConn:
    return FakeConn({"link": (PLACE_UID,), "state": state,
                     "conditions": conditions or {}})


OPEN_STATE = (True, False, False, None,
              {"hours": ["11:00", "22:00"], "break": ["15:00", "17:00"]},
              CONFIRMED)


# ── 답할 수 없을 때 ──────────────────────────────────────────

def test_장소를_모르면_조회하지_않는다():
    conn = linked()
    assert ledger.dining_state(conn, "demo", None, "2026-09-22 12:00+09:00") is None
    assert conn.cursor_obj.asked == []


def test_시각이_없으면_답하지_않는다():
    conn = linked()
    assert ledger.dining_state(conn, "demo", CORE_ID, None) is None
    assert conn.cursor_obj.asked == []


def test_시각을_못_읽으면_지금으로_대체하지_않는다():
    conn = linked()
    assert ledger.dining_state(conn, "demo", CORE_ID, "언젠가") is None
    assert conn.cursor_obj.asked == []


# ── 이어지지 않은 장소 ────────────────────────────────────────

def test_안_이어진_장소는_모름이지_영업_안_함이_아니다():
    conn = FakeConn({"link": None})
    got = ledger.dining_state(conn, "demo", CORE_ID, "2026-09-22 12:00+09:00")
    assert got["linked"] is False
    assert got["open_at_slot"] is None
    assert got["needs_holiday_check"] is None


def test_안_이어진_장소도_같은_칸을_갖는다():
    """모양이 갈리면 받는 쪽이 갈래마다 다르게 다뤄야 한다."""
    a = ledger.dining_state(FakeConn({"link": None}), "demo", CORE_ID,
                            "2026-09-22 12:00+09:00")
    b = ledger.dining_state(linked(OPEN_STATE), "demo", CORE_ID,
                            "2026-09-22 12:00+09:00")
    assert set(a) == set(b)


# ── 판정 ────────────────────────────────────────────────────

def test_여는_시각의_답():
    got = ledger.dining_state(linked(OPEN_STATE), "demo", CORE_ID,
                              "2026-09-22 12:00+09:00", "2026-09-22 13:00+09:00")
    assert got["open_at_slot"] is True
    assert got["confirmed_at"] == CONFIRMED
    assert got["source"] == "dining_ledger"


def test_브레이크를_잃지_않는다():
    """hours 만 꺼내면 11:00~22:00 내내 연다고 보이게 된다."""
    got = ledger.dining_state(linked(OPEN_STATE), "demo", CORE_ID,
                              "2026-09-22 12:00+09:00")
    assert got["break"] == ["15:00", "17:00"]
    assert got["attributes"]["break"] == ["15:00", "17:00"]


def test_규칙이_없으면_모름():
    got = ledger.dining_state(linked(None), "demo", CORE_ID, "2026-09-22 12:00+09:00")
    assert got["linked"] is True
    assert got["open_at_slot"] is None
    assert got["hours"] is None


# ── 조건 ────────────────────────────────────────────────────

@pytest.mark.parametrize("accepted", [True, False, None])
def test_card_payment_keeps_the_ledgers_three_valued_verdict(accepted):
    got = ledger.dining_state(linked(OPEN_STATE, {"card_payment": accepted}), "demo", CORE_ID,
                              "2026-09-22 12:00+09:00")
    assert got["card_payment"] is accepted


def test_모르는_조건은_어느_목록에도_넣지_않는다():
    conn = linked(OPEN_STATE, {"halal": None, "vegetarian_menu": True,
                               "kids_allowed": False})
    got = ledger.dining_state(conn, "demo", CORE_ID, "2026-09-22 12:00+09:00")
    assert got["dietary"] == ["vegetarian"]
    assert got["dietary_absent"] == ["kids"]


# ── 코어 결과에 얹기 ──────────────────────────────────────────

def test_비어_있을_때만_채운다():
    row = {"place_id": CORE_ID, "open_at_slot": None, "confirmed_at": None,
           "dietary": [], "dietary_absent": []}
    got = ledger.enrich_place(linked(OPEN_STATE), "demo", row,
                              "2026-09-22 12:00+09:00")
    assert got["open_at_slot"] is True
    assert got["dining_source"] == "dining_ledger"


def test_확인_시각은_코어_키_이름으로_채운다():
    """SQL 칸은 hours_confirmed_at 이지만 도구가 주는 키는 confirmed_at 이다."""
    row = {"place_id": CORE_ID, "open_at_slot": None, "confirmed_at": None,
           "dietary": [], "dietary_absent": []}
    got = ledger.enrich_place(linked(OPEN_STATE), "demo", row,
                              "2026-09-22 12:00+09:00")
    assert got["confirmed_at"] == CONFIRMED
    assert "hours_confirmed_at" not in got


def test_코어가_아는_값은_덮지_않는다():
    row = {"place_id": CORE_ID, "open_at_slot": False, "confirmed_at": None,
           "dietary": [], "dietary_absent": []}
    got = ledger.enrich_place(linked(OPEN_STATE), "demo", row,
                              "2026-09-22 12:00+09:00")
    assert got["open_at_slot"] is False


def test_조건_목록은_합치고_지우지_않는다():
    row = {"place_id": CORE_ID, "open_at_slot": None, "confirmed_at": None,
           "dietary": ["vegetarian"], "dietary_absent": []}
    conn = linked(OPEN_STATE, {"halal": True})
    got = ledger.enrich_place(conn, "demo", row, "2026-09-22 12:00+09:00")
    assert got["dietary"] == ["vegetarian", "halal"]


def test_안_이어진_장소는_코어_결과를_건드리지_않는다():
    row = {"place_id": CORE_ID, "open_at_slot": None, "dietary": []}
    got = ledger.enrich_place(FakeConn({"link": None}), "demo", row,
                              "2026-09-22 12:00+09:00")
    assert got == row
    assert "dining_source" not in got


def test_시각이_없으면_아무것도_채우지_않는다():
    row = {"place_id": CORE_ID, "open_at_slot": None, "dietary": []}
    got = ledger.enrich_place(linked(OPEN_STATE), "demo", row, None)
    assert got == row


def test_장소가_없으면_그대로_None():
    assert ledger.enrich_place(linked(OPEN_STATE), "demo", None,
                               "2026-09-22 12:00+09:00") is None


# ── 시각 읽기 ────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect_hour", [
    ("2026-09-22T12:00:00+09:00", 12),
    ("2026-09-22 12:00+09:00", 12),
    ("2026-09-22T03:00:00Z", 3),
])
def test_시각_표기를_읽는다(text, expect_hour):
    got = ledger._as_datetime(text)
    assert got is not None and got.hour == expect_hour


def test_시간대가_없으면_한국_시각으로_본다():
    got = ledger._as_datetime("2026-09-22 12:00")
    assert got.utcoffset() == timedelta(hours=9)
