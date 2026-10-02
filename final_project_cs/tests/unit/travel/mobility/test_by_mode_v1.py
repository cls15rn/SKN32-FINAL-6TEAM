# -*- coding: utf-8 -*-
"""수단별 대표 후보(봉투 `by_mode`) — 93번 방(2026-10-02 · 본인 요구).

plan() 이 내는 최적 경로(계획 수단)에 더해 **지하철만 · 버스만 · 지하철+버스 · 택시** 칸마다 대표 하나를 경로·시각과 함께 낸다.
판정은 안 바꾼다 — 성립을 확인한 후보에서 고르기만 한다. 켤 때만(by_mode=True) 칸이 생긴다.

두 층:
  게이트(데이터 없음) — 대표 고르기(가장 늦게 떠나도 되는 후보 · 환승이 더 적은 후보가 5분 안이면 그쪽) · 환승 상한 잠금 ·
    칸 모양 · 범위 안/밖/모름 · 운행일 경계 · 없는 수단의 이유 · 혼합은 비교 조건 없이 · 택시는 자리만.
  전체층(실데이터 · mobility_full) — 범위 안/밖 · 켜도 options·계획·출발이 같다 · 대표 환승 수 ≤ 상한(동행 조건별) ·
    plan() 봉투(만든 구간 route 키 · 못 만든 구간 None · 끄면 칸 없음) · 식(출발 + 최악 소요 ≤ 다음 일정 시작).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
SDATE = date(2026, 10, 6)
SUB1 = [{"line": "02호선", "from": "잠실", "to": "성수"}]
SUB2 = [{"line": "03호선", "from": "경복궁", "to": "을지로3가"}, {"line": "02호선", "from": "을지로3가", "to": "성수"}]
BUS = [{"mode": "bus", "route": "2016", "from": "건대입구역", "to": "성수역"}]
MIX = [{"mode": "bus", "route": "152", "from": "명동", "to": "을지로2가"}, {"line": "03호선", "from": "을지로3가", "to": "경복궁"}]


def _opt(legs, start, eta, tr, key, margin=10, **kw):
    return {"eta_min": eta, "uses": P.uses_of(legs), "_legs": legs, "_route": P.label_of(legs), "_start": start,
            "_transfers": tr, "_n": key[2], "_key": key, "_margin": margin, "_slack": 0, "_walk_min": 5,
            "_walk_m": 300.4, "_fare": 1550, **kw}


def _stub(mixed=(), modes=None, limit=2, rules=None, mleft=()):
    """_by_mode 만 보는 Planner — 판정기는 환승 상한·규칙 값만, 혼합 생성은 가짜."""
    pl = P.Planner.__new__(P.Planner)
    pl.modes = set(P.DEFAULT_MODES) if modes is None else set(modes)
    pl.v = SimpleNamespace(R=rules or RULES, bus=object(), sc=object(),
                           _party_limit=lambda party: (limit, "default"), rv=lambda *k: 500)
    seen = {}

    def mixed_(a, b, sa, sb, arrive_dt, sdate, arrive_by, party, fv, cid, wlim, opts, left):
        seen["opts"], seen["cid"] = opts, cid
        left.extend(mleft)
        return list(mixed)
    pl._mixed = mixed_
    pl._seen = seen
    pl.disruptions = ()
    pl._any_station_near = lambda place, lim: True
    return pl


ST_A, ST_B = [("가역", 100, None)], [("나역", 100, None)]
PL_A, PL_B = {"name": "가", "lat": 0, "lon": 0}, {"name": "나", "lat": 0, "lon": 0}


def _run(pl, opts, bus_opts=(), left=None, why=None, r=None, arrive_by=660, rf="2026-10-06T10:00:00+09:00",
         sa=ST_A, sb=ST_B, visited=("가역→나역",), sdate=SDATE):
    left = [] if left is None else left
    rf_dt = None if rf is None else datetime.fromisoformat(rf)
    return pl._by_mode(list(opts) + list(bus_opts), list(bus_opts), left, why, r, PL_A, PL_B, sa, sb, None, sdate, arrive_by,
                       {}, True, "t", 1200, rf_dt, visited)


# ── 대표 고르기 ───────────────────────────────────────────────────────────
def test_pick_latest_departure_is_representative():
    a = _opt(SUB2, 620, 30, 1, ("rail", 0, 1))
    b = _opt(SUB2, 610, 40, 1, ("rail", 0, 2))
    assert P.Planner._by_mode_pick([b, a], 5) is a


def test_pick_yields_to_fewer_transfers_within_minutes():
    """환승이 더 적은 후보가 5분 안으로 따라오면 그쪽 · 6분이면 그대로(가장 이르지만 환승 많은 경로를 막는다 · 본인 10/2)."""
    two = _opt(SUB2, 620, 30, 2, ("rail", 0, 1))
    one_5 = _opt(SUB2, 615, 35, 1, ("rail", 0, 2))
    one_6 = _opt(SUB2, 614, 36, 1, ("rail", 0, 3))
    assert P.Planner._by_mode_pick([two, one_5], 5) is one_5
    assert P.Planner._by_mode_pick([two, one_6], 5) is two
    assert P.Planner._by_mode_pick([two, one_6], 0) is two
    zero = _opt(SUB1, 616, 34, 0, ("rail", 0, 4))
    assert P.Planner._by_mode_pick([two, one_5, zero], 5) is zero          # 양보 폭 안에서는 환승이 가장 적은 것
    late_zero = _opt(SUB1, 600, 50, 0, ("rail", 0, 5))
    assert P.Planner._by_mode_pick([two, one_5, late_zero], 5) is one_5    # 폭 밖의 더 적은 환승은 안 본다


def test_yield_rule_proposed_and_validation():
    assert _stub()._yield_min() == P.BY_MODE_YIELD_MIN_PROPOSED == 5
    r = json.loads(json.dumps(RULES))
    r["candidates"]["대표_환승_양보_분"] = {"value": 0}
    assert _stub(rules=r)._yield_min() == 0                                # 규칙에 들어가면 규칙 값이 이긴다
    for bad in (-1, 2.5, True, "5"):
        r["candidates"]["대표_환승_양보_분"] = {"value": bad}
        with pytest.raises(ValueError):
            _stub(rules=r)._yield_min()


# ── 칸 모양 ───────────────────────────────────────────────────────────────
def test_shape_four_keys_and_found_fields():
    mix = _opt(MIX, 610, 34, 1, ("mix", 0, 0))
    out = _run(_stub([mix]), [_opt(SUB2, 620, 28, 1, ("rail", 0, 1))], [_opt(BUS, 602, 40, 0, ("bus", 0, 0), margin=18)])
    assert list(out["modes"]) == list(P.BY_MODE_KEYS) == ["subway", "bus", "subway_bus", "taxi"]
    assert (out["range_from"], out["range_to"], out["range_min"]) == (
        "2026-10-06T10:00:00+09:00", "2026-10-06T11:00:00+09:00", 60)
    s = out["modes"]["subway"]
    assert s == {"status": "found", "label": "3호선 경복궁→을지로3가 → 2호선 을지로3가→성수",
                 "legs": [{"mode": "subway", "line": "3호선", "from": "경복궁", "to": "을지로3가"},
                          {"mode": "subway", "line": "2호선", "from": "을지로3가", "to": "성수"}],
                 "uses": ["3호선:경복궁", "3호선:을지로3가", "2호선:을지로3가", "2호선:성수"],
                 "depart_at": "2026-10-06T10:20:00+09:00", "arrive_at": "2026-10-06T10:48:00+09:00",
                 "eta_min": 28, "worst_min": 38, "transfers": 1, "walk_m": 300, "fare_krw": 1550, "within_range": True}
    b = out["modes"]["bus"]
    assert b["legs"] == [{"mode": "bus", "route": "2016", "from": "건대입구역", "to": "성수역"}] and b["worst_min"] == 58
    assert out["modes"]["subway_bus"]["legs"][0]["mode"] == "bus" and out["modes"]["subway_bus"]["transfers"] == 1
    # 경로 저장 금지 — 좌표·내부 칸이 나가지 않는다
    txt = json.dumps(out, ensure_ascii=False)
    assert "lat" not in txt and '"_' not in txt


def test_unknown_walk_and_fare_keys_are_dropped():
    o = _opt(SUB1, 620, 20, 0, ("rail", 0, 1), _walk_m=None, _fare=None)
    s = _run(_stub(), [o])["modes"]["subway"]
    assert "walk_m" not in s and "fare_krw" not in s                       # 모르면 키를 뺀다


def test_within_range_inside_outside_and_unknown():
    """범위 = 앞 일정 시작 ~ 다음 일정 시작. 10:00 · 11:00 이면 10:01 뒤 출발이 범위 안(59분 안) — 범위 밖이어도 시각은 낸다."""
    inside = _run(_stub(), [_opt(SUB1, 601, 49, 0, ("rail", 0, 1))])["modes"]["subway"]
    edge = _run(_stub(), [_opt(SUB1, 600, 50, 0, ("rail", 0, 1))])["modes"]["subway"]
    outside = _run(_stub(), [_opt(SUB1, 560, 90, 0, ("rail", 0, 1))])["modes"]["subway"]
    assert inside["within_range"] is True and edge["within_range"] is False and outside["within_range"] is False
    assert outside["status"] == "found" and outside["depart_at"] == "2026-10-06T09:20:00+09:00"
    first = _run(_stub(), [_opt(SUB1, 601, 49, 0, ("rail", 0, 1))], rf=None)
    assert first["range_from"] is None and first["range_min"] is None
    assert first["modes"]["subway"]["within_range"] is None                # 앞 일정 시작을 모르면 판단하지 않는다
    assert first["modes"]["subway"]["depart_at"] == "2026-10-06T10:01:00+09:00"


def test_service_day_boundary_times():
    """운행일 경계 — 도착 목표 00:30(운행일 축 24:30) · 앞 일정 시작 23:00. 밖으로는 벽시계(+1일)."""
    o = _opt(SUB1, 1440 + 5, 15, 0, ("rail", 0, 1))
    out = _run(_stub(), [o], arrive_by=1470, rf="2026-10-06T23:00:00+09:00")
    s = out["modes"]["subway"]
    assert (s["depart_at"], s["arrive_at"]) == ("2026-10-07T00:05:00+09:00", "2026-10-07T00:20:00+09:00")
    assert out["range_to"] == "2026-10-07T00:30:00+09:00" and out["range_min"] == 90 and s["within_range"] is True
    # 앞 일정 시작이 04:00 전(같은 운행일의 연장)이어도 같은 축
    out = _run(_stub(), [o], arrive_by=1470, rf="2026-10-07T00:10:00+09:00")
    assert out["range_min"] == 20 and out["modes"]["subway"]["within_range"] is False


# ── 환승 상한 잠금 ────────────────────────────────────────────────────────
def test_representative_never_exceeds_transfer_limit():
    """상한을 넘는 후보가 섞여 들어와도 대표가 되지 않는다 — 넘는 것뿐이면 칸을 비우고 이유(transfer_limit)."""
    over = _opt(SUB2, 630, 20, 3, ("rail", 0, 1))
    ok = _opt(SUB2, 600, 50, 2, ("rail", 0, 2))
    s = _run(_stub(limit=2), [over, ok])["modes"]["subway"]
    assert s["status"] == "found" and s["transfers"] == 2
    s = _run(_stub(limit=1), [over, ok])["modes"]["subway"]
    assert s == {"status": "none", "code": "transfer_limit", "reason": "성립 후보가 환승 상한 1회(default)를 넘는다"}


# ── 없는 수단 ─────────────────────────────────────────────────────────────
def test_taxi_slot_is_placeholder_only():
    t = _run(_stub(), [])["modes"]["taxi"]
    assert t["status"] == "none" and t["code"] == "no_data" and "택시" in t["reason"]


def test_bus_none_reasons():
    out = _run(_stub(), [_opt(SUB1, 620, 20, 0, ("rail", 0, 1))])
    b = out["modes"]["bus"]
    assert b["status"] == "none" and b["code"] == "no_service" and "한 노선" in b["reason"] and "갈아타는" in b["reason"]
    # 직행 노선은 있지만 이 도착 목표에 성립하지 않을 때 — 판정에서 뺀 이유를 옮긴다
    left = [{"_o": {"_legs": BUS, "_walk_m": 100, "_n": 100}, "label": "버스 2016", "code": "no_last_departure", "reason": "첫차 전"}]
    b = _run(_stub(), [], left=left)["modes"]["bus"]
    assert b["code"] == "no_last_departure" and "버스 1개" in b["reason"] and "첫차 전" in b["reason"]
    assert len(left) == 1                                                   # 봉투 left_out 재료를 건드리지 않는다


def test_subway_none_reason_is_about_subway_only():
    """GPT 93 #2 — 지하철 칸의 이유는 지하철 탐색 결과만으로. leg() 의 대표 이유(버스·혼합까지 묶은 문장)를 옮기지 않는다."""
    why = {"code": "no_service", "reason": "걸어갈 역이 모두 사고로 막혔고(가) 버스 직행·혼합 후보도 없다"}
    pl = _stub()
    pl.disruptions = ({"kind": "line_closed", "line": "02호선"},)
    out = _run(pl, [], [_opt(BUS, 602, 40, 0, ("bus", 0, 0))], why=why, sa=[], visited=())
    s = out["modes"]["subway"]
    assert s == {"status": "none", "code": "no_service", "reason": "걸어갈 지하철역이 모두 사고로 막혔다(가)"}
    assert out["modes"]["bus"]["status"] == "found" and "버스" not in s["reason"]
    # 사고가 없으면 「역이 없다」
    s = _run(_stub(), [], sb=[], visited=())["modes"]["subway"]
    assert s == {"status": "none", "code": "no_data", "reason": "도보 상한 안에 지하철역이 없다(나)"}
    # 두 장소의 가장 가까운 역이 같다 — 지하철 탐색만의 이유라 그대로
    same = {"code": "no_data", "reason": "두 장소의 가장 가까운 역이 같다(서울역) — 도보만 본다"}
    assert _run(_stub(), [], why=same, visited=())["modes"]["subway"] == {"status": "none", **same}
    # 역 짝을 봤지만 성립 후보가 없다 — 본 짝과 개수를 밝힌다(첫 짝 이유를 전체로 일반화하지 않는다)
    r = SimpleNamespace(out={"code": "after_last", "reason": "막차 뒤"}, reason="")
    s = _run(_stub(), [], r=r, visited=("가역→나역", "다역→나역"))["modes"]["subway"]
    assert s["code"] == "after_last" and "역 짝 2개(가역→나역 · 다역→나역)" in s["reason"] and "막차 뒤" in s["reason"]
    assert _run(_stub(), [], visited=())["modes"]["subway"]["code"] == "no_data"


def test_modes_filter_is_not_requested():
    out = _run(_stub(modes={"subway", "walk"}), [_opt(SUB1, 620, 20, 0, ("rail", 0, 1))])
    assert out["modes"]["subway"]["status"] == "found"
    assert out["modes"]["bus"]["code"] == out["modes"]["subway_bus"]["code"] == P.BY_MODE_NOT_REQUESTED


def test_bad_uses_candidates_are_not_representative():
    """팀 표기 검사(route_uses)를 못 넘는 후보는 대표가 되지 않는다(코어 등록이 거절하는 표기 — options[] 와 같은 기준)."""
    bad = _opt([{"mode": "bus", "route": "N 37", "from": "a", "to": "b"}], 630, 20, 0, ("bus", 0, 0))
    good = _opt(BUS, 600, 40, 0, ("bus", 0, 1))
    assert P.O.uses_problems(bad["uses"])
    assert _run(_stub(), [], [bad, good])["modes"]["bus"]["label"].startswith("버스 2016")
    assert _run(_stub(), [], [bad])["modes"]["bus"]["code"] == "uses_format"


def test_mixed_is_generated_without_comparison_base():
    """혼합 칸은 「지하철만·버스만보다 낫고 1.5배 안」 조건 없이 — _mixed 에 비교 대상 없이([]) 부른다. 지하철만보다 느려도 대표."""
    slow_mix = _opt(MIX, 560, 90, 1, ("mix", 0, 0))
    pl = _stub([slow_mix], mleft=[{"_o": {"_legs": []}, "label": "x", "code": "mix_cap", "reason": "상한"}])
    left = []
    out = _run(pl, [_opt(SUB1, 630, 20, 0, ("rail", 0, 1))], left=left)
    assert pl._seen["opts"] == [] and pl._seen["cid"] == "t~m"
    assert out["modes"]["subway_bus"]["status"] == "found" and out["modes"]["subway_bus"]["eta_min"] == 90
    assert left == []                                                       # 이 칸의 뺀 이유는 봉투 left_out 에 섞지 않는다
    assert out["modes"]["subway_bus"]["search_limited"] is True             # 판정 안 한 후보가 남은 채 고른 대표(GPT 93 #1)
    assert "search_limited" not in _run(_stub([slow_mix]), [])["modes"]["subway_bus"]
    assert _run(_stub([]), [])["modes"]["subway_bus"]["code"] == "no_service"


def test_mixed_unverified_candidates_are_unconfirmed_not_absent():
    """GPT 93 #1 — 판정한 혼합 후보는 모두 불성립이고 판정하지 않은 후보(mix_cap·mix_skipped)가 남았으면 「없다」(첫 후보의
    after_last 등)가 아니라 unconfirmed. 판정한 후보의 이유는 보조로 남긴다."""
    cap = {"_o": {"_legs": []}, "label": "그 밖 1개", "code": "mix_cap", "reason": "상한"}
    late = {"_o": {"_legs": []}, "label": "y", "code": "after_last", "reason": "막차 뒤"}
    m = _run(_stub([], mleft=[late, cap]), [])["modes"]["subway_bus"]
    assert m["status"] == "none" and m["code"] == P.BY_MODE_UNCONFIRMED == "unconfirmed"
    assert "확인한 것은 아니다" in m["reason"] and "막차 뒤" in m["reason"]
    skip = dict(cap, code="mix_skipped")
    assert _run(_stub([], mleft=[skip]), [])["modes"]["subway_bus"]["code"] == "unconfirmed"
    # 남은 미판정 후보가 없으면 판정한 후보의 이유 그대로
    assert _run(_stub([], mleft=[late]), [])["modes"]["subway_bus"]["code"] == "after_last"


def test_pick_is_latest_departure_not_earliest_arrival():
    """GPT 93 #3 — 대표는 「가장 늦게 떠나도 되는 후보」다. 더 일찍 도착하거나 예정 소요가 짧은 후보가 따로 있어도 바꾸지 않는다
    (여유·남는 분이 후보마다 다르다) — 이 뜻을 잠근다."""
    a = _opt(SUB2, 620, 30, 1, ("rail", 0, 1))            # 10:20 출발 · 30분 · 10:50 도착
    b = _opt(SUB2, 615, 20, 1, ("rail", 0, 2), _slack=15)  # 10:15 출발 · 20분 · 10:35 도착
    assert P.Planner._by_mode_pick([a, b], 5) is a


def test_range_across_0400_service_day_boundary():
    """GPT 93 #7 — 04:00 운행일 전환을 지난다. 앞 일정 시작 03:50(앞 운행일 27:50) · 다음 일정 시작 04:20(새 운행일 04:20)."""
    o = _opt(SUB1, 245, 10, 0, ("rail", 0, 1), margin=5)               # 04:05 출발
    out = _run(_stub(), [o], arrive_by=260, rf="2026-10-07T03:50:00+09:00", sdate=date(2026, 10, 7))
    assert (out["range_from"], out["range_to"], out["range_min"]) == (
        "2026-10-07T03:50:00+09:00", "2026-10-07T04:20:00+09:00", 30)
    s = out["modes"]["subway"]
    assert s["within_range"] is True and s["depart_at"] == "2026-10-07T04:05:00+09:00"
    # 03:59 → 04:00 — 1분 범위 · 04:00 전(앞 운행일 몫)에 떠나야 하는 후보는 범위 밖 · 시각은 같은 날 벽시계
    early = _opt(SUB1, 235, 4, 0, ("rail", 0, 1), margin=1)            # 03:55 출발
    out = _run(_stub(), [early], arrive_by=240, rf="2026-10-07T03:59:00+09:00", sdate=date(2026, 10, 7))
    assert out["range_min"] == 1 and out["modes"]["subway"]["within_range"] is False
    assert out["modes"]["subway"]["depart_at"] == "2026-10-07T03:55:00+09:00"


# ── 전체층 — 실데이터 ─────────────────────────────────────────────────────
_RT = None
MYEONGDONG = {"key": "md", "name": "명동", "lat": 37.5609, "lon": 126.9862}
GYEONGBOK = {"key": "gb", "name": "경복궁", "lat": 37.5796, "lon": 126.977}
HONGDAE = {"key": "hd", "name": "홍대", "lat": 37.5572, "lon": 126.9245}
JAMSIL = {"key": "js", "name": "잠실", "lat": 37.5133, "lon": 127.1001}
SINYONGSAN = {"key": "sy", "name": "신용산", "lat": 37.5292, "lon": 126.968}
NAMSEONG = {"key": "ns", "name": "남성", "lat": 37.4847, "lon": 126.971}
FULL_LEGS = [(MYEONGDONG, GYEONGBOK, "2026-10-06T10:00:00+09:00"), (HONGDAE, JAMSIL, "2026-10-06T18:30:00+09:00"),
             (SINYONGSAN, NAMSEONG, "2026-10-06T15:00:00+09:00"), (GYEONGBOK, JAMSIL, "2026-10-06T12:00:00+09:00"),
             (JAMSIL, MYEONGDONG, "2026-10-06T21:00:00+09:00")]


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:
            _RT = e
    if isinstance(_RT, Exception):
        pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    return _RT


def _min(iso):
    d = datetime.fromisoformat(iso)
    return d.hour * 60 + d.minute


@pytest.mark.mobility_full
def test_full_by_mode_does_not_change_options_or_planned():
    """켜도 계획 수단·출발·도착·options·뺀 후보가 그대로다(추가만)."""
    for a, b, arr in FULL_LEGS:
        arr = datetime.fromisoformat(arr)
        off_pl = P.Planner(_runtime(), stage="planning")
        off, off_why = off_pl.leg(a, b, arr, {}, True, "x")
        on_pl = P.Planner(_runtime(), stage="planning")
        on, on_why = on_pl.leg(a, b, arr, {}, True, "x", by_mode=True)
        assert off_pl.last_by_mode is None and on_pl.last_by_mode is not None
        assert (on is None) == (off is None) and on_why == off_why, (a["name"], b["name"])
        if on is not None:
            assert on == off, (a["name"], b["name"])


@pytest.mark.mobility_full
def test_full_range_inside_and_outside():
    """명동 → 경복궁 10:00 도착. 앞 일정 08:30 시작이면 범위 안 · 09:50 시작이면 범위 밖 — 밖이어도 시각·경로는 같다."""
    arr = datetime.fromisoformat("2026-10-06T10:00:00+09:00")
    got = {}
    for tag, rf in (("in", "2026-10-06T08:30:00+09:00"), ("out", "2026-10-06T09:50:00+09:00"), ("none", None)):
        pl = P.Planner(_runtime(), stage="planning")
        pl.leg(MYEONGDONG, GYEONGBOK, arr, {}, True, "x", by_mode=True,
               range_from_dt=None if rf is None else datetime.fromisoformat(rf))
        got[tag] = pl.last_by_mode
    assert got["in"]["range_min"] == 90 and got["out"]["range_min"] == 10 and got["none"]["range_min"] is None
    s_in, s_out, s_none = (got[k]["modes"]["subway"] for k in ("in", "out", "none"))
    assert s_in["status"] == "found" and s_in["within_range"] is True
    assert s_out["within_range"] is False and s_none["within_range"] is None
    strip = lambda e: {k: v for k, v in e.items() if k != "within_range"}      # noqa: E731
    assert strip(s_in) == strip(s_out) == strip(s_none)
    # 식 — 출발 + 최악 소요(예정 + 여유) ≤ 다음 일정 시작 · 도착 = 출발 + 예정
    for e in got["in"]["modes"].values():
        if e["status"] != "found":
            continue
        assert _min(e["depart_at"]) + e["worst_min"] <= 600 and _min(e["arrive_at"]) - _min(e["depart_at"]) == e["eta_min"]
    assert got["in"]["modes"]["taxi"] == {"status": "none", "code": "no_data", "reason": got["in"]["modes"]["taxi"]["reason"]}


@pytest.mark.mobility_full
def test_full_representative_transfers_within_limit():
    """대표 후보의 환승 수 ≤ 환승 상한(동행 조건별) — 상한을 넘는 후보가 대표로 나오면 실패한다."""
    for party in ({}, {"fatigue_high": True}):
        for a, b, arr in FULL_LEGS:
            pl = P.Planner(_runtime(), stage="planning")
            lim, _ = pl.v._party_limit(party)
            pl.leg(a, b, datetime.fromisoformat(arr), party, True, "x", by_mode=True)
            for k, e in pl.last_by_mode["modes"].items():
                if e["status"] == "found":
                    assert e["transfers"] <= lim, (party, a["name"], b["name"], k, e)
                    assert e["transfers"] == max(0, len(e["legs"]) - 1), e
                    assert {x["mode"] for x in e["legs"]} == {"subway": {"subway"}, "bus": {"bus"},
                                                               "subway_bus": {"subway", "bus"}}[k], e


@pytest.mark.mobility_full
def test_full_plan_envelope():
    """plan() — 켜면 봉투 by_mode(구간마다 한 줄 · 만든 구간은 routes 키 · 못 만든 구간은 None) · 끄면 칸 자체가 없다."""
    places = [dict(MYEONGDONG), dict(GYEONGBOK), dict(JAMSIL)]
    items = [{"seq": 1, "kind": "activity", "title": "명동", "place": "md",
              "starts_at": "2026-10-06T08:30:00+09:00", "ends_at": "2026-10-06T09:00:00+09:00"},
             {"seq": 2, "kind": "activity", "title": "경복궁", "place": "gb",
              "starts_at": "2026-10-06T10:00:00+09:00", "ends_at": "2026-10-06T11:00:00+09:00"},
             {"seq": 3, "kind": "activity", "title": "잠실", "place": "js",       # 경복궁 끝 11:00 → 11:10 도착은 못 맞춘다
              "starts_at": "2026-10-06T11:10:00+09:00", "ends_at": "2026-10-06T12:00:00+09:00"}]
    off = P.plan(places, items, runtime=_runtime())
    on = P.plan(places, items, runtime=_runtime(), by_mode=True)
    assert "by_mode" not in off
    bm = on.pop("by_mode")
    off.pop("basis"), on.pop("basis")
    assert on == off                                                          # 나머지 봉투는 그대로
    assert [(x["from"], x["to"]) for x in bm] == [("명동", "경복궁"), ("경복궁", "잠실")]
    assert [(x["from_place"], x["to_place"]) for x in bm] == [("md", "gb"), ("gb", "js")]      # 구간 식별(GPT 93 #5)
    assert bm[0]["route"] in on["routes"] and bm[0]["range_min"] == 90
    assert bm[1]["route"] is None and on["skipped"] and on["skipped"][0]["to"] == "잠실"
    # 못 만든 구간도 시각을 낸다. ★범위는 앞 일정 **시작**(10:00) 기준이라, 앞 일정이 끝나기(11:00) 전에 떠나야 하는 후보도
    #   「범위 안」으로 표시된다 — 앞 일정 끝과의 비교는 받는 쪽이 depart_at 으로 한다(본인 10/2 범위 정의 그대로).
    s = bm[1]["modes"]["subway"]
    assert s["status"] == "found" and bm[1]["range_min"] == 70
    assert s["within_range"] is (_min(s["depart_at"]) > 600) and _min(s["depart_at"]) < 660
    assert P.core_routes(on["routes"]) == P.core_routes(off["routes"])


@pytest.mark.mobility_full
def test_full_plan_blank_row_for_unbuildable_leg():
    """GPT 93 #5 — leg() 를 부르기 전에 못 만든 구간(좌표 없음 · 장소 없음)에도 한 줄 — 네 칸 모두 no_data."""
    places = [dict(MYEONGDONG), {"key": "nc", "name": "좌표 없는 곳"}, dict(JAMSIL)]
    items = [{"seq": 1, "kind": "activity", "title": "명동", "place": "md",
              "starts_at": "2026-10-06T08:30:00+09:00", "ends_at": "2026-10-06T09:00:00+09:00"},
             {"seq": 2, "kind": "activity", "title": "어딘가", "place": "nc",
              "starts_at": "2026-10-06T10:00:00+09:00", "ends_at": "2026-10-06T11:00:00+09:00"},
             {"seq": 3, "kind": "activity", "title": "자유 시간",
              "starts_at": "2026-10-06T12:00:00+09:00", "ends_at": "2026-10-06T13:00:00+09:00"},
             {"seq": 4, "kind": "activity", "title": "잠실", "place": "js",
              "starts_at": "2026-10-06T15:00:00+09:00", "ends_at": "2026-10-06T16:00:00+09:00"}]
    on = P.plan(places, items, runtime=_runtime(), by_mode=True)
    bm = on["by_mode"]
    assert len(bm) == len(on["skipped"]) == 3
    assert [(x["from_place"], x["to_place"], x["route"], x["range_min"]) for x in bm] == [
        ("md", "nc", None, 90), ("nc", None, None, 120), (None, "js", None, 180)]
    assert (bm[1]["from"], bm[1]["to"], bm[2]["from"]) == ("좌표 없는 곳", "자유 시간", "자유 시간")
    for x in bm:
        assert list(x["modes"]) == list(P.BY_MODE_KEYS)
        assert all(e["status"] == "none" and e["code"] == "no_data" for e in x["modes"].values()), x


@pytest.mark.mobility_full
def test_full_last_by_mode_is_per_call():
    """GPT 93 #6 — 한 Planner 를 순서대로 다시 쓸 때 앞 호출 값이 남지 않는다(켬 → 끔 → 켬 · 예외 뒤)."""
    pl = P.Planner(_runtime(), stage="planning")
    arr = datetime.fromisoformat("2026-10-06T10:00:00+09:00")
    pl.leg(MYEONGDONG, GYEONGBOK, arr, {}, True, "x", by_mode=True)
    first = pl.last_by_mode
    assert first is not None
    pl.leg(MYEONGDONG, GYEONGBOK, arr, {}, True, "x")
    assert pl.last_by_mode is None
    pl.leg(GYEONGBOK, MYEONGDONG, arr, {}, True, "y", by_mode=True)
    assert pl.last_by_mode is not None and pl.last_by_mode != first
    with pytest.raises(Exception):
        pl.leg({"name": "x"}, GYEONGBOK, arr, {}, True, "z", by_mode=True)      # 좌표 없는 입력 — leg() 가 던진다
    assert pl.last_by_mode is None
