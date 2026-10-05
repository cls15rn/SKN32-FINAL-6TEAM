# -*- coding: utf-8 -*-
"""Activity 실패·예외 코드 — 결과의 `decisions[].failure_code` 와 로그(`acop.activity.failure`)가 맞는지.

대상: 휴무 · 위급재난 · 이미 시작됨 · 정원 초과 · 장소 모름 · 장소 검색 실패 · 대체 후보 조회 실패/없음 · 도구(API·DB) 예외.
※분류 실패·팀 에스컬레이션은 코어가 이미 `failure_code` 와 함께 이벤트로 남긴다(여기서 다시 안 본다).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import pytest

from app.modules.travel_ops.activity import ActivityTeam
from app.modules.travel_ops.activity import failure_codes as fc

from .helpers import FakeTools, pack, task
from .test_activity_alternatives_flow import ALLOWED, KST, POOL, _row, _values

DISASTER = {"for_region": [{"step": "위급재난", "kind": "지진"}],
            "confirmed_at": "2026-09-28T10:00:00+00:00", "source": "disaster_api"}


class RaisingTools(FakeTools):
    """값이 예외 객체면 그 도구를 부를 때 던진다 — API·DB 실패를 흉내 낸다."""

    def call(self, name, context, arguments, allowed_tools, seen, budget=None):
        value = self.values.get(name)
        if isinstance(value, Exception):
            self.calls.append((name, dict(arguments)))
            raise value
        return super().call(name, context, arguments, allowed_tools, seen, budget)


async def _run(values, caplog, *, tools_cls=FakeTools):
    caplog.set_level(logging.WARNING, logger=fc.LOGGER_NAME)
    tools = tools_cls(values)
    ctx = pack("activity", scope=["activity"])
    result = await ActivityTeam(tools).execute(task("activity", "activity.check_feasible", ctx, ALLOWED))
    return result, _lines(caplog)


def _lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == fc.LOGGER_NAME]


def _codes(lines) -> list[str]:
    return [line["code"] for line in lines]


# ── 코드 목록 ────────────────────────────────────────────────────

def test_every_code_is_described_and_snake_case():
    codes = {v for k, v in vars(fc).items() if k.isupper() and isinstance(v, str) and k != "LOGGER_NAME"}
    assert codes == set(fc.DESCRIPTIONS), "새 코드를 더했으면 DESCRIPTIONS 에도 적는다"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", c) for c in codes)


# ── 판정 실패(정상 응답으로 끝나는 것) ────────────────────────────────

@pytest.mark.asyncio
async def test_closed_weekday_carries_the_code_and_logs_one_line(caplog):
    result, lines = await _run(_values(), caplog)                 # 월요일 · 매주 월요일 휴무
    assert result.decisions[0]["failure_code"] == fc.CLOSED_WEEKDAY
    closed = [x for x in lines if x["code"] == fc.CLOSED_WEEKDAY]
    assert len(closed) == 1
    assert closed[0]["event"] == "activity_failure" and closed[0]["team"] == "activity"
    assert closed[0]["capability"] == "activity.check_feasible" and closed[0]["case_id"]


@pytest.mark.asyncio
async def test_log_line_carries_no_coordinates_or_place_names(caplog):
    _, lines = await _run(_values(), caplog)
    raw = json.dumps(lines, ensure_ascii=False)
    assert "창경궁" not in raw and "126.97" not in raw and "37.57" not in raw


@pytest.mark.asyncio
async def test_critical_disaster_code(caplog):
    result, lines = await _run(_values(restdate="연중무휴", disaster=DISASTER), caplog)
    assert result.decisions[0]["failure_code"] == fc.DISASTER_BLOCKS
    assert fc.DISASTER_BLOCKS in _codes(lines) and fc.CLOSED_WEEKDAY not in _codes(lines)


@pytest.mark.asyncio
async def test_disaster_wins_when_it_overlaps_a_closed_weekday(caplog):
    """★둘이 겹치면 위급재난이 앞선다 — 대체 장소 `withheld` 와 같은 우선순위."""
    result, lines = await _run(_values(disaster=DISASTER), caplog)
    assert result.decisions[0]["failure_code"] == fc.DISASTER_BLOCKS
    assert fc.CLOSED_WEEKDAY not in _codes(lines)


@pytest.mark.asyncio
async def test_already_started_and_over_capacity(caplog):
    started, lines = await _run(_values(starts_at=datetime.now(KST) - timedelta(hours=3)), caplog)
    assert started.decisions[0]["failure_code"] == fc.ALREADY_STARTED
    assert _codes(lines) == [fc.ALREADY_STARTED]
    caplog.clear()
    over, lines = await _run(_values(party=9, capacity=4), caplog)
    assert over.decisions[0]["failure_code"] == fc.PARTY_OVER_CAPACITY
    assert _codes(lines) == [fc.PARTY_OVER_CAPACITY]


@pytest.mark.asyncio
async def test_unknown_place_is_insufficient_info_with_a_code(caplog):
    result, lines = await _run({**_values(), "read.place": None}, caplog)
    assert result.decisions[0]["status"] == "insufficient_info"
    assert result.decisions[0]["failure_code"] == fc.PLACE_UNKNOWN
    assert _codes(lines) == [fc.PLACE_UNKNOWN]


@pytest.mark.asyncio
async def test_a_feasible_booking_leaves_no_code_and_no_log(caplog):
    result, lines = await _run(_values(restdate="매주 화요일 휴무"), caplog)
    assert result.decisions[0]["feasible"] is True
    assert "failure_code" not in result.decisions[0] and lines == []


# ── 대체 장소 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alternatives_failures_are_logged_but_the_result_shape_is_unchanged(caplog):
    withheld, lines = await _run(_values(disaster=DISASTER), caplog)
    assert withheld.decisions[0]["alternatives"] == {"status": "withheld", "reason": "disaster_blocks"}
    assert fc.ALTERNATIVES_WITHHELD in _codes(lines)
    caplog.clear()
    _, lines = await _run(_values(content_id=None), caplog)
    assert fc.ALTERNATIVES_NO_CONTENT_ID in _codes(lines)
    caplog.clear()
    _, lines = await _run(_values(pool=None), caplog)
    assert fc.ALTERNATIVES_POOL_UNAVAILABLE in _codes(lines)


@pytest.mark.asyncio
async def test_no_candidate_and_only_unconfirmed_candidates_are_told_apart(caplog):
    empty = {**POOL, "candidates": [_row("closed", "월요일 휴무 궁궐", closed="매주 월요일")]}
    _, lines = await _run(_values(pool=empty), caplog)
    assert fc.ALTERNATIVES_NONE in _codes(lines)
    caplog.clear()
    unconfirmed = {**POOL, "candidates": [_row("u", "운영 미확인 궁궐", closed=None)]}
    _, lines = await _run(_values(pool=unconfirmed), caplog)
    assert fc.ALTERNATIVES_UNCONFIRMED in _codes(lines) and fc.ALTERNATIVES_NONE not in _codes(lines)


# ── 일정 제출 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_not_found_asks_the_customer_again_with_a_code(caplog):
    caplog.set_level(logging.WARNING, logger=fc.LOGGER_NAME)
    ctx = pack("activity", scope=["activity"],
               state={"requested_place_name": "없는곳", "requested_activity_time": datetime.now(KST) + timedelta(days=3)})
    result = await ActivityTeam(FakeTools({"read.place_lookup": {"status": "not_found", "via": "place_catalog"}})).execute(
        task("activity", "activity.submit_itinerary", ctx, ALLOWED))
    assert result.next_action.value == "wait_for_input"
    assert result.decisions[0]["failure_code"] == fc.PLACE_NOT_FOUND
    lines = _lines(caplog)
    assert _codes(lines) == [fc.PLACE_NOT_FOUND]
    assert "없는곳" not in json.dumps(lines, ensure_ascii=False)       # 고객이 쓴 이름은 로그에 안 남긴다


# ── 도구 예외 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_exception_is_logged_with_its_name_and_rethrown_not_swallowed(caplog):
    """★API·DB 예외를 「모름」으로 바꾸지 않는다(RULE §3.2) — 코드만 남기고 그대로 던진다."""
    values = {**_values(), "read.place": RuntimeError("boom 37.5796,126.9770 secret-url")}
    with pytest.raises(RuntimeError, match="boom"):
        await _run(values, caplog, tools_cls=RaisingTools)
    lines = _lines(caplog)
    assert _codes(lines) == [fc.TOOL_ERROR]
    assert lines[0]["tool"] == "read.place" and lines[0]["error"] == "RuntimeError"
    assert "boom" not in json.dumps(lines) and "secret" not in json.dumps(lines)    # 예외 문구는 안 싣는다
