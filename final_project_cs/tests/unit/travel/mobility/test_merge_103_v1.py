# -*- coding: utf-8 -*-
"""합치기 3(103 · 2026-10-05) — 팀장 `role-manager` `f142e0f` 의 이동 자리 변경을 받음(따릉이 실시간 조회의 호출 한도 문).

받은 것: `engine/bike.py`(BikeLive 에 문) · `engine/runtime.py`·`wiring.py`(문 넘기기) · `route_shape.py`·`test_route_shape.py`.
안 받은 것: 코어 호출 한도 묶음(`infrastructure/travel/source_budget.py` 등)과 그것을 직접 쓰는 팀장 시험 `test_bike_live_gate.py`
  — 팀장 묶음이 develop 에 들어올 때 같이 온다. 그때까지 이 파일이 **이동 쪽 문 동작**을 코어 import 없이 대역 문으로 잠근다.

잠그는 것
① 한도에 걸리면 부르지 않고 「모름」 — 판정은 「불가」로도 「거치 확인됨」으로도 바뀌지 않는다(거치 미상 · 등급 근거없음 · 경고).
② 문이 없으면(시험 · 명령줄 · 문을 안 넘긴 기기) 예전대로 부른다 · 픽스처는 문을 안 건드린다.
③ 문의 결함(이유 이름이 없는 예외)은 한도로 삼키지 않는다.
④ 서버 배선: 키가 있는데 문이 없으면 실시간을 끈다 · 문이 오면 그대로 넘긴다.
⑤ 합치며 살아 있어야 하는 우리 것: `gh_url` 은 받고 버린다(계산기로 안 넘김) · 계산기 인자에 `gh_url` 없음 · `BikeRouter.route_ex` ·
   `_LazyCar.arrive_by` · 엔진 안 서버 주소 읽기 0.
"""
from __future__ import annotations

import inspect
import io
import json
import urllib.request

import pytest

from app.modules.travel_ops.mobility import wiring
from app.modules.travel_ops.mobility.engine import bike as B
from app.modules.travel_ops.mobility.engine import runtime as RT
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
ST1 = {"stationId": "ST-1", "name": "대여소1", "lat": 37.5000, "lon": 127.0000, "mode": "QR", "rack": 10}
ST2 = {"stationId": "ST-2", "name": "대여소2", "lat": 37.5100, "lon": 127.0100, "mode": "QR", "rack": 10}
LEG = {"mode": "bike", "from": {"lat": 37.5001, "lng": 127.0001, "name": "출발"},
       "to": {"lat": 37.5101, "lng": 127.0101, "name": "도착"}}


class Spent(RuntimeError):
    """코어 `RateLimited` 갈래의 대역 — 이유 이름(`reason`)이 있는 예외."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Gate:
    def __init__(self, exc=None):
        self.exc, self.names = exc, []

    def acquire(self, name):
        self.names.append(name)
        if self.exc is not None:
            raise self.exc


def _net(monkeypatch, n=3):
    hits = []

    def fake(url, timeout=None):
        hits.append(url)
        doc = {"rentBikeStatus": {"row": [{"stationId": "ST-1", "parkingBikeTotCnt": str(n)}]}}
        return io.BytesIO(json.dumps(doc).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _Ctx(fake(url, timeout)))
    return hits


class _Ctx:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self.f

    def __exit__(self, *a):
        return False


def _verifier(**kw):
    from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier
    return Verifier(Timetable(), None, RULES, set(), **kw)


# ── ① 한도에 걸리면 부르지 않고 모름 ──────────────────────────────────
@pytest.mark.parametrize("reason", ["budget_exhausted", "budget_unavailable", "rate_limited"])
def test_spent_gate_makes_no_call_and_says_why(monkeypatch, reason):
    hits = _net(monkeypatch)
    live = B.BikeLive(key="k", gate=Gate(Spent(reason)))
    assert live.get("ST-1") is None
    assert hits == [] and live.calls == 0, "한도에 걸리면 바깥으로 나가지 않는다"
    assert live.last_error == {"kind": "budget", "reason": reason}


def test_spent_gate_leaves_the_verdict_unknown_not_flipped(monkeypatch):
    """한도에 걸린 조회 = 거치 확인 못 함. 「불가」(거치 0 과 같은 답)도 「거치 확인됨」도 아니다 — 통신 실패 때와 같은 답이어야 한다."""
    hits = _net(monkeypatch)
    v = _verifier(bk=B.BikeStations([ST1, ST2]), bike_live=B.BikeLive(key="k", gate=Gate(Spent("budget_exhausted"))))
    r = v.verify_leg_bike(1, LEG, 600, "weekday", party={"size": 1})
    assert hits == []
    assert r.verdict == "feasible", "한도 때문에 「불가」로 바뀌면 안 된다"
    assert r.grade == "근거없음" and "거치 미상" in r.reason, "거치를 확인한 것처럼 내면 안 된다"
    assert any("MOB_W_BIKE_LIVE_UNKNOWN" in json.dumps(w, ensure_ascii=False) for w in r.warnings)
    assert not any("거치 " in (e.get("claim") or "") and e.get("grade") == "확정" and "대 (" in (e.get("claim") or "")
                   for e in r.evidence if isinstance(e, dict)), "거치 대수 근거가 붙으면 안 된다"

    v0 = _verifier(bk=B.BikeStations([ST1, ST2]), bike_live=None)
    r0 = v0.verify_leg_bike(1, LEG, 600, "weekday", party={"size": 1})
    assert (r.verdict, r.grade, r.arrive_min, r.ride_min) == (r0.verdict, r0.grade, r0.arrive_min, r0.ride_min), \
        "실시간이 꺼진 판정과 같은 값(문구의 까닭만 다르다)"


def test_empty_station_is_still_infeasible_when_the_gate_is_open(monkeypatch):
    """문이 열려 있고 거치가 0 이면 종전대로 「불가」 — 한도 문이 이 답을 바꾸지 않는다."""
    _net(monkeypatch, n=0)
    gate = Gate()
    v = _verifier(bk=B.BikeStations([ST1, ST2]), bike_live=B.BikeLive(key="k", gate=gate))
    r = v.verify_leg_bike(1, LEG, 600, "weekday", party={"size": 1})
    assert r.verdict == "infeasible" and "거치" in r.reason and gate.names == [B.BikeLive.GATE_NAME]


# ── ② 문이 없을 때 · 픽스처 ──────────────────────────────────────────
def test_no_gate_calls_as_before(monkeypatch):
    hits = _net(monkeypatch)
    live = B.BikeLive(key="k")
    got = live.get("ST-1")
    assert got and got["available"] == 3 and len(hits) == 1 and live.gate is None


def test_open_gate_is_asked_once_per_call_by_the_meter_name(monkeypatch):
    hits = _net(monkeypatch)
    gate = Gate()
    live = B.BikeLive(key="k", gate=gate)
    assert live.get("ST-1")["available"] == 3
    assert gate.names == ["seoul_bike"] and len(hits) == 1


def test_fixture_and_missing_key_never_touch_the_gate():
    gate = Gate(Spent("budget_exhausted"))
    fx = B.BikeLive(fixture={"checked_at": "t", "counts": {"ST-1": 2}}, gate=gate)
    assert fx.get("ST-1")["available"] == 2
    nokey = B.BikeLive(key=None, gate=gate)
    assert nokey.get("ST-1") is None and nokey.last_error == {"kind": "no_key"}
    assert gate.names == []


# ── ③ 문의 결함은 삼키지 않는다 ──────────────────────────────────────
def test_gate_bug_is_raised_not_read_as_budget(monkeypatch):
    _net(monkeypatch)
    live = B.BikeLive(key="k", gate=Gate(TypeError("결함")))
    with pytest.raises(TypeError):
        live.get("ST-1")


# ── ④ 서버 배선 ─────────────────────────────────────────────────────
def _settings(key):
    class S:
        seoul_openapi_key = key
        mobility_data_dir = ""
        mobility_gh_url = "http://gh.example:8989"
        guardrails_path = "config/guardrails.yaml"
    return S()


def _seen(monkeypatch):
    seen = {}
    monkeypatch.setattr(wiring, "configure", lambda **kw: seen.update(kw) or {"mode": "disabled"})
    return seen


def test_wiring_turns_live_off_when_key_has_no_gate(monkeypatch):
    seen = _seen(monkeypatch)
    wiring.configure_from_settings(_settings("k"), preload=False)
    assert seen["seoul_key"] == "" and seen["bike_gate"] is None


def test_wiring_passes_the_gate_and_key_through(monkeypatch):
    seen = _seen(monkeypatch)
    gate = Gate()
    wiring.configure_from_settings(_settings("k"), preload=False, bike_gate=gate)
    assert seen["seoul_key"] == "k" and seen["bike_gate"] is gate


def test_wiring_without_key_needs_no_gate(monkeypatch):
    seen = _seen(monkeypatch)
    wiring.configure_from_settings(_settings(""), preload=False)
    assert seen["seoul_key"] == "" and seen["bike_gate"] is None


# ── ⑤ 합치며 살아 있어야 하는 우리 것 ─────────────────────────────────
def test_gh_url_is_accepted_but_never_reaches_the_engine(monkeypatch):
    """팀 시험·설정이 `gh_url` 로 불러도 받기만 하고 계산기로 넘기지 않는다(경로 서버 호출 0 · 99·101) — 문을 받으면서도 그대로."""
    assert "gh_url" in inspect.signature(wiring.configure).parameters
    src = inspect.getsource(wiring.configure)
    params = inspect.signature(RT.build_verifier).parameters
    assert "gh_url" not in params and "bike_gate" in params and "local_router" in params
    seen = _seen(monkeypatch)
    wiring.configure_from_settings(_settings("k"), preload=False, bike_gate=Gate())
    assert "gh_url" not in seen, "설정의 mobility_gh_url 을 configure 로 넘기지 않는다"
    assert '"gh_url"' not in src and '"bike_gate": bike_gate' in src, "계산기로 가는 인자 묶음에 gh_url 이 없고 문은 있다"


def test_engine_still_reads_no_router_server_address():
    src = inspect.getsource(RT)
    assert "MOBILITY_GH_URL" not in src and "urllib" not in src, "문을 받으면서 팀장 판의 서버 주소 읽기가 되살아나면 안 된다"
    assert "gate=bike_gate" in src and '"bike_gate": bike_gate' in src


def test_our_merged_pieces_survive():
    assert callable(getattr(B.BikeRouter, "route_ex", None))
    assert callable(getattr(RT._LazyCar, "arrive_by", None))
    from app.modules.travel_ops.mobility.engine import verify_time as VT
    assert callable(VT.foot_walk) and callable(VT.foot_router_of)
