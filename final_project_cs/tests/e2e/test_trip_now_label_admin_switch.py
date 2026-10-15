# -*- coding: utf-8 -*-
"""관리 콘솔의 「여행 화면 「지금 HH:MM」 시각 표시」 스위치 — 기본 켜짐, 운영 API 로 끄면 여행 조회의 `now_label` 이 false. `[2026-10-15 사용자 결정 — ST4F-494]`

관리 콘솔 화면은 `/admin/limits` 가 주는 목록을 그대로 그린다(참/거짓은 켜짐 · 꺼짐 고르기). 그래서 목록에 있고
저장이 먹으면 화면에도 스위치가 생긴다. 계약: `wiki/external/rest-endpoints/web-trips.md` 「여행 화면 「지금」 시각 표시」.
"""
from __future__ import annotations

import pytest

from app.domains.travel_ops.components import settings_hook
from app.domains.travel_ops.modules.web_account import web_guard

from .test_trip_api import _create, api  # noqa: F401 — 픽스처를 그대로 쓴다
from .test_web_guard import _client

NAME = settings_hook.TRIP_NOW_LABEL


@pytest.fixture(autouse=True)
def _operator_values():
    web_guard.clear_cache()
    settings_hook.register(lambda tenant, name: web_guard.values(tenant).get(name))
    yield
    settings_hook.clear()
    web_guard.clear_cache()


def _now_label(api, trip_id):
    response = api["client"].get(f"/v1/trips/{trip_id}", headers=api["auth"]("trip:read"))
    assert response.status_code == 200, response.text
    return response.json()["now_label"]


def test_the_console_lists_the_switch_on_and_turning_it_off_reaches_the_trip_view(api):
    trip_id = _create(api)["trip_id"]
    client = _client()
    read, write = api["auth"]("limits:read"), api["auth"]("limits:write")
    view = client.get("/admin/limits", headers=read).json()
    row = next(r for r in view["limits"] if r["name"] == NAME)
    assert (row["type"], row["value"], row["source"]) == ("bool", True, "default")
    assert _now_label(api, trip_id) is True

    off = client.patch("/admin/limits", headers=write, json={
        "expected_revision": view["revision"], "actor": "op-kim", "reason": "배포 — 시험 표시 끄기", "changes": {NAME: False}})
    assert off.status_code == 200, off.text
    assert next(r for r in off.json()["limits"] if r["name"] == NAME)["value"] is False
    assert _now_label(api, trip_id) is False

    back = client.patch("/admin/limits", headers=write, json={
        "expected_revision": off.json()["revision"], "actor": "op-kim", "reason": "되돌림", "changes": {NAME: None}})
    assert back.status_code == 200, back.text
    assert _now_label(api, trip_id) is True
