# final_project_cs/tests/unit/travel/mobility/test_verify55_v1.py — 55번 방 · 판정기 소수정 회귀
# 실행: 저장소 루트에서
#   $env:PYTHONPATH = "final_project_cs"
#   python final_project_cs/tests/unit/travel/mobility/test_verify55_v1.py      # 단위 + (시간표가 있으면) 데이터 축
#   python -m pytest tests/unit/travel/mobility/test_verify55_v1.py
# 실패하면 종료코드 1.
#
# 축
#   ① p90   — verify_multi 후보 밖 판의 p90_eta_min 이 eta 와 같이 접근·이탈 도보만큼 밀린다(48 발견 · 서울역→이태원 421
#             14:00 eta 29 · p90 23 → 32). 뜻(41 D5 · 버스 승차 p90 가산)은 그대로.
#   ② 환승  — CandidateGraph 가 규칙 station_names.환승_제외_역명(양평 · 신촌)에서 갈아타지 않는다(54 발견 ·
#             원덕→영등포구청 최단·최소환승이 경의선 양평 → 5호선 양평 53.6 km 가짜 환승이었다). 이촌은 실제 환승역이라 유지.
#   ④ 동명이역 — 역명만 받은 입력이 「양평」이면 양평군(경의선)·영등포구(5호선) 어느 쪽도 조용히 집지 않는다.
#             노선이나 장소 좌표를 주면 그 물리적 역으로 고른다 · 출구표도 물리적 역 단위.
#   G1 (GPT #1) 후보 생성기를 거치지 않는 legs 입력 — 경의선 원덕→양평 + 5호선 양평→영등포구청 이 성립하지 않는다(종전 15:47 도착).
#   G2 (GPT #2) 동명이역 두 역 사이의 이동(경의선 양평 → 5호선 양평)은 「같은 역」으로 제외하지 않는다.
#   U 는 데이터 없이 · D 는 시간표·좌표표가 있을 때(없으면 SKIP).
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.modules.travel_ops.mobility.engine.candidates import CandidateGraph  # noqa: E402
from app.modules.travel_ops.mobility.engine.exits import StationExits  # noqa: E402
from app.modules.travel_ops.mobility.engine.geo import StationCoords, same_station  # noqa: E402
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR  # noqa: E402
from app.modules.travel_ops.mobility.engine.verify_time import Verifier  # noqa: E402

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
YP_GUN = (37.4926, 127.4919)          # 경의중앙선 양평역 근처(양평군) — 5호선 양평(영등포구)에서 53 km
YP_YDP = (37.5257, 126.8860)          # 5호선 양평역 근처(영등포구)


# ── U 단위 ────────────────────────────────────────────────────────────────
def test_u_shift_out_p90():
    o = {"eta_min": 23, "eta_worst_min": 26, "p90_eta_min": 23, "arrive_min": 623, "depart_min": 600}
    s = Verifier._shift_out(o, 598, 2, 7)
    assert s["eta_min"] == 32 and s["p90_eta_min"] == 32 and s["eta_worst_min"] == 35
    assert s["p90_eta_min"] >= s["eta_min"], "① p90 은 eta 와 같은 축(도보 포함)이어야 한다"
    assert o["p90_eta_min"] == 23, "원본을 고치지 않는다(복사본)"
    n = Verifier._shift_out({"eta_min": 10, "p90_eta_min": None}, 600, 3, 3)
    assert n["p90_eta_min"] is None and n["eta_min"] == 16, "p90 이 없으면 그대로 None"


def _sc_doc():
    st = {}

    def add(line, nm, lat, lng):
        st[f"{line}|{nm}"] = {"station_key": f"{line}|{nm}", "line": line, "station_nm": nm, "lat": lat, "lng": lng}
    add("05호선", "양평", 37.5257, 126.8860)
    add("경의선", "양평", 37.4927, 127.4919)
    add("02호선", "잠실", 37.5133, 127.1001)
    add("08호선", "잠실", 37.5145, 127.1040)            # 약 370 m — 한 물리적 역
    add("02호선", "신촌", 37.5552, 126.9369)
    add("경의선", "신촌", 37.5597, 126.9423)            # 약 690 m — 다른 역
    add("GTX-A", "성수", None, None)                    # 좌표 없는 노선 하나는 동명이역 근거가 아니다
    add("02호선", "성수", 37.5446, 127.0558)
    return {"stations": st, "built_at": "t"}


def test_u_geo_ambiguous():
    sc = StationCoords(_sc_doc(), ambig_m=RULES["station_names"]["동명이역_좌표차_m"]["value"])
    assert set(sc.ambiguous) == {"양평", "신촌"}
    assert sc.by_name.get("양평") is None and sc.get(None, "양평") is None, "역명만으로는 동명이역을 안 집는다"
    assert sc.resolve("양평") is None
    assert sc.resolve("양평", ["경의선"])["lng"] > 127.4 and sc.resolve("양평", ["05호선"])["lng"] < 127.0
    assert sc.get("경의선", "양평")["lng"] > 127.4, "노선 키는 그대로"
    assert sc.by_name["잠실"]["line"] == "02호선", "동명이역이 아니면 종전처럼 역명당 첫 키"
    assert "성수" not in sc.ambiguous and sc.by_name["성수"]["line"] == "GTX-A", "좌표 없는 노선은 묶음을 가르지 않는다(종전 대표 유지)"
    near = sc.stations_near(37.5575, 126.9395, 1000)
    assert sorted(v["line"] for _d, v in near if v["station_nm"] == "신촌") == ["02호선", "경의선"], \
        "근처 역은 물리적 역 단위 — 동명이역 둘 다 나온다"


def test_u_same_station():
    assert same_station("잠실", None, "잠실", None)
    assert same_station("양평", ["경의선"], "양평", ["경의선"])
    assert not same_station("양평", ["경의선"], "양평", ["05호선"]), "G2 노선군이 안 겹치면 다른 역"
    assert not same_station("사당", None, "이촌", None)


def test_u_exits_split():
    sc = StationCoords(_sc_doc(), ambig_m=500)
    ex = StationExits({"exits": {"양평": [{"lat": 37.5258, "lng": 126.8861, "ref": "1"},
                                          {"lat": 37.4928, "lng": 127.4912, "ref": "1"},
                                          {"lat": 37.4930, "lng": 127.4925, "ref": "2"}],
                                 "잠실": [{"lat": 37.5134, "lng": 127.1002, "ref": "1"}]}}).bind(sc)
    assert ex.exits_of("양평") == [], "노선 없이는 동명이역 출구를 안 준다"
    assert [e["ref"] for e in ex.exits_of("양평", "경의선")] == ["1", "2"]
    assert len(ex.exits_of("양평", "05호선")) == 1
    assert ex.nearest("양평", 37.5257, 126.8860, "경의선")[0] > 50_000, "노선으로 고른 역의 출구만 본다"
    assert ex.nearest("양평", 37.5257, 126.8860) is None
    assert len(ex.exits_of("잠실")) == 1 and ex.nearest("잠실", 37.5133, 127.1001) is not None, "동명이역이 아니면 종전대로"


class _LO:
    def __init__(self, lines):
        self.doc = {"lines": lines}


def _line(nm_list, t=2.0):
    return {"stations": [{"station_nm": n} for n in nm_list],
            "edges": [{"a": a, "b": b, "travel_min": t} for a, b in zip(nm_list, nm_list[1:])]}


def test_u_graph_no_transfer():
    lo = _LO({"경의선": _line(["원덕", "양평", "오빈"]), "05호선": _line(["양평", "영등포구청"]),
              "04호선": _line(["사당", "이촌"]), "경의선2": _line(["이촌", "왕십리"])})
    cg = CandidateGraph(lo, None, RULES)
    assert cg.search("원덕", "영등포구청", "최단") is None, "② 양평에서는 갈아타지 않는다"
    got = cg.search("사당", "왕십리", "최단")
    assert got is not None and [l["line"] for l in got.legs] == ["04호선", "경의선2"], "제외 목록 밖(이촌)은 종전대로 환승"
    assert cg.search("양평", "영등포구청", "최단") is None, "제외 역명이 끝점이면 노선 없이는 안 만든다"
    c = cg.search("양평", "영등포구청", "최단", origin_lines=["05호선"])
    assert c is not None and c.legs == [{"line": "05호선", "from": "양평", "to": "영등포구청"}]
    assert cg.search("양평", "영등포구청", "최단", origin_lines=["경의선"]) is None
    # G2 — 동명이역 두 역 사이(경의선 양평 → 5호선 양평)는 같은 역이 아니다
    lo2 = _LO({"경의선": _line(["원덕", "양평", "왕십리"]), "05호선": _line(["왕십리", "양평"])})
    cg2 = CandidateGraph(lo2, None, RULES)
    c = cg2.search("양평", "양평", "최단", origin_lines=["경의선"], dest_lines=["05호선"])
    assert c is not None and [l["line"] for l in c.legs] == ["경의선", "05호선"], "G2 동명이역 두 역 사이 후보"
    assert cg2.search("양평", "양평", "최단", origin_lines=["경의선"], dest_lines=["경의선"]) is None, "같은 물리적 역이면 후보 없음"
    c = cg.search("원덕", "양평", "최단", dest_lines=["경의선"])
    assert c is not None and c.legs[-1]["line"] == "경의선"
    assert cg.search("원덕", "양평", "최단", dest_lines=["05호선"]) is None, "도착도 고른 노선의 역에서만 끝난다"


# ── D 데이터 ──────────────────────────────────────────────────────────────
_RT = None


class _Skip(Exception):
    pass


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:
            _RT = e
    if isinstance(_RT, Exception):
        try:
            import pytest
            pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
        except ImportError:
            raise _Skip()
    return _RT


def _transfer_at(legs):
    return [a["to"] for a, b in zip(legs, legs[1:]) if a.get("line") != b.get("line")]


def test_d_p90_multi_seoul_itaewon():
    rt = _runtime()
    r = rt.verify_case({"id": "55-p90", "date": "2026-09-29", "depart_at": "14:00",
                        "multi": {"from": "서울역", "to": "이태원"}})
    with_p90 = [c for c in r.candidates if (c.get("out") or {}).get("p90_eta_min") is not None]
    assert any(c["walk_in_min"] + c["walk_out_min"] > 0 for c in with_p90), "도보 붙은 버스 후보가 있어야 이 축이 뜻이 있다"
    for c in with_p90:
        o = c["out"]
        assert o["p90_eta_min"] >= o["eta_min"], f"① {c['label']} p90 {o['p90_eta_min']} < eta {o['eta_min']}"
        assert o["p90_eta_min"] <= o["eta_worst_min"], f"① {c['label']} p90 이 worst 를 넘는다"


def test_d_wondeok_no_yangpyeong_transfer():
    rt = _runtime()
    cg = rt._v.candidate_graph(True)
    got = cg.candidates("원덕", "영등포구청", RULES["candidates"]["기준"]["value"])
    crit = {k for c in got for k in c.criteria}
    assert {"최단", "최소환승"} <= crit, "최단·최소환승 후보는 있어야 한다(가짜 환승 대신 실제 경로)"
    for c in got:
        assert "양평" not in _transfer_at(c.legs), f"② {c.criteria} 가 양평에서 갈아탄다 — {c.legs}"
    # 이촌은 실제 환승역 — 사당→왕십리 최단은 종전대로 이촌 환승(MULTI-01)
    got = cg.candidates("사당", "왕십리", RULES["candidates"]["기준"]["value"])
    assert any("이촌" in _transfer_at(c.legs) for c in got), "이촌 환승은 남는다"


def test_d_exclusion_matches_coords():
    rt = _runtime()
    sc = rt._v.sc
    assert set(RULES["station_names"]["환승_제외_역명"]["value"]) == set(sc.ambiguous), \
        f"규칙 제외 목록과 좌표로 잰 동명이역이 다르다 — 목록 {RULES['station_names']['환승_제외_역명']['value']} · 좌표 {sorted(sc.ambiguous)}"


def test_d_yangpyeong_name_only_not_silent():
    rt = _runtime()
    v = rt._v
    # multi — 역명만이면 값 없음 + 경고
    r = rt.verify_case({"id": "55-yp", "date": "2026-09-29", "depart_at": "10:00", "multi": {"from": "양평", "to": "공덕"}})
    assert r.code == "no_data" and not r.candidates, "④ 역명만의 양평은 후보를 만들지 않는다"
    assert "MOB_W_STATION_AMBIGUOUS" in [w["code"] for w in r.warnings]
    # 노선을 주면 그 역으로 — 경의선이면 5호선 후보가 끼지 않는다
    r = rt.verify_case({"id": "55-yp-k", "date": "2026-09-29", "depart_at": "10:00",
                        "multi": {"from": "양평", "to": "공덕", "from_lines": ["경의선"]}})
    lines = {leg.get("line") for c in r.candidates for leg in c["legs"][:1] if leg.get("line")}
    assert lines == {"경의선"}, f"④ 경의선 양평 출발에 다른 노선 첫 구간 — {lines}"
    bike = [c for c in r.candidates if c["legs"][0].get("mode") == "bike"]
    for c in bike:
        frm = c["legs"][0]["from"]
        assert isinstance(frm, dict) and frm["lng"] > 127.4, "자전거 끝점은 고른 물리적 역(양평군) 좌표"
    # 자전거 구간 역명 입력
    r = rt.verify_case({"id": "55-yp-b", "date": "2026-09-29", "depart_at": "10:00",
                        "legs": [{"mode": "bike", "from": "양평", "to": "공덕"}], "no_alternatives": True})
    assert r.verdict == "unknown" and "MOB_W_STATION_AMBIGUOUS" in [w["code"] for w in r.warnings], \
        "④ 자전거 끝점 「양평」은 영등포 대여소를 조용히 찾지 않는다"
    # 판정기 _pt (노선 없음) · 좌표표 by_name
    assert v._pt(None, "양평") is None and v.sc.by_name.get("양평") is None
    assert v._pt("경의선", "양평")[0] > 127.4
    # 계획 — 양평군 장소 → 경의선 양평(노선군 실림)
    from app.modules.travel_ops.mobility.engine.plan import Planner, _multi
    p = Planner(rt)
    near = p._near_station({"lat": YP_GUN[0], "lon": YP_GUN[1]}, 1000)
    assert near is not None and near[0] == "양평" and near[2] == ["경의선"] and near[1] < 500, near
    m = _multi(near, ("공덕", 0, None))
    assert m == {"from": "양평", "to": "공덕", "from_lines": ["경의선"]}
    # 추정 — 역명 문자열 「양평」은 거절(장소로 달라고)
    from app.modules.travel_ops.mobility.engine.plan_estimate import Estimator
    try:
        Estimator(rt)._point("양평", 1000)
        raise AssertionError("④ plan_estimate 가 역명만의 양평을 받았다")
    except ValueError as e:
        assert "두 역" in str(e)
    # 출구표 — 노선 없이는 양평 출구를 안 준다 · 경의선 양평 출구는 양평군 쪽만
    ex = v.ex
    if ex is not None:
        assert ex.exits_of("양평") == []
        k = ex.exits_of("양평", "경의선")
        assert k and all(e["lng"] > 127.4 for e in k), "경의선 양평 출구에 영등포 출구가 섞였다"


def test_d_g1_legs_fake_transfer_blocked():
    rt = _runtime()
    case = {"id": "55-g1", "date": "2026-09-29", "depart_at": "15:30", "no_alternatives": True,
            "legs": [{"line": "경의선", "from": "원덕", "to": "양평"}, {"line": "05호선", "from": "양평", "to": "영등포구청"}]}
    r = rt.verify_case(case)
    assert r.verdict == "infeasible" and r.code == "transfer_walk", f"G1 가짜 환승이 성립했다 — {r.verdict} {r.reason}"
    assert (r.out or {}).get("verdict") == "infeasible"
    # 실제 환승역은 종전대로 — 사당→이촌(4호선) + 이촌→왕십리(경의선)
    ok = rt.verify_case({"id": "55-g1-ok", "date": "2026-09-29", "depart_at": "14:00", "no_alternatives": True,
                         "legs": [{"line": "04호선", "from": "사당", "to": "이촌"}, {"line": "경의선", "from": "이촌", "to": "왕십리"}]})
    assert ok.code != "transfer_walk", f"이촌 환승이 막혔다 — {ok.reason}"


def test_d_g2_between_homonyms():
    rt = _runtime()
    from app.modules.travel_ops.mobility.engine.plan import Planner
    p = Planner(rt)
    a = p._near_station({"lat": YP_GUN[0], "lon": YP_GUN[1]}, 1000)
    b = p._near_station({"lat": YP_YDP[0], "lon": YP_YDP[1]}, 1000)
    assert a[2] == ["경의선"] and b[2] == ["05호선"], (a, b)
    assert not same_station(a[0], a[2], b[0], b[2])
    r = rt.verify_case({"id": "55-g2", "date": "2026-09-29", "depart_at": "16:00",
                        "multi": {"from": "양평", "to": "양평", "from_lines": ["경의선"], "to_lines": ["05호선"]}})
    assert r.candidates, f"G2 동명이역 두 역 사이 후보가 없다 — {r.reason}"
    for c in r.candidates:
        legs = [x for x in c["legs"] if x.get("line")]
        if legs:
            assert legs[0]["line"] == "경의선" and legs[-1]["line"] == "05호선", c["label"]
    from app.modules.travel_ops.mobility.engine.plan_estimate import Estimator
    out = Estimator(rt).estimate({"name": "양평군", "lat": YP_GUN[0], "lon": YP_GUN[1]},
                                 {"name": "영등포 양평동", "lat": YP_YDP[0], "lon": YP_YDP[1]}, "2026-09-29", "오후")
    assert out.get("verdict") != "no_data", f"G2 plan_estimate 가 같은 역으로 봤다 — {out.get('reason')}"


if __name__ == "__main__":
    fails, skips, n = 0, 0, 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            n += 1
            try:
                fn()
                print(f"  ok   {name}")
            except _Skip:
                skips += 1
                print(f"  SKIP {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
            except Exception as e:
                fails += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"[verify55] {n - fails - skips}/{n} 통과 · SKIP {skips} · 실패 {fails}")
    sys.exit(1 if fails else 0)
