# final_project_cs/tests/unit/travel/mobility/test_plan_bike_v1.py — 58번 방 · plan() 자전거 후보(◆테마 ① 자전거)
# 실행: 저장소 루트에서
#   $env:PYTHONPATH = "final_project_cs"
#   python final_project_cs/tests/unit/travel/mobility/test_plan_bike_v1.py      # 단위 + (시간표가 있으면) 골든
#   python -m pytest tests/unit/travel/mobility/test_plan_bike_v1.py
# 실패하면 종료코드 1.
#
# 규칙(plan._bike_direct): 따릉이는 24시간(rules bike.ddareungi.no_timetable) → lfd = 도착 목표 − (eta + @)
#   @ = 판정기 margin_min(자전거는 스프레드 없음 → 단계 정책 버퍼) · 그 lfd 에서 다시 판정해 성립한 것만 싣는다.
#   장소 좌표 기준(판정기 multi 의 역 기준 자전거 후보는 안 싣는다) · 계획 단계는 실시간 거치를 안 본다(가용 근거없음).
#
# 축
#   U  단위(데이터 없이 · 가짜 판정기) — 출발 식 · 다시 판정 · 앞 일정 끝 이후 재판정 · 불성립 이유 · 04:00 경계 ·
#      계획 단계 실시간 떼기(복사본만) · multi 의 역 기준 자전거 후보 건너뜀
#   G  골든(시간표 + 따릉이 표가 있는 기기) — 예시 입력 `--modes bike,walk`(자전거 테마 호출 모양) → plan_example_out_bike_v1.json.
#      자전거는 추천하지 않는다(본인 9/27) — 여행자가 자전거로 이동한다고 했을 때만 bike(+walk). 섞어 주면 left_out 이유만
#      (그 잠금은 test_plan_concurrency_v1 B).
#      GraphHopper 대신 **요약 픽스처**(plan_bike_gh_fixture_v1.json · 집 PC 에서 GH 로 기록 · 형상 없음)로 돌린다 →
#      GH 없는 노트북에서도 같은 값. 픽스처에 없는 좌표는 소요 근거없음 → 자전거가 빠져 골든과 달라진다(의도)
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

# ★ 71번 방(2026-09-29) — 전체층 마커. 실데이터 축은 기본 `pytest` 에서 빠지고 `-m mobility_full` 로 돈다
#   (conftest.py). 스크립트로 직접 돌릴 때는 pytest 가 없어도 되게 감싼다.
try:
    import pytest
    _full = pytest.mark.mobility_full          # 실데이터(DATA_DIR)를 읽는 시험에만 붙인다 — 합성 단위는 게이트에 남는다
except ImportError:     # pragma: no cover
    _full = lambda f: f                        # noqa: E731

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.modules.travel_ops.mobility.engine.plan import Planner, plan  # noqa: E402
from app.modules.travel_ops.mobility.engine.runtime import Runtime  # noqa: E402

IN = HERE / "plan_example_in_v1.json"
GOLD_BIKE = HERE / "plan_example_out_bike_v1.json"
GH_FIX = HERE / "plan_bike_gh_fixture_v1.json"
MODES_BIKE = ["bike", "walk"]                  # 자전거 테마 호출 모양(58 · 본인)
KST_DAY = "2026-09-29"


# ── U 단위 ────────────────────────────────────────────────────────────────
def _fake(eta=30, buf=10, feasible=True, ride=True, seq=None, fail_at=None):
    """자전거 구간만 판정하는 가짜 판정기 — 소요 eta · @ = 버퍼 · 도착 목표가 있으면 slack 을 낸다.
    seq: 호출 순서별 소요(없으면 eta 고정 · 모자라면 마지막 값) — 판정마다 소요가 바뀌는 모양(GPT 58 #7)
    fail_at: 이 번째(0부터) 호출에서 소요를 못 낸다(라우터 끊김 모양)."""
    R = {"measured_baseline": {"kakao_walk_speed_mps": {"value": 1.04}},
         "transfer": {"stop_station_walk": {"detour_factor": {"value": 1.3}}},
         "candidates": {"버스_직행_최대": {"value": 2}},
         "bike": {"ddareungi": {"fare": {}}}}
    calls = []

    class V:
        def __init__(self):
            self.R, self.bk, self.bike_live, self.sc, self.ex, self.bus = R, object(), object(), None, None, None

        def rv(self, *k):
            return buf if k[0] == "buffer" else None

        def _walk_limit(self, party):
            return 800

        def verify_case(self, case):
            i = len(calls)
            calls.append((case["depart_at"], case.get("arrive_by"), self))
            assert [x["mode"] for x in case["legs"]] == ["bike"] and isinstance(case["legs"][0]["from"], dict)
            e = eta if seq is None else seq[min(i, len(seq) - 1)]
            if not feasible:
                o = {"verdict": "infeasible", "code": "mode_unavailable", "reason": "반경 300m 안 대여소 없음"}
                return NS(out=o, reason=o["reason"], legs=[], day_type="평일", verdict="infeasible", warnings=[])
            if not ride or i == fail_at:
                o = {"verdict": "infeasible", "code": "no_data", "reason": "승차 소요 근거없음"}
                return NS(out=o, reason=o["reason"], legs=[], day_type="평일", verdict="feasible", warnings=[])
            o = {"verdict": "feasible", "eta_min": e, "margin_min": buf, "depart_min": case["depart_at"]}
            if case.get("arrive_by") is not None:
                slack = case["arrive_by"] - case["depart_at"] - e - buf
                o["slack_min"] = slack
                if slack < 0:           # 판정기와 같이 — 늦어도 eta·@ 는 낸다(verify_case arrive_late 분기)
                    o = {"verdict": "infeasible", "code": "arrive_late", "reason": "늦는다", "eta_min": e,
                         "margin_min": buf, "slack_min": slack}
            return NS(out=o, reason=o.get("reason"), legs=[NS(walk_min=8, ride_min=None)], day_type="평일",
                      verdict=o["verdict"], warnings=[])
    v = V()
    return Runtime(v, timetable_built_at="t", rules_version="r", stats={}), v, calls


A = {"name": "A", "lat": 37.5636, "lon": 126.9826}
B = {"name": "B", "lat": 37.5445, "lon": 127.0560}          # 약 7 km — 도보 직행 후보가 안 생긴다


def _dt(hhmm, day=KST_DAY):
    from datetime import datetime
    return datetime.fromisoformat(f"{day}T{hhmm}:00+09:00")


def test_bike_lfd_rule():
    """출발 = 목표 − (eta + @). 13:30 도착 · eta 30 · @ 10 → 12:50 출발 · 13:20 도착 · 그 출발에서 다시 판정."""
    rt, v, calls = _fake()
    P = Planner(rt, modes=["bike"])
    got, why = P.leg(A, B, _dt("13:30"), {}, True, "a_to_b")
    assert why is None, why
    route, start, end, _sd, left = got
    assert (start, end) == (13 * 60 + 30 - 40, 13 * 60 + 30 - 10), (start, end)
    assert route["planned"] == "bike" and [o["id"] for o in route["options"]] == ["bike"], route
    o = route["options"][0]
    assert o["eta_min"] == 30 and o["uses"] == [] and "walk_m" not in o, o
    assert "대여 가능 여부는 출발 때 확인" in o["label"], o["label"]
    assert calls[-1][:2] == (start, 810), "역산 출발에서 도착 목표를 걸고 다시 판정해야 한다"
    assert all(c[2] is not v for c in calls), "판정은 싱글턴이 아니라 복사본에서(56)"


def test_bike_after_prev_end():
    """앞 일정이 12:55 에 끝나면 12:50 출발 자전거는 자격이 없다 → 12:55 재판정 → 늦음(5분) → 구간 못 만듦."""
    rt, _v, _ = _fake()
    P = Planner(rt, modes=["bike"])
    got, why = P.leg(A, B, _dt("13:30"), {}, True, "a_to_b", not_before_dt=_dt("12:55"))
    assert got is None and why["code"] == "arrive_late", why
    got, why = P.leg(A, B, _dt("13:30"), {}, True, "a_to_b", not_before_dt=_dt("12:50"))
    assert why is None and got[1] == 770


def test_bike_not_feasible_reason():
    """자전거만 고른 구간에서 자전거가 안 되면 그 이유를 낸다(역이 없다는 이유로 덮지 않는다) · 소요 모름도 불성립."""
    for kw, code in (({"feasible": False}, "mode_unavailable"), ({"ride": False}, "no_data")):
        rt, _v, _ = _fake(**kw)
        got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
        assert got is None and why["code"] == code and why["reason"].startswith("자전거"), (kw, why)
    rt, _v, _ = _fake(feasible=False)
    got, why = Planner(rt, modes=["subway", "bike"]).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
    assert got is None and not why["reason"].startswith("자전거"), "지하철도 고른 구간은 역 이유를 그대로"
    # 다른 후보(도보)는 실리고 자전거가 안 되면 — 봉투 left_out 에 자전거 이유 한 줄(코어로는 안 나감)
    near = {"name": "C", "lat": 37.5650, "lon": 126.9826}             # A 에서 약 160 m
    got, why = Planner(rt, modes=["walk", "bike"]).leg(A, near, _dt("13:30"), {}, True, "a_to_c")
    assert why is None and got[0]["planned"] == "walk", (why, got)
    assert [(e["label"], e["code"]) for e in got[4]] == [("자전거(따릉이)", "mode_unavailable")], got[4]


def test_bike_service_day_edge():
    """04:20 도착 · eta 30 · @ 10 → 03:40 출발 = 앞 운행일 축 → 보지 않는다(정수 분이 +24h 로 읽힌다)."""
    rt, _v, calls = _fake()
    got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("04:20"), {}, True, "a_to_b")
    assert got is None and "04:00" in why["reason"], why
    assert len(calls) == 1, "역산 전 한 번만 판정"


def test_planning_stage_drops_live_on_copy():
    """계획 단계 + bike → 복사본에서만 실시간 거치를 뗀다(싱글턴·다른 단계는 그대로)."""
    rt, v, _ = _fake()
    live = v.bike_live
    assert Planner(rt, modes=["bike"]).v.bike_live is None
    assert v.bike_live is live, "싱글턴의 실시간 클라이언트는 그대로"
    assert Planner(rt, modes=["bike"], stage="pre_departure").v.bike_live is live, "출발 전·진행 중은 실시간을 본다"
    assert Planner(rt, modes=["subway", "walk"]).v.bk is None, "bike 없으면 종전대로 따릉이 표를 뗀다(56)"


def test_default_no_bike_calls():
    """기본 modes 는 자전거를 부르지 않는다(56 그대로)."""
    rt, _v, calls = _fake()
    got, why = Planner(rt).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
    assert got is None and not calls, calls


def test_bike_eta_changes_recompute():
    """GPT 58 #3 — 재판정 소요가 첫 판정과 다르면 새 값으로 한 번 더 · 같아질 때만 싣는다(그때 slack 0 = 마지막 성립 출발).
    줄어듦 40→30: 13:10 이 아니라 13:20 · 늘어남 30→40: 13:20 불성립에서 버리지 않고 13:10 · 계속 바뀜: 뺀다."""
    for seq, want in (([40, 30, 30], 810 - 40), ([30, 40, 40], 810 - 50)):
        rt, _v, calls = _fake(seq=seq)
        got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
        assert why is None and got[1] == want and got[0]["options"][0]["eta_min"] == seq[-1], (seq, why, got and got[1])
        assert len(calls) == 3, calls
    rt, _v, calls = _fake(seq=[30, 40, 50])
    got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
    assert got is None and "판정마다 소요가 달라" in why["reason"] and len(calls) == 3, (why, calls)


def test_bike_recheck_fails_not_listed():
    """GPT 58 #7 — 첫 판정은 되고 재판정에서 소요를 못 내면 싣지 않는다 · 도보가 실리면 봉투 left_out 에 자전거 이유."""
    rt, _v, _ = _fake(fail_at=1)
    got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {}, True, "a_to_b")
    assert got is None and why["code"] == "no_data" and "소요를 못 냈다" in why["reason"], why
    near = {"name": "C", "lat": 37.5650, "lon": 126.9826}
    rt, _v, _ = _fake(fail_at=1)                         # 새 판정기(호출 순번을 처음부터)
    got, why = Planner(rt, modes=["walk", "bike"]).leg(A, near, _dt("13:30"), {}, True, "a_to_c")
    assert why is None and [o["id"] for o in got[0]["options"]] == ["walk"], got
    assert [(e["label"], e["code"]) for e in got[4]] == [("자전거(따릉이)", "no_data")], got[4]


def test_bike_service_day_boundary_exact():
    """GPT 58 #4 — 04:00 출발(240)은 보고 03:59(239)는 안 본다(운행일 축 제한 · 운행 불가가 아님을 이유에 적는다)."""
    rt, _v, _ = _fake()
    got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("04:40"), {}, True, "a_to_b")
    assert why is None and got[1] == 240, (why, got)
    got, why = Planner(rt, modes=["bike"]).leg(A, B, _dt("04:39"), {}, True, "a_to_b")
    assert got is None and "표현 축" in why["reason"], why


def test_bike_stage_buffer_and_prev_end_exact():
    """GPT 58 #8 — 진행 중 버퍼 15: 13:30 − (30+15) = 12:45 출발 · 앞 일정이 정확히 12:45 에 끝나면 싣는다(경계 포함)
    · 출발 전·진행 중은 「출발 때 확인」 문구를 안 붙인다(실시간을 본다)."""
    rt, _v, _ = _fake(buf=15)
    got, why = Planner(rt, modes=["bike"], stage="in_progress").leg(A, B, _dt("13:30"), {}, True, "a_to_b",
                                                                     not_before_dt=_dt("12:45"))
    assert why is None and (got[1], got[2]) == (765, 795), (why, got)
    assert "출발 때 확인" not in got[0]["options"][0]["label"], got[0]["options"][0]["label"]


@_full
def test_bike_party_size_label():
    """GPT 58 #6 — 판정기는 거치 ≥1 만 본다(인원수 대수 미확인) — label 에 필요 대수를 적는다."""
    rt, _v, _ = _fake()
    got, _w = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {"size": 3}, True, "a_to_b")
    assert "3명 — 3대 필요" in got[0]["options"][0]["label"], got[0]["options"][0]["label"]
    got, _w = Planner(rt, modes=["bike"]).leg(A, B, _dt("13:30"), {"size": 1}, True, "a_to_b")
    assert "대 필요" not in got[0]["options"][0]["label"]


# ── 데이터 축 ────────────────────────────────────────────────────────────────
_RT = None


class _Skip(Exception):
    pass


def _skip(msg):
    try:
        import pytest
        pytest.skip(msg)
    except ImportError:
        raise _Skip()


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:
            _RT = e
    return None if isinstance(_RT, Exception) else _RT


def fixture_runtime(rt):
    """GH 대신 요약 픽스처 · 실시간 없음 — 싱글턴은 안 건드린다(복사본)."""
    from app.modules.travel_ops.mobility.engine.bike import BikeRouter
    fx = json.loads(GH_FIX.read_text(encoding="utf-8"))
    r2 = copy.copy(rt)
    r2._v = copy.copy(rt._v)
    r2._v.bike_live = None
    r2._v.bike_router = BikeRouter(None, fx["routes"], fx.get("pbf_date"))
    return r2


def _need_bike_data():
    rt = _runtime()
    if rt is None:
        _skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    if rt._v.bk is None:
        _skip("따릉이 대여소 표 없음 — 자전거 데이터 축 SKIP")
    if not GOLD_BIKE.exists() or not GH_FIX.exists():
        _skip("자전거 골든·픽스처 없음(집 PC gen58 이 만든다)")
    return rt


def _run(rt, modes):
    d = json.loads(IN.read_text(encoding="utf-8"))
    out = plan(d["places"], d["items"], d.get("party_size"), d.get("constraints"),
               runtime=rt, routes=d.get("routes"), modes=modes)
    out.pop("basis", None)
    return out


@_full
def test_golden_bike():
    """G — 자전거 테마 예시(bike+walk)가 골든과 같다 · 자전거가 한 구간 이상 계획 수단 · 옵션 키는 계약 칸뿐
    · 못 만든 구간은 이유가 「자전거 — …」(대중교통으로 바꾸지 않는다)."""
    rt = _need_bike_data()
    got = _run(fixture_runtime(rt), MODES_BIKE)
    want = json.loads(GOLD_BIKE.read_text(encoding="utf-8"))
    assert got == want, "자전거 포함 예시가 골든과 다르다 — 픽스처·시간표·규칙이 바뀌었으면 gen58 로 다시 뽑고 이유를 적는다"
    bikes = [o for r in got["routes"].values() for o in r["options"] if o["id"] == "bike"]
    assert bikes and any(r["planned"] == "bike" for r in got["routes"].values()), "자전거가 한 구간도 계획 수단이 아니다(58 목적)"
    assert all(o["id"] in ("bike", "walk") for r in got["routes"].values() for o in r["options"]), got["routes"]
    assert all(s["reason"].startswith("자전거") or s["code"] == "arrive_late" for s in got["skipped"]), got["skipped"]
    for o in bikes:
        assert set(o) <= {"id", "label", "eta_min", "fare_krw", "uses"} and o["uses"] == [], o


@_full
def test_bike_only_request():
    """자전거만 요청하면 옵션은 전부 자전거 · 계획 수단 bike · **이동 끝 + 계획 버퍼 = 다음 일정 시작**(slack 0 — 마지막
    성립 출발) · 이동 시작 ≥ 앞 일정 끝 (GPT 58 #8 — 설명만 있고 검사가 없던 것)."""
    from datetime import datetime
    rt = _need_bike_data()
    got = _run(fixture_runtime(rt), ["bike"])
    assert got["routes"], got["skipped"]
    for key, r in got["routes"].items():
        assert [o["id"] for o in r["options"]] == ["bike"] and r["planned"] == "bike", (key, r)
    buf = rt._v.rv("buffer", "by_stage", "planning")
    its = got["items"]
    t = lambda x: datetime.fromisoformat(x)                    # noqa: E731
    for i, it in enumerate(its):
        if it["kind"] != "mobility" or it.get("route") not in got["routes"]:
            continue
        prev, nxt = its[i - 1], its[i + 1]
        assert (t(nxt["starts_at"]) - t(it["ends_at"])).total_seconds() == buf * 60, (it, nxt)
        assert t(it["starts_at"]) >= t(prev.get("ends_at") or prev["starts_at"]), (prev, it)
        eta = got["routes"][it["route"]]["options"][0]["eta_min"]
        assert (t(it["ends_at"]) - t(it["starts_at"])).total_seconds() == eta * 60, it


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
            except Exception as e:                  # 예외도 실패로 센다
                fails += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"[plan58] {n - fails - skips}/{n} 통과 · SKIP {skips} · 실패 {fails}")
    sys.exit(1 if fails else 0)
