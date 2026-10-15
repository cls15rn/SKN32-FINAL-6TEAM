# -*- coding: utf-8 -*-
"""실제 사용자 흐름 여섯 단계 — 로그인 → 접수 → 질문 → 대안 → 변경 → 재조회를 **한 사람이 이어서** 한다. `[2026-10-15]`

6주차 점검에서 「로그인 → 접수 → 질문 → 대안 → 변경 → 재조회 전체의 인수 근거 없음」이 나왔다. 조각 시험은 많지만
(`test_consents` · `test_trip_api` · `test_decision_unit_web` …) 한 사람이 쿠키 하나로 처음부터 끝까지 가는 시험은 없었다.
이 시험이 그 흐름을 **약관 동의가 필요한 새 게스트**로 처음부터 끝까지 한 번에 돈다.

★이 시험이 보는 것(서버 응답): 쿠키 세션 → 동의 게이트(403 → 동의 → 통과) → 여행 접수 → 사실 질문 답 → 대안 찾기(후보만, 일정 그대로)
  → 변경(버전 +1) → 쿠키를 새 클라이언트로 옮겨 다시 읽어도 같은 일정·대화·변경 이력 · 다른 사람은 못 읽음.
★이 시험이 **못** 보는 것: 해석 모델은 자동 시험용 고정 규칙이다(실제 모델 아님) — 모델 정확도·응답 시간은 재지 않는다.
  화면 반응(브라우저)도 아니다. 실서버(x600·오라클)를 부르지 않는다. 그래서 이 시험의 통과는 「서버 응답 계약이 흐름 끝까지 이어진다」까지만 뜻한다.

재현:

    python -m pytest tests/e2e/test_user_flow_six_steps.py -v
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domains.travel_ops.components.customer import consents
from app.infrastructure.db.session import get_connection
from tests.helpers.plugin_bindings import publish_fixed_mobility

from .test_decision_unit_web import _Decider
from .test_trip_api import _body, api  # noqa: F401 — 픽스처를 그대로 쓴다
from .test_web_cookie_session import WEB, FakeGoogle, cookies  # noqa: F401 — 픽스처를 그대로 쓴다

VERSION = consents.get_guardrails().get("consent.terms_version")
SHA = "a" * 64


class _FlowDecider(_Decider):
    """`_Decider` 에 「다른 데 알아봐 줘」(대안만 찾는다)를 더한다."""

    def structured(self, system, user, schema, *, num_predict=160):
        message = user.rsplit("고객 문장:", 1)[1].strip()
        if "알아봐" in message:
            self.prompts.append(user)
            return {"action": "propose_alternatives", "target_item": self._alias(user, "성수 점심"), "target_change": "none",
                    "fact": "none", "minutes": 0, "question": "", "choices": []}
        return super().structured(system, user, schema, num_predict=num_predict)


@pytest.fixture()
def flow(cookies, monkeypatch):  # noqa: F811
    """동의 게이트가 켜진 서버 + 해석 모델 고정 규칙. 끝나면 이 시험이 만든 동의 기록을 지운다(정리 작업과 같은 문을 연다)."""
    from app.domains.travel_ops.components.conversation import trip_messages
    from app.domains.travel_ops.entry.trip_api import build_trip_router
    from app.domains.travel_ops.modules.web_account import web_guard
    from app.domains.travel_ops.modules.web_account.web_auth_api import build_auth_router
    from app.presentation.api.app import create_app

    monkeypatch.setattr(consents, "gate_enabled", lambda: True)
    decider = _FlowDecider()
    monkeypatch.setattr(trip_messages, "_decision_mode", lambda chat, tenant=None: "on")
    real = web_guard.values
    monkeypatch.setattr(web_guard, "values", lambda tenant: {**real(tenant), "web.dev_mode": "on"})

    def make_client():
        return TestClient(create_app(
            classifier=lambda _m: {"intent": "other", "issue_code": "other", "sentiment": "neutral"},
            domain_routers=[build_trip_router(classifier_factory=lambda: (lambda m: {"intent": "other", "issue_code": "other",
                                                                                     "sentiment": "neutral"}),
                                              chat_factory=lambda: decider),
                            build_auth_router(exchange=FakeGoogle())]), follow_redirects=False)

    client = make_client()
    publish_fixed_mobility(client.app, source="user_flow_test")
    yield {**cookies, "client": client, "decider": decider, "make_client": make_client, "steps": []}
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL app.consent_purge = 'on'")
        cur.execute("DELETE FROM consent_events WHERE tenant_id=%s", (cookies["tenant"],))


def _csrf(client) -> dict:
    return {"Origin": WEB, "X-CSRF-Token": client.get("/v1/web/auth/me").json()["csrf_token"]}


def _timed(flow, name, call):
    started = time.monotonic()
    response = call()
    flow["steps"].append({"step": name, "status_code": response.status_code, "seconds": round(time.monotonic() - started, 3)})
    return response


def _say(flow, trip_id, text, request_id, client=None):
    client = client or flow["client"]
    return _timed(flow, f"message:{request_id}", lambda: client.post(
        f"/v1/web/trips/{trip_id}/messages", headers=_csrf(client),
        json={"request_id": request_id, "message": text, "at": "2030-01-01T09:00:00+09:00"}))


def test_a_new_guest_goes_from_login_to_a_changed_and_reread_trip_in_one_cookie(flow):
    client = flow["client"]

    # ① 로그인 — 새 게스트. 쿠키만 받고, 동의 전에는 여행 목록도 못 연다.
    session = _timed(flow, "1-login", lambda: client.post("/v1/web/auth/session", headers={"X-Forwarded-For": flow["ip"]}))
    assert session.status_code == 201 and session.json()["kind"] == "guest" and "user_key" not in session.json()
    blocked = _timed(flow, "1-gate-blocks", lambda: client.get("/v1/web/trips"))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "consent_required"
    assert client.get("/v1/web/auth/me").status_code == 200                      # 면제 길: 로그인 상태 확인
    agreed = _timed(flow, "1-consent", lambda: client.post("/v1/web/consents", headers=_csrf(client), json={
        "version": VERSION, "items": [{"code": "service_terms", "agreed": True, "text_sha256": SHA},
                                      {"code": "privacy", "agreed": True, "text_sha256": SHA}]}))
    assert agreed.status_code == 200 and agreed.json()["ok"] is True, agreed.text
    assert _timed(flow, "1-gate-opens", lambda: client.get("/v1/web/trips")).status_code == 200

    # ② 접수 — 여행을 만든다(일정이 짜인다).
    body = {k: v for k, v in _body(flow["customer"], "flow-1").items() if k != "customer_id"}
    created = _timed(flow, "2-intake", lambda: client.post("/v1/web/trips", headers=_csrf(client), json=body))
    assert created.status_code in (200, 201), created.text
    trip = created.json()
    trip_id = trip["trip_id"]
    assert trip["version"] == 1 and trip["items"], trip
    lunch = next(i for i in trip["items"] if "성수 점심" in i["title"])

    # ③ 질문 — 일정의 사실을 묻는다(일정은 그대로).
    answered = _say(flow, trip_id, "성수 점심 식당 알려줘", "flow-q").json()
    assert answered["status"] == "answered" and answered["decision"]["action"] == "answer_fact", answered
    assert "성수 점심" in answered["answer"]

    # ④ 대안 — 후보만 보여 주고 일정은 안 바꾼다.
    asked = _say(flow, trip_id, "점심 다른 데 알아봐 줘", "flow-alt").json()
    assert asked["status"] == "asked", asked
    names = [o["name"] for o in asked["outcome"]["options"]]
    assert names and lunch["title"] not in names, names                           # 원래 곳은 후보에 없다
    still = client.get(f"/v1/web/trips/{trip_id}").json()
    assert still["version"] == 1, "대안을 찾는 것만으로 일정이 바뀌면 안 된다"

    # ⑤ 변경 — 실제로 바꾼다(버전이 오른다).
    changed = _say(flow, trip_id, "점심 다른 데로 바꿔", "flow-chg").json()
    assert changed["status"] == "adjusted", changed
    after = _timed(flow, "5-read-after-change", lambda: client.get(f"/v1/web/trips/{trip_id}")).json()
    assert after["version"] == 2, after["version"]
    new_lunch = next(i for i in after["items"] if i["item_id"] == lunch["item_id"] or i["seq"] == lunch["seq"])
    assert new_lunch["title"] != lunch["title"] or new_lunch.get("place") != lunch.get("place"), "바꿨다는데 일정이 그대로"

    # ⑥ 재조회 — 같은 쿠키를 **새 클라이언트**(새로 고침·다른 탭)로 옮겨 읽는다. 같은 일정 · 대화 · 변경 이력이어야 한다.
    reloaded = flow["make_client"]()
    reloaded.cookies.update(client.cookies)
    reread = _timed(flow, "6-reread", lambda: reloaded.get(f"/v1/web/trips/{trip_id}")).json()
    assert reread["version"] == 2 and [i["title"] for i in reread["items"]] == [i["title"] for i in after["items"]]
    turns = reloaded.get(f"/v1/web/trips/{trip_id}/chat").json()["turns"]
    assert [t["role"] for t in turns] == ["customer", "assistant"] * 3, [t["role"] for t in turns]
    assert turns[0]["text"] == "성수 점심 식당 알려줘"

    # 다른 사람(새 게스트)은 이 여행을 못 본다.
    stranger = flow["make_client"]()
    stranger.post("/v1/web/auth/session", headers={"X-Forwarded-For": flow["ip"]})
    stranger.post("/v1/web/consents", headers=_csrf(stranger), json={
        "version": VERSION, "items": [{"code": "service_terms", "agreed": True, "text_sha256": SHA},
                                      {"code": "privacy", "agreed": True, "text_sha256": SHA}]})
    assert stranger.get(f"/v1/web/trips/{trip_id}").status_code == 404

    evidence = os.environ.get("FLOW_EVIDENCE")
    if evidence:
        Path(evidence).write_text(json.dumps({"steps": flow["steps"]}, ensure_ascii=False, indent=1), encoding="utf-8")


def test_a_guest_who_has_not_agreed_cannot_reach_any_trip_step(flow):
    """동의 없이는 접수·질문·재조회가 모두 막힌다 — 게이트가 흐름 한가운데 구멍이 아니다."""
    client = flow["client"]
    client.post("/v1/web/auth/session", headers={"X-Forwarded-For": flow["ip"]})
    body = {k: v for k, v in _body(flow["customer"], "flow-2").items() if k != "customer_id"}
    assert client.post("/v1/web/trips", headers=_csrf(client), json=body).status_code == 403
    assert client.get("/v1/web/trips").status_code == 403
