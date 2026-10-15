# -*- coding: utf-8 -*-
"""웹 채팅의 「항공편 다른 걸로 보여 줘」가 **항공 팀**에 닿는다. `[2026-10-15 숙소 고침(LB-02)과 같은 줄기 — 항공]`

☆왜: 숙소(`test_chat_lodging_change_web.py`)와 같은 결함이 항공에도 있었다. 채팅은 요청이 가리킨 일정의 종류로 담당 팀을 정하는데 항공을 몰라서 항공편 항목을 가리킨 요청이
  **활동 팀**으로 넘어갔고, 항공 팀에 닿아도 공용 처리가 식당 · 활동의 장소 목록으로 계획해 항공 팀 자신의 「바꿔 줘」(`FlightTeam._change`)는 웹 채팅에서 불린 적이 없었다.
  되묻기 목록에서 항공편을 고르는 길(`case_dispatch._resume_route`)과 항공 팀이 되묻기를 항목 없이 맡는 길도 막혀 있었다.

★지키려는 것
 ①항공편 항목을 가리킨 「바꿔 줘」 · 「다른 편」은 항공 팀 일정 관리(`flight.itinerary`)로 가고 그 팀의 답이 고객에게 나간다
 ②식당 요청은 그대로 · 숙소 요청은 여전히 숙소 팀
 ③되묻기 번호 고르기에서 항공편을 고르면 항공 팀으로 간다
 ④해석 모델에 보이는 일정 목록이 항공편을 「항공편」이라고 부른다(영어 종류 이름 `flight` 를 그대로 보이지 않는다)

★해석 모델과 항공 팀의 실제 검색(MCP)은 **자동 시험용 고정 대역**이다 — 이 시험은 채팅이 일을 누구에게 맡기는가(배선)를 본다. 후보 검색 · 고르기는
  `tests/unit/travel/booking/test_flight_team.py` · `tests/integration/travel/test_lodging_flight_options.py`.

재현:

    python -m pytest tests/e2e/test_chat_flight_change_web.py -v
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest

from app.core.contracts import NextAction
from tests.helpers.plugin_bindings import publish_fixed_mobility

from .test_trip_api import DAY
from .test_web_api import _fresh_limit_cache, _h, _session, _web_body, api  # noqa: F401 — 시험 준비물


class _Decider:
    """해석 모델 흉내 — 「항공 …」은 항공편, 「점심 …」은 성수 점심을 고른다. 받은 일정 목록(`prompts`)도 적어 둔다."""

    def __init__(self):
        self.prompts: list[str] = []

    @staticmethod
    def _alias(user: str, name: str) -> str:
        for line in user.split("\n"):
            if re.match(r"i\d+ \|", line) and name in line:
                return line.split(" |")[0]
        return "none"

    def structured(self, system, user, schema, *, num_predict=160):
        self.prompts.append(user)
        message = user.rsplit("고객 문장:", 1)[1].strip()
        none = {"target_change": "none", "fact": "none", "minutes": 0, "question": ""}
        if "항공 다른 편" in message:
            return {**none, "action": "propose_alternatives", "target_item": self._alias(user, "김포 → 제주")}
        if "항공 바꿔" in message:
            return {**none, "action": "apply_change", "target_item": self._alias(user, "김포 → 제주")}
        if "점심 다른 곳" in message:
            return {**none, "action": "propose_alternatives", "target_item": self._alias(user, "성수 점심")}
        if "무엇을" in message:
            flight, lunch = self._alias(user, "김포 → 제주"), self._alias(user, "성수 점심")
            return {**none, "action": "clarify", "target_item": "none",
                    "choices": [{"action": "propose_alternatives", "target_item": flight, "target_change": "none", "fact": "none"},
                                {"action": "propose_alternatives", "target_item": lunch, "target_change": "none", "fact": "none"}]}
        if "점심 먼저" in message:         # 항공편이 둘째 후보 — 되묻기 번호 고르기가 고른 항목의 팀을 직접 찾아야 한다
            flight, lunch = self._alias(user, "김포 → 제주"), self._alias(user, "성수 점심")
            return {**none, "action": "clarify", "target_item": "none",
                    "choices": [{"action": "propose_alternatives", "target_item": lunch, "target_change": "none", "fact": "none"},
                                {"action": "propose_alternatives", "target_item": flight, "target_change": "none", "fact": "none"}]}
        return {**none, "action": "other", "target_item": "none"}


@pytest.fixture()
def flight_chat(api, monkeypatch):
    from fastapi.testclient import TestClient

    from app.domains.travel_ops.components.conversation import trip_messages
    from app.domains.travel_ops.entry.trip_api import build_trip_router
    from app.domains.travel_ops.instances.booking import FlightTeam
    from app.presentation.api.app import create_app

    calls: list[dict] = []
    decider = _Decider()

    def stand_in(self, task, ctx):
        """항공 팀 자신의 「바꿔 줘」(`FlightTeam._change`) — 어떤 항목으로 불렸는지만 적고 후보 대신 정해진 답을 낸다(실제 검색 없음)."""
        ref = ctx["ref"]
        calls.append({"team": self.manifest.team_id, "capability": task.capability, "part_id": ref.get("part_id"),
                      "action": ctx["request"].get("action")})
        evidence = self._evidence(task, source_id="read.itinerary", claim="현재 일정 버전", value={"stand_in": True})
        return self._result(task, outcome="completed", confidence=1.0, evidence=evidence, next_action=NextAction.RESPOND,
                            answer="김포 → 제주 대신 탈 수 있는 편이에요 — 1) 제주항공 7C151 2) 티웨이 TW701 · 고르시면 바꿀게요.",
                            decisions=[{"itinerary": "asked", "item": "김포 → 제주"}])

    monkeypatch.setattr(FlightTeam, "_change", stand_in)
    monkeypatch.setattr(trip_messages, "_decision_mode", lambda chat, tenant=None: "on")
    client = TestClient(create_app(
        classifier=lambda _m: {"intent": "other", "issue_code": "other", "sentiment": "neutral"},
        domain_routers=[build_trip_router(classifier_factory=lambda: (lambda m: {"intent": "other", "issue_code": "other",
                                                                                 "sentiment": "neutral"}),
                                          chat_factory=lambda: decider)]))
    publish_fixed_mobility(client.app, source="chat_flight_test")
    me = _session(api)
    body = _web_body(api, request_id="flt-" + uuid4().hex[:6])
    last = max(it["seq"] for it in body["items"])
    body["items"].append({"seq": last + 1, "kind": "flight", "title": "김포 → 제주", "place": None, "route": None,
                          "starts_at": f"{DAY}T21:00:00+09:00", "ends_at": f"{DAY}T22:10:00+09:00",
                          "detail": {"origin": "GMP", "destination": "CJU"}})
    trip = client.post("/v1/web/trips", json=body, headers=_h(me["user_key"]))
    assert trip.status_code in (200, 201), trip.text
    yield {"client": client, "key": me["user_key"], "trip": trip.json(), "calls": calls, "decider": decider}


def _say(chat, text, request_id, **extra):
    response = chat["client"].post(f"/v1/web/trips/{chat['trip']['trip_id']}/messages", headers=_h(chat["key"]),
                                   json={"request_id": request_id, "message": text, "at": f"{DAY}T09:00:00+09:00", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_asking_for_another_flight_goes_to_the_flight_team_and_its_answer_reaches_the_customer(flight_chat):
    body = _say(flight_chat, "항공 다른 편으로 보여 줘", "flt-a")
    assert flight_chat["calls"], f"항공 팀이 불리지 않았다: {body}"
    call = flight_chat["calls"][0]
    assert call["team"] == "flight" and call["capability"] == "flight.itinerary" and call["action"] == "propose_alternatives", call
    assert "제주항공 7C151" in body["answer"], body
    flight = next(i for i in flight_chat["trip"]["items"] if i["kind"] == "flight")
    assert call["part_id"] == flight["item_id"], (call, flight)


def test_the_change_request_form_goes_to_the_flight_team_too(flight_chat):
    _say(flight_chat, "항공 바꿔 줘", "flt-b")
    assert [c["team"] for c in flight_chat["calls"]] == ["flight"], flight_chat["calls"]


def test_a_lunch_request_still_goes_to_a_dining_team_not_the_flight_team(flight_chat):
    _say(flight_chat, "점심 다른 곳으로 보여 줘", "flt-c")
    assert flight_chat["calls"] == [], "식당 요청은 항공 팀이 받지 않는다"


def test_picking_the_flight_from_a_clarify_list_goes_to_the_flight_team(flight_chat):
    ask = _say(flight_chat, "무엇을 바꿀까", "flt-d1")
    assert ask["status"] == "clarify" and ask["choices"], ask
    number = next(n for n, c in enumerate(ask["choices"], start=1) if "김포" in (c.get("label") or c.get("message") or ""))
    picked = _say(flight_chat, f"{number}번", "flt-d2", case_id=ask["case_id"])
    assert "제주항공 7C151" in picked["answer"], picked
    assert [c["team"] for c in flight_chat["calls"]] == ["flight"], flight_chat["calls"]


def test_picking_the_flight_as_the_second_choice_finds_the_flight_team_from_the_picked_item(flight_chat):
    ask = _say(flight_chat, "점심 먼저 볼까", "flt-f1")
    assert ask["status"] == "clarify" and len(ask["choices"]) == 2, ask
    number = next(n for n, c in enumerate(ask["choices"], start=1) if "김포" in (c.get("label") or c.get("message") or ""))
    assert number == 2, ask["choices"]
    picked = _say(flight_chat, f"{number}번", "flt-f2", case_id=ask["case_id"])
    assert "제주항공 7C151" in picked["answer"], picked
    assert [c["team"] for c in flight_chat["calls"]] == ["flight"], flight_chat["calls"]


def test_the_item_list_the_model_sees_calls_a_flight_a_flight_in_korean(flight_chat):
    _say(flight_chat, "항공 다른 편으로 보여 줘", "flt-e")
    line = next(l for l in flight_chat["decider"].prompts[-1].split("\n") if "김포 → 제주" in l and re.match(r"i\d+ \|", l))
    assert "항공편" in line and "flight" not in line, line
