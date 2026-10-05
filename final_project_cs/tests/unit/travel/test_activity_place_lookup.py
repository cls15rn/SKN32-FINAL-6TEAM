# -*- coding: utf-8 -*-
"""장소 이름 찾기 — 카탈로그 → (없으면) 카카오 **존재만** 확인 → Team 이 상태별로 답한다.

DB·네트워크 없이 본다(가짜 DB·가짜 카카오). SQL 자체는 `tests/integration/db/test_place_by_name_db.py`.
★핵심 둘: ① 「없음」과 「못 물어봄」을 가른다 ② 카카오가 준 값은 결과·근거·로그 어디에도 남지 않는다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from app.core.contracts import NextAction
from app.modules.travel_ops.activity import ActivityTeam
from app.modules.travel_ops.activity import failure_codes as fc
from app.modules.travel_ops.activity.place_lookup import lookup_place
from app.tools.read_tools import ReadToolbox

from .helpers import FakeTools, pack, task

ALLOWED = ActivityTeam.manifest.allowed_tools
WHEN = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        self.params = params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn(_Cursor):
    def cursor(self):
        return self


def _db(rows):
    # (content_id, content_type_id, title, latitude, longitude, source)
    return lambda: _Conn([tuple(r) for r in rows])


GYEONGBOK = ("126508", "12", "경복궁", 37.576, 126.977, "tour_api")


class _Kakao:
    def __init__(self, hits, misses=None):
        self.hits, self.misses, self.calls = hits, misses or {}, []

    def search(self, query, **kw):
        self.calls.append(query)
        return self.hits


KAKAO_HIT = [{"id": "999", "name": "새로생긴전시관", "category": "문화", "category_group": "CT1",
              "address": "서울 종로구 비밀주소 1", "latitude": 37.5, "longitude": 127.0}]


# ── lookup_place ─────────────────────────────────────────────────

def test_catalog_hit_never_calls_kakao():
    kakao = _Kakao(KAKAO_HIT)
    out = lookup_place(_db([GYEONGBOK]), "t", "경복궁", kakao)
    assert out["status"] == "found" and out["content_id"] == "126508" and kakao.calls == []


def test_two_catalog_hits_are_ambiguous_with_candidate_names():
    rows = [("1", "12", "서울 숲", 37.5, 127.0, "tour_api"), ("2", "12", "서울  숲", 37.6, 127.1, "tour_api")]
    out = lookup_place(_db(rows), "t", "서울숲", _Kakao(None))
    assert out["status"] == "ambiguous" and out["match_count"] == 2
    assert [c["content_id"] for c in out["candidates"]] == ["1", "2"]


def test_exact_title_beats_looser_matches():
    rows = [("1", "12", "경복궁", 37.5, 127.0, "tour_api"), ("2", "12", "경복궁 야간개장", 37.5, 127.0, "tour_api")]
    assert lookup_place(_db(rows), "t", "경복궁", None)["content_id"] == "1"


def test_missing_in_catalog_but_kakao_knows_it_is_exists_unregistered():
    out = lookup_place(_db([]), "t", "새로생긴전시관", _Kakao(KAKAO_HIT))
    assert out == {"status": "exists_unregistered", "via": "kakao"}


def test_kakao_values_never_appear_in_the_result():
    out = lookup_place(_db([]), "t", "새로생긴전시관", _Kakao(KAKAO_HIT))
    raw = json.dumps(out, ensure_ascii=False)
    for leaked in ("비밀주소", "37.5", "127.0", "999", "CT1"):
        assert leaked not in raw


def test_kakao_with_no_matching_name_is_not_found():
    """카카오 1위가 아무 가게여도 「있다」고 하지 않는다."""
    out = lookup_place(_db([]), "t", "듣도보도못한곳", _Kakao(KAKAO_HIT))
    assert out["status"] == "not_found"


def test_kakao_empty_result_is_not_found():
    assert lookup_place(_db([]), "t", "없는곳", _Kakao([]))["status"] == "not_found"


def test_kakao_failure_is_unknown_not_not_found():
    out = lookup_place(_db([]), "t", "없는곳", _Kakao(None, misses={"budget_exhausted": 3}))
    assert out["status"] == "unknown" and out["reason"] == "kakao_blocked" and out["detail"] == "budget_exhausted"


def test_no_kakao_key_is_unknown_not_not_found():
    out = lookup_place(_db([]), "t", "없는곳", None)
    assert out["status"] == "unknown" and out["reason"] == "kakao_unavailable"


def test_too_short_name_is_not_searched_at_all():
    kakao = _Kakao(KAKAO_HIT)
    out = lookup_place(_db([GYEONGBOK]), "t", "경", kakao)
    assert out["status"] == "not_found" and kakao.calls == []


def test_toolbox_tool_returns_none_for_blank_name():
    class Scope:
        tenant_id = "t"

    assert ReadToolbox(_db([])).place_lookup(Scope(), name="  ") is None
    assert "read.place_lookup" in ReadToolbox(_db([]))._travel_tools()


# ── Team 이 상태별로 답하는 방식 ─────────────────────────────────────

def _task():
    ctx = pack("activity", scope=["activity"],
               state={"requested_place_name": "새로생긴전시관", "requested_activity_time": WHEN})
    return task("activity", "activity.submit_itinerary", ctx, ALLOWED)


async def _run(result, caplog):
    caplog.set_level(logging.WARNING, logger=fc.LOGGER_NAME)
    return await ActivityTeam(FakeTools({"read.place_lookup": result})).execute(_task())


@pytest.mark.asyncio
async def test_exists_unregistered_asks_again_and_makes_no_proposal(caplog):
    out = await _run({"status": "exists_unregistered", "via": "kakao"}, caplog)
    assert out.next_action is NextAction.WAIT_FOR_INPUT and not out.action_proposals
    assert out.decisions[0]["failure_code"] == fc.PLACE_EXISTS_UNREGISTERED
    assert "실제로 있는 장소" in out.answer


@pytest.mark.asyncio
async def test_ambiguous_lists_candidates_and_asks(caplog):
    found = {"status": "ambiguous", "via": "place_catalog", "match_count": 2,
             "candidates": [{"content_id": "1", "title": "서울 숲"}, {"content_id": "2", "title": "서울숲 공원"}]}
    out = await _run(found, caplog)
    assert out.next_action is NextAction.WAIT_FOR_INPUT and out.decisions[0]["failure_code"] == fc.PLACE_AMBIGUOUS
    assert "서울 숲" in out.answer and "서울숲 공원" in out.answer


@pytest.mark.asyncio
async def test_not_found_asks_again(caplog):
    out = await _run({"status": "not_found", "via": "kakao"}, caplog)
    assert out.next_action is NextAction.WAIT_FOR_INPUT and out.decisions[0]["failure_code"] == fc.PLACE_NOT_FOUND


@pytest.mark.asyncio
async def test_blocked_lookup_goes_to_a_person_not_back_to_the_customer(caplog):
    """★못 물어본 것을 「없는 이름」이라며 고객에게 다시 쓰게 하지 않는다."""
    out = await _run({"status": "unknown", "via": "kakao", "reason": "kakao_blocked"}, caplog)
    assert out.outcome == "escalated"
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == fc.LOGGER_NAME]
    assert [x["code"] for x in lines] == [fc.PLACE_LOOKUP_BLOCKED]
    assert lines[0]["reason"] == "kakao_blocked"


@pytest.mark.asyncio
async def test_failure_logs_carry_no_customer_text(caplog):
    await _run({"status": "exists_unregistered", "via": "kakao"}, caplog)
    raw = json.dumps([r.getMessage() for r in caplog.records if r.name == fc.LOGGER_NAME], ensure_ascii=False)
    assert "새로생긴전시관" not in raw
