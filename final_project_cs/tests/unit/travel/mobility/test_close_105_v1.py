# -*- coding: utf-8 -*-
"""105번 방(2026-10-05 · 마무리 1) — 틀린 결과가 나가던 두 자리. 게이트 시험은 데이터 없이 돈다.

㉠ 환승 이름 — 환승 거리표(서울교통공사)의 역·노선 이름이 판정기 이름과 다른 6쌍
   · 노선 표기만 다른 것(수서 「국철」 = 수인분당선 · 석계 「경원선」 = 01호선) → 그 노선쌍 값 대신 역 최대값을 썼다
   · 이름이 다른 같은 역(04호선 총신대입구 ↔ 07호선 이수 · 서울역 ↔ GTX-A 「서울」) → 환승 이음이 아예 없었다
   고친 자리는 자료 하나(transfer_name_map_v1.json) — 판정기·후보 생성기에 역 이름을 적지 않는다.
㉡ 9호선 급행 — 원천이 급행도 전 역 출발 행으로 실어, 통과역에서 그 편을 [확정]으로 태웠다
   자료 express_marks_v1.json 이 급행 편의 행을 표시하고(추정), 판정기는 표시된 편을 급행 정차역(공표)끼리만 쓴다.

가짜 노선(㉠)  L1: A — B — X1 — C      L2: D — X2 — E      (X1 과 X2 가 이름이 다른 같은 환승역 · 역간 2분)
가짜 노선(㉡)  L:  A — B — C — D — E   (역간 2분 · 급행 정차역 A·C·E · 통과역 B·D)
   · 완행 — A 에서 10분마다(06:00~23:00) · E 행
   · 급행 — A 에서 매시 03분 · E 행(완행과 같은 행선지) · **모든 역에 출발 행이 있다**(통과 시각) · 역간 1분
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))      # final_project_cs

from app.modules.travel_ops.mobility.engine import options as O                   # noqa: E402
from app.modules.travel_ops.mobility.engine import plan as P                      # noqa: E402
from app.modules.travel_ops.mobility.engine.candidates import CandidateGraph      # noqa: E402
from app.modules.travel_ops.mobility.engine.errors import CaseInputError          # noqa: E402
from app.modules.travel_ops.mobility.engine.line_order import LineOrder           # noqa: E402
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR                # noqa: E402
from app.modules.travel_ops.mobility.engine.transfer_walk import TransferWalk     # noqa: E402
from app.modules.travel_ops.mobility.engine.verify_time import (                  # noqa: E402
    Dep, ExpressMarks, Timetable, Verifier)

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))


def _rules():
    """규칙 사본 — 정책 수치(value_from)는 시험 값으로 채운다(guardrails 없이 돈다)."""
    r = json.loads(json.dumps(RULES))
    for k, v in (("default", 3), ("infant", 1), ("elderly", 2), ("fatigue_high", 2)):
        r["limits"]["transfers"][k] = dict(r["limits"]["transfers"][k], value=v)
    return r


# ── ㉠ 환승 이름 ─────────────────────────────────────────────────────────────
TW_DOC = {"built_at": "t", "pairs": {
    "X1|L1|L2": {"station_nm": "X1", "from_line": "L1", "to_line": "L2", "distance_m": 104.0},
    "X2|L2|L1": {"station_nm": "X2", "from_line": "L2", "to_line": "L1", "distance_m": 104.0},
    "S|L1|국철": {"station_nm": "S", "from_line": "L1", "to_line": "국철", "distance_m": 62.4},
    "S|L1|L3": {"station_nm": "S", "from_line": "L1", "to_line": "L3", "distance_m": 312.0},
    "Q|L1|L2": {"station_nm": "Q", "from_line": "L1", "to_line": "L2", "distance_m": 208.0}},
    "stations": {"X1": {"station_nm": "X1", "distance_m": 104.0, "worst_pair": "L1↔L2"},
                 "S": {"station_nm": "S", "distance_m": 312.0, "worst_pair": "L1↔L3"}}}
NAME_MAP = {"line_alias": [{"station_nm": "S", "table_line": "국철", "line": "L4"}],
            "same_station": [{"name": "X", "members": [{"line": "L1", "station_nm": "X1"}, {"line": "L2", "station_nm": "X2"},
                                                       {"line": "L5", "station_nm": "X1"}]}]}


def _tw(with_map=True):
    return TransferWalk(json.loads(json.dumps(TW_DOC)), 1.04, NAME_MAP if with_map else None)


def test_line_alias_reads_the_pair_instead_of_the_station_max():
    old, new = _tw(False).lookup("S", "L1", "L4"), _tw().lookup("S", "L1", "L4")
    assert (old.basis, old.distance_m) == ("station_max", 312.0), "앞 판 — 노선 표기가 달라 그 역 최대값"
    assert (new.basis, new.distance_m) == ("pair", 62.4) and _tw().lookup("S", "L4", "L1").distance_m == 62.4
    assert _tw().lookup("S", "L1", "L3").distance_m == 312.0, "다른 노선쌍은 그대로"


def test_same_station_pair_is_found_under_either_name():
    tw = _tw()
    for nm, a, b in (("X1", "L1", "L2"), ("X2", "L1", "L2"), ("X2", "L2", "L1"), ("X1", "L2", "L1")):
        w = tw.lookup(nm, a, b)
        assert (w.basis, w.distance_m) == ("pair", 104.0), (nm, a, b)
    assert _tw(False).lookup("X2", "L5", "L2").basis == "none", "앞 판 — 다른 이름의 역 값은 못 찾는다"
    w = tw.lookup("X2", "L5", "L2")                      # 거리표에 줄이 없는 노선쌍 → 같은 역의 최대값(다른 이름으로 물어도)
    assert (w.basis, w.distance_m) == ("station_max", 104.0)


def test_partners_and_same_station_come_only_from_the_map():
    tw = _tw()
    assert sorted(tw.partners("L1", "X1")) == [("L2", "X2")] and tw.partners("L2", "X2") == [("L1", "X1"), ("L5", "X1")]
    assert tw.partners("L1", "Q") == [] and _tw(False).partners("L1", "X1") == []
    assert tw.same_station("L1", "X1", "L2", "X2") and tw.same_station("L1", "Q", "L2", "Q")
    assert not tw.same_station("L1", "X1", "L2", "Q") and not _tw(False).same_station("L1", "X1", "L2", "X2")
    assert (("L1", "X1"), ("L2", "X2")) in tw.links() and (("L1", "S"), ("L4", "S")) in tw.links()
    assert len(tw.pairs) == 5, "거리표 원본(pairs)은 건드리지 않는다"


def test_pairs_and_map_are_not_shared_between_instances_without_a_map():
    """맞춤표가 없으면 앞 판과 같다 — 거리표에 있는 이름 그대로만 찾는다."""
    tw = _tw(False)
    assert tw.lookup("X1", "L1", "L2").distance_m == 104.0 and tw.lookup("Q", "L2", "L1").distance_m == 208.0
    assert tw.lookup("S", "L1", "국철").distance_m == 62.4


def _lo2():
    def line(names):
        return {"stations": [{"station_nm": n, "fr_order": i} for i, n in enumerate(names)],
                "edges": [{"a": a, "b": b, "grade": "확정", "travel_min": 2.0} for a, b in zip(names, names[1:])],
                "dir_label": {"reliable": True}, "is_loop": False}
    return LineOrder({"built_at": "t", "dest_alias": {}, "lines": {
        "L1": line(["A", "B", "X1", "C"]), "L2": line(["D", "X2", "E"])}})


def _legs(c):
    return [(x["line"], x["from"], x["to"]) for x in c.legs]


def test_candidate_graph_links_differently_named_same_station():
    R = _rules()
    old = CandidateGraph(_lo2(), _tw(False), R)
    assert old.search("A", "E", "최단") is None, "앞 판 — 역명이 같아야만 환승을 이어 길이 없다"
    cg = CandidateGraph(_lo2(), _tw(), R)
    c = cg.search("A", "E", "최단")
    assert _legs(c) == [("L1", "A", "X1"), ("L2", "X2", "E")] and c.transfers == 1 and c.walk_min == 1.7
    assert _legs(cg.search("D", "C", "최단")) == [("L2", "D", "X2"), ("L1", "X1", "C")]


def test_candidate_graph_endpoints_use_the_other_name_without_a_fake_transfer():
    cg = CandidateGraph(_lo2(), _tw(), _rules())
    c = cg.search("X1", "E", "최단")                     # 출발이 X1 — L2 의 X2 에서 바로 탄다(갈아타지 않는다)
    assert _legs(c) == [("L2", "X2", "E")] and c.transfers == 0 and c.walk_min == 0
    c = cg.search("A", "X2", "최단")                     # 도착이 X2 — L1 의 X1 에 닿으면 도착이다(길이 0 인 환승 구간을 만들지 않는다)
    assert _legs(c) == [("L1", "A", "X1")] and c.transfers == 0
    assert cg.search("X1", "X2", "최단") is None and cg.search("X2", "X1", "최단") is None, "같은 역끼리는 후보가 없다"
    c = cg.search("X1", "E", "최단", origin_lines=["L1"])      # 노선을 줬으면 그 노선의 역에서만 — 여기서는 갈아탄다
    assert c is None, "출발역에서는 갈아타지 않는다(그 노선에서 출발한 것과 같다) — L1 만으로는 E 에 못 간다"


def test_candidate_graph_respects_excluded_names_and_avoid_lines():
    R = _rules()
    R["station_names"]["환승_제외_역명"] = dict(R["station_names"]["환승_제외_역명"], value=["X2"])
    assert CandidateGraph(_lo2(), _tw(), R).search("A", "E", "최단") is None, "환승 제외 역명은 맞춤표로도 잇지 않는다"
    assert CandidateGraph(_lo2(), _tw(), _rules()).search("A", "E", "최단", avoid_lines=["L2"]) is None


def test_endpoint_other_name_survives_when_the_original_line_is_avoided():
    """GPT 105 #4 — 원래 이름의 노선이 전부 avoid 여도 같은 역의 다른 이름 노드에서 출발·도착한다."""
    cg = CandidateGraph(_lo2(), _tw(), _rules())
    assert _legs(cg.search("X1", "E", "최단", avoid_lines=["L1"])) == [("L2", "X2", "E")]
    assert _legs(cg.search("A", "X2", "최단", avoid_lines=["L2"])) == [("L1", "A", "X1")]
    assert cg.search("A", "E", "최단", avoid_lines=["L1"]) is None


def test_shortest_through_an_edge_without_travel_time_also_gives_the_solid_shortest():
    """소요 없는 간선(구조만 있는 이음)을 탄 최단이면 그 간선을 안 타는 최단도 같이 낸다 — 다른 기준의 시간 상한도 그쪽 기준."""
    def line(names, none=()):
        return {"stations": [{"station_nm": n, "fr_order": i} for i, n in enumerate(names)],
                "edges": [{"a": a, "b": b, "grade": "확정", "travel_min": None if (a, b) in none else 10.0} for a, b in zip(names, names[1:])],
                "dir_label": {"reliable": True}, "is_loop": False}
    lo = LineOrder({"built_at": "t", "dest_alias": {}, "lines": {"G": line(["A", "Z"], {("A", "Z")}), "M": line(["A", "B", "Z"])}})
    cg = CandidateGraph(lo, None, _rules())
    assert _legs(cg.search("A", "Z", "최단")) == [("G", "A", "Z")], "종전 탐색 — 소요 없는 간선을 대체 분으로 쳐 최단이 된다"
    assert _legs(cg.search("A", "Z", "최단", no_fallback=True)) == [("M", "A", "Z")] and _legs(cg.search_solid("A", "Z", "최단")) == [("M", "A", "Z")]
    got = cg.candidates("A", "Z", ["최단", "최소환승", "최소도보"])
    assert [_legs(c) for c in got][:2] == [[("M", "A", "Z")], [("G", "A", "Z")]] and "최단" in got[0].criteria and got[0].grade == "추정"
    lo1 = LineOrder({"built_at": "t", "dest_alias": {}, "lines": {"G": line(["A", "Z"], {("A", "Z")})}})
    assert _legs(CandidateGraph(lo1, None, _rules()).search_solid("A", "Z", "최단")) == [("G", "A", "Z")], "그 간선뿐이면 종전대로"


def _tt2():
    tt = Timetable()
    for m in range(6 * 60, 23 * 60 + 1, 10):
        for k, st in enumerate(["A", "B", "X1"]):
            tt.by_key[("L1", st, "weekday")].append(Dep(m + 2 * k, "D", "C"))
        for k, st in enumerate(["D", "X2"]):
            tt.by_key[("L2", st, "weekday")].append(Dep(m + 2 * k, "D", "E"))
    for ln, names in (("L1", ["A", "B", "X1", "C"]), ("L2", ["D", "X2", "E"])):
        for st in names:
            tt.stations.add((ln, st))
    tt.rows = sum(len(v) for v in tt.by_key.values())
    return tt


def _case(*legs):
    return {"id": "t", "date": "2026-09-23", "depart_at": "08:00", "legs": [dict(zip(("line", "from", "to"), x)) for x in legs]}


def test_verifier_accepts_a_transfer_between_differently_named_same_station():
    v = Verifier(_tt2(), _lo2(), _rules(), set(), _tw())
    r = v.verify_case(_case(("L1", "A", "X1"), ("L2", "X2", "E")))
    assert r.verdict == "feasible"
    tr = [x for x in r.legs if x.label.startswith("환승")]
    assert len(tr) == 1 and tr[0].label == "환승 X1↔X2" and "104m" in tr[0].reason


def test_verifier_still_rejects_legs_that_do_not_connect():
    v_old = Verifier(_tt2(), _lo2(), _rules(), set(), _tw(False))
    with pytest.raises(CaseInputError):
        v_old.verify_case(_case(("L1", "A", "X1"), ("L2", "X2", "E")))          # 맞춤표가 없으면 앞 판대로 입력 오류
    v = Verifier(_tt2(), _lo2(), _rules(), set(), _tw())
    with pytest.raises(CaseInputError):
        v.verify_case(_case(("L1", "A", "B"), ("L2", "X2", "E")))               # 묶음 밖은 여전히 이어지지 않는다


def test_transfer_walk_m_reads_the_pair_of_a_cross_name_transfer():
    v = Verifier(_tt2(), _lo2(), _rules(), set(), _tw())
    legs = [{"line": "L1", "from": "A", "to": "X1"}, {"line": "L2", "from": "X2", "to": "E"}]
    assert O.transfer_walk_m(v, legs) == 104.0


def test_plan_version_is_2_8():
    assert P.PLAN_VERSION == "plan-v2.8"


# ── ㉡ 급행 편 표시 ───────────────────────────────────────────────────────────
NAMES = ["A", "B", "C", "D", "E"]
STOPS = ["A", "C", "E"]


def _hm(m):
    return f"{m // 60:02d}:{m % 60:02d}:30"          # 초가 붙은 표기 — 판정기와 같은 규칙(초 버림)으로 읽어야 한다


def _lo5():
    return LineOrder({"built_at": "t", "dest_alias": {}, "lines": {"L": {
        "stations": [{"station_nm": n, "fr_order": i} for i, n in enumerate(NAMES)],
        "edges": [{"a": a, "b": b, "grade": "확정", "travel_min": 2.0} for a, b in zip(NAMES, NAMES[1:])],
        "dir_label": {"reliable": True}, "is_loop": False}}})


def _tt5(marks=True, local_until=23 * 60, same_minute=False):
    tt = Timetable()
    mk = collections.defaultdict(list)
    for m in range(6 * 60, local_until + 1, 10):                 # 완행 — A·B·C·D 출발(E 는 종착)
        for k, st in enumerate(NAMES[:-1]):
            tt.by_key[("L", st, "weekday")].append(Dep(m + 2 * k, "D", "E"))
    for h in range(7, 24):                                       # 급행 — 모든 역에 출발 행(통과 시각) · 역간 1분
        for k, st in enumerate(NAMES[:-1]):
            m = h * 60 + (0 if same_minute else 3) + k * (2 if same_minute else 1)
            tt.by_key[("L", st, "weekday")].append(Dep(m, "D", "E"))
            mk[st].append(_hm(m))
    for st in NAMES:
        tt.stations.add(("L", st))
    for v in tt.by_key.values():
        v.sort(key=lambda x: x.min)
    tt.rows = sum(len(v) for v in tt.by_key.values())
    if marks:
        tt.express = ExpressMarks({"built_at": "t", "lines": {"L": {
            "stops": STOPS, "stops_source": {"url": "https://example.invalid/stops", "checked_at": None},
            "marks": {"weekday": {"D": {st: {"E": v} for st, v in mk.items()}}}}}})
    return tt


def _leg(v, a, b, at):
    return v.verify_leg(0, {"line": "L", "from": a, "to": b}, at, "weekday", False)


def _v5(**kw):
    return Verifier(_tt5(**kw), _lo5(), _rules(), set())


def test_without_marks_the_express_is_boarded_at_a_pass_station():
    r = _leg(_v5(marks=False), "B", "E", 8 * 60 + 3)             # 앞 판 — B(통과역) 08:04 급행 행을 그대로 탄다
    assert r.verdict == "feasible" and r.depart_min == 8 * 60 + 4 and r.grade == "확정"


def test_express_is_not_boarded_at_a_pass_station():
    r = _leg(_v5(), "B", "E", 8 * 60 + 3)                        # 08:04 급행(B 통과) → 08:12 완행
    assert r.verdict == "feasible" and r.depart_min == 8 * 60 + 12 and r.grade == "확정"
    assert r.dropped["급행_통과"] == 17
    ev = [e for e in r.evidence if e["source_id"] == "express_marks_v1"]
    assert len(ev) == 1 and ev[0]["grade"] == "추정" and "추정" in ev[0]["claim"] and "공표 대조 안 함" in ev[0]["claim"]


def test_express_is_not_used_to_alight_at_a_pass_station():
    r = _leg(_v5(), "A", "D", 8 * 60 + 1)                        # A 08:03 급행으로 D(통과역)에 내리지 않는다 → 08:10 완행
    assert r.verdict == "feasible" and r.depart_min == 8 * 60 + 10 and r.dropped["급행_통과"] == 17


def test_express_is_kept_between_express_stops():
    r = _leg(_v5(), "A", "C", 8 * 60 + 1)                        # A·C 둘 다 정차역 → 08:03 급행 그대로
    assert r.verdict == "feasible" and r.depart_min == 8 * 60 + 3 and not r.dropped.get("급행_통과")
    assert not [e for e in r.evidence if e["source_id"] == "express_marks_v1"]


def test_local_is_unchanged_by_marks():
    for a, b, at in (("A", "C", 8 * 60 + 5), ("B", "D", 8 * 60 + 5), ("C", "E", 9 * 60)):
        x, y = _leg(_v5(marks=False), a, b, at), _leg(_v5(), a, b, at)
        if (x.depart_min - 3) % 60 > 3:                           # 앞 판이 완행을 고른 자리는 값이 같다
            assert (x.depart_min, x.arrive_min, x.grade) == (y.depart_min, y.arrive_min, y.grade)


def test_only_express_left_at_a_pass_station_is_unknown_not_infeasible():
    v = _v5(local_until=22 * 60)                                 # 완행 막차 22:00 뒤에도 급행은 23:03 까지
    r = _leg(v, "B", "E", 22 * 60 + 30)
    assert r.verdict == "unknown" and r.code == "no_data" and "급행으로 가린 편" in r.reason and "추정" in r.reason
    assert [e["source_id"] for e in r.evidence] == ["express_marks_v1"]
    r = _leg(v, "A", "C", 22 * 60 + 30)                          # 정차역끼리는 급행으로 간다
    assert r.verdict == "feasible" and r.depart_min == 23 * 60 + 3


def test_same_minute_local_and_express_drop_only_the_marked_count():
    v = _v5(same_minute=True)                                    # 08:02 B — 급행 행과 완행 행이 같은 분·같은 행선지
    cands, drop, _ = v.candidates("L", "B", "E", "weekday")
    at = [d.min for d, _v, _f in cands if d.min == 8 * 60 + 2]
    assert len(at) == 1 and drop["급행_통과"] == 17, "같은 분의 두 행 중 표시된 수(1)만 뺀다"


def test_filled_rows_are_never_counted_as_marked():
    """GPT 105 #3 — 같은 분·방향·행선지에 행선지를 채운 보충 행(완행)과 원천 급행 행이 겹치면, 입력 순서와 무관하게 원천 행을 뺀다."""
    for first in ("filled", "source"):
        tt = _tt5(local_until=5 * 60)                              # 완행 없음 — 급행 행만
        row = [Dep(8 * 60 + 4, "D", "E", "chain_v1")]
        cur = tt.by_key[("L", "B", "weekday")]
        tt.by_key[("L", "B", "weekday")] = sorted(row + cur if first == "filled" else cur + row, key=lambda d: d.min)
        cands, drop, _ = Verifier(tt, _lo5(), _rules(), set()).candidates("L", "B", "E", "weekday")
        left = [d for d, _v, _f in cands if d.min == 8 * 60 + 4]
        assert len(left) == 1 and left[0].inferred == "chain_v1", first


def _marks_file(tmp_path, fp):
    row = {"line": "L", "station_nm": "B", "day_type": "weekday", "dep_time": "08:04:00", "dir": "D", "dest_nm": "E", "fetched_at": "2026-10-01"}
    p = tmp_path / "timetable_v1.jsonl"
    p.write_text("\n".join(json.dumps(dict(row, station_nm=st, dep_time=t)) for st, t in (("B", "08:04:00"), ("B", "08:12:00"), ("D", "08:16:00")))
                 + "\n", encoding="utf-8")
    (tmp_path / "express_marks_v1.json").write_text(json.dumps({"timetable": {"rows_fingerprint": fp}, "lines": {"L": {
        "stops": STOPS, "marks": {"weekday": {"D": {"B": {"E": ["08:04:00"]}}}}}}}), encoding="utf-8")
    return p


def test_marks_from_another_timetable_are_not_used(tmp_path):
    """GPT 105 #1 — 표시 파일의 지문이 적재한 시간표와 다르면 표시를 쓰지 않는다. 통과역이 낀 구간은 편을 못 가려 모른다."""
    keys = sorted(f"weekday|D|{st}|{t}|E" for st, t in (("B", "08:04:00"), ("B", "08:12:00"), ("D", "08:16:00")))
    good = hashlib.sha256("\n".join(keys).encode()).hexdigest()
    tt = Timetable.load(_marks_file(tmp_path, good))
    assert tt.express.stale is False
    assert Timetable.load(_marks_file(tmp_path, good), wanted={("L", "B")}).express.stale is False, "부분 적재여도 지문은 전부로 낸다"
    tt = Timetable.load(_marks_file(tmp_path, "0" * 64))
    assert tt.express.stale is True
    for st in NAMES:
        tt.stations.add(("L", st))
    v = Verifier(tt, _lo5(), _rules(), set())
    cands, drop, _ = v.candidates("L", "B", "E", "weekday")
    assert cands == [] and drop["급행_표시_판불일치"] == 2 and not drop.get("급행_통과")
    r = _leg(v, "B", "E", 8 * 60)
    assert r.verdict == "unknown" and r.code == "no_data" and "이 시간표의 것이 아니라" in r.reason


def test_marks_use_the_same_minute_rule_as_the_timetable():
    xm = _tt5().express
    assert xm.has("L") and not xm.has("L2") and xm.both_stop("L", "A", "E") and not xm.both_stop("L", "A", "B")
    assert xm.count("L", "weekday", "B", Dep(8 * 60 + 4, "D", "E")) == 1
    assert xm.count("L", "weekday", "B", Dep(8 * 60 + 4, "U", "E")) == 0 and xm.count("L", "holiday", "B", Dep(8 * 60 + 4, "D", "E")) == 0


def test_timetable_loads_marks_only_from_its_own_folder(tmp_path):
    p = tmp_path / "timetable_v1.jsonl"
    p.write_text(json.dumps({"line": "L", "station_nm": "A", "day_type": "weekday", "dep_time": "08:03:00", "dir": "D",
                             "dest_nm": "E", "fetched_at": "2026-10-01"}) + "\n", encoding="utf-8")
    assert Timetable.load(p).express is None, "표시 파일이 없으면 앞 판과 같다"
    (tmp_path / "express_marks_v1.json").write_text(json.dumps({"lines": {"L": {"stops": ["A"], "marks": {
        "weekday": {"D": {"A": {"E": ["08:03:00"]}}}}}}}), encoding="utf-8")
    tt = Timetable.load(p)
    assert tt.express.count("L", "weekday", "A", tt.by_key[("L", "A", "weekday")][0]) == 1


# ── 실데이터(전체층) ─────────────────────────────────────────────────────────
_RT = None


def _real_v():
    global _RT
    if _RT is None:
        try:
            from app.modules.travel_ops.mobility.engine.runtime import build_verifier
            _RT = build_verifier(quiet=True)
        except Exception as e:      # noqa: BLE001 — 자료가 없는 기기
            _RT = e
    if isinstance(_RT, Exception):
        pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    return _RT._v


@pytest.mark.mobility_full
def test_real_name_map_members_exist_in_the_line_order():
    v = _real_v()
    if not getattr(v.tw, "name_map", None):
        pytest.skip("transfer_name_map_v1.json 없음 — 앞 판과 같다")
    for g in v.tw.name_map["same_station"]:
        for m in g["members"]:
            assert m["station_nm"] in {s["station_nm"] for s in v.lo.doc["lines"][m["line"]]["stations"]}, m
    for a in v.tw.name_map["line_alias"]:
        assert a["line"] in v.lo.doc["lines"] and a["table_line"] not in v.lo.doc["lines"], a
    lines = set(v.lo.doc["lines"])
    names = {ln: {s["station_nm"] for s in L["stations"]} for ln, L in v.lo.doc["lines"].items()}
    bad = [(x, y) for x, y in v.tw.links() if not (x[0] in lines and y[0] in lines and x[1] in names[x[0]] and y[1] in names[y[0]])]
    assert bad == [], "거리표 213줄이 전부 판정기 이름으로 읽힌다(105 전: 12줄이 안 맞았다)"


@pytest.mark.mobility_full
def test_real_transfer_pairs_that_were_misnamed():
    v = _real_v()
    if not getattr(v.tw, "name_map", None):
        pytest.skip("transfer_name_map_v1.json 없음")
    for st, a, b, m in (("수서", "03호선", "수인분당선", 92.0), ("석계", "06호선", "01호선", 125.0), ("이수", "04호선", "07호선", 171.0),
                        ("총신대입구", "07호선", "04호선", 171.0), ("서울", "01호선", "GTX-A", 172.0), ("서울역", "GTX-A", "04호선", 264.0)):
        w = v.tw.lookup(st, a, b)
        assert (w.basis, w.distance_m) == ("pair", m), (st, a, b)
    assert v.tw.lookup("수서", "03호선", "GTX-A").distance_m == 276.0
    cg = v.candidate_graph(True)
    assert _legs(cg.search("신용산", "남성", "최단")) == [("04호선", "신용산", "총신대입구"), ("07호선", "이수", "남성")]
    assert _legs(cg.search("시청", "킨텍스", "최단")) == [("01호선", "시청", "서울역"), ("GTX-A", "서울", "킨텍스")]
    c = cg.search("원덕", "영등포구청", "최단", origin_lines=["경의선"], dest_lines=["02호선", "05호선"])
    assert c is not None and all(x["from"] != "양평" and x["to"] != "양평" for x in c.legs), "양평(동명이역)은 여전히 잇지 않는다"


@pytest.mark.mobility_full
def test_real_express_marks_match_this_timetable():
    """표시 파일은 그 시간표에서 만든 것이어야 한다 — 시간표를 바꾸면(재수집) `build_express_marks_v1.py` 를 다시 돌린다."""
    from app.modules.travel_ops.mobility.engine import paths
    from app.modules.travel_ops.mobility.engine.verify_time import EXPRESS_MARKS_FILE
    v = _real_v()
    if v.tt.express is None:
        pytest.skip("express_marks_v1.json 없음 — 9호선 급행을 못 가린다(앞 판과 같다)")
    doc = json.loads((paths.PROCESSED / "mobility" / EXPRESS_MARKS_FILE).read_text(encoding="utf-8"))
    import gzip
    tt = paths.timetable_file(paths.PROCESSED / "mobility")
    rows = []
    with (gzip.open if tt.suffix == ".gz" else open)(tt, "rt", encoding="utf-8") as f:
        for raw in f:
            if "09호선" in raw:
                r = json.loads(raw)
                if r.get("line") == "09호선" and r.get("dep_time"):
                    rows.append(f"{r['day_type']}|{r['dir']}|{r['station_nm']}|{r.get('dep_time')}|{r.get('dest_nm')}")
    assert hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest() == doc["timetable"]["rows_fingerprint"]
    L = doc["lines"]["09호선"]
    assert len(L["stops"]) == 16 and set(L["stops"]) <= {s["station_nm"] for s in v.lo.doc["lines"]["09호선"]["stations"]}
    n = {(dt, dr): sum(len(t) for by in sts.values() for t in by.values())
         for dt, by_dir in L["marks"].items() for dr, sts in by_dir.items()}
    assert set(n) == {("weekday", "D"), ("weekday", "U"), ("holiday", "D"), ("holiday", "U")} and min(n.values()) > 3000, n


@pytest.mark.mobility_full
def test_real_line9_express_is_not_used_at_pass_stations():
    v = _real_v()
    if v.tt.express is None:
        pytest.skip("express_marks_v1.json 없음")
    leg = lambda a, b, at, dt="weekday": v.verify_leg(0, {"line": "09호선", "from": a, "to": b}, at, dt, False)      # noqa: E731
    r = leg("구반포", "당산", 10 * 60 + 10)
    assert (r.verdict, r.depart_min) == ("feasible", 10 * 60 + 16) and "개화행" in r.reason and r.dropped["급행_통과"] > 100
    r = leg("당산", "구반포", 10 * 60 + 10)
    assert (r.verdict, r.depart_min) == ("feasible", 10 * 60 + 19)
    r = leg("신논현", "당산", 10 * 60 + 13)
    assert (r.verdict, r.depart_min) == ("feasible", 10 * 60 + 13) and "김포공항행" in r.reason and not r.dropped.get("급행_통과")
    r = leg("흑석", "여의도", 10 * 60 + 10, "holiday")
    assert r.verdict == "feasible" and "개화행" in r.reason and r.dropped["급행_통과"] == 92
