# final_project_cs/tests/unit/travel/mobility/test_plan_concurrency_v1.py — 56번 방 · 판정기 동시성(①) + 자전거 호출 끊기(②) 회귀
# 실행: 저장소 루트에서
#   $env:PYTHONPATH = "final_project_cs"
#   python final_project_cs/tests/unit/travel/mobility/test_plan_concurrency_v1.py      # 단위 + (시간표가 있으면) 데이터 축
#   python -m pytest tests/unit/travel/mobility/test_plan_concurrency_v1.py
# 실패하면 종료코드 1.
#
# 축
#   U  단위(데이터 없이) — Planner·Runtime 이 싱글턴이 아니라 얕은 복사본으로 판정 · 기본 수단에 자전거 없음 ·
#      자전거를 안 볼 때 복사본에서만 따릉이 표를 뗀다(싱글턴은 그대로)
#   B  자전거 — 기본 plan() 은 자전거 구간 판정을 한 번도 부르지 않는다(따릉이 실시간·라우터 호출 0) ·
#      기본 결과 = 지하철·버스·도보 골든(plan_example_out_all_v1) · (58 뒤집음) 지하철·버스와 섞어 bike 를 주면 자전거 후보를
#      판정은 하되 **추천하지 않는다** — items·routes 는 기본과 같고 구간마다 봉투 left_out 에 자전거 이유가 한 줄(본인 9/27)
#      · 자전거만 요청하면 옵션이 전부 자전거(출발 = 목표 − (eta+@) · plan._bike_direct)
#      — 56 때는 「켜도 같음」·「자전거만 = 옵션 0」 으로 잠갔다(시간표 없는 수단은 lfd None 이라 거름)
#   I  끼어들기 흉내(결정적) — 판정 도중 **다른 요청이 같은 싱글턴에** verify_case 를 부른 모양을 만든다.
#      ⓐ 싱글턴에 직접 부르면 섞인다(실패 장면 — 시험이 섞임을 잡을 수 있다는 증거)
#      ⓑ 복사본을 끄면(56 전 모양) plan() 결과가 달라진다 ⓒ 56 판 plan() 은 같다
#   T  스레드 8 × 80건 — 24 ① 과 같은 조건(세 입력 화 9/29 · 일 9/27 · 토 10/3 · 자전거 뺌) + first_visit=False 한 벌로
#      순차 결과와 대조. **새 Runtime 을 세워 캐시가 빈 채로 스레드부터 시작**한다(GPT 56 #3 — 공유 캐시 최초 생성 경쟁).
#      ★ 어긋남 0 은 「안전 증명」이 아니라 회귀다(GIL 전환 시점에 달림). 결정적 증거는 I 축과 U 의 합성 끼어들기.
#   L  (GPT 56 #1) 판정 로그 install — 호출 깊이가 스레드마다(겹친 두 최상위 호출이 둘 다 기록된다)
#
# 공유로 남는 것(격리 대상 아님 · 56 인계 §확인 안 한 것): 시간표·표 · 캐시(_passes/_origin/_dominant · _cg · options._NETS)
#   · 따릉이 실시간·라우터 클라이언트(calls 는 프로세스 누적) — 격리하는 것은 verify_case 가 건마다 **재할당**하는 상태뿐이다.
import copy
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.modules.travel_ops.mobility.engine import plan as plan_mod  # noqa: E402
from app.modules.travel_ops.mobility.engine import judgment_log as jl  # noqa: E402
from app.modules.travel_ops.mobility.engine.plan import DEFAULT_MODES, Planner, plan  # noqa: E402
from app.modules.travel_ops.mobility.engine.runtime import Runtime  # noqa: E402

IN = HERE / "plan_example_in_v1.json"
GOLD_ALL = HERE / "plan_example_out_all_v1.json"
GH_FIX = HERE / "plan_bike_gh_fixture_v1.json"     # 58 — GH 요약 픽스처(집 PC 기록 · 형상 없음) — GH 없는 기기도 같은 값
MODES_ALL = ["subway", "walk", "bus"]
THREADS, TOTAL = 8, 80                      # 24 ① 과 같은 규모(8스레드 × 5회 × 복사본 40 + 공유 40 = 80)
# 다른 요청 — 2호선 운행중단 사건이 붙은 재판정. 도착 목표를 새벽으로 둬 역산 후보가 몇 개 안 되게(시험 시간)
HOSTILE = {"id": "hostile", "date": "2026-10-03", "stage": "planning",
           "legs": [{"line": "02호선", "from": "성수", "to": "강남"}], "depart_at": 320, "arrive_by": 345,
           "no_alternatives": True,
           "disruptions": [{"kind": "line_closed", "line": "02호선", "grade": "확정", "source": "test56"}]}


# ── U 단위 ────────────────────────────────────────────────────────────────
def _fake_runtime():
    R = {"measured_baseline": {"kakao_walk_speed_mps": {"value": 1.04}},
         "transfer": {"stop_station_walk": {"detour_factor": {"value": 1.3}}}}
    seen = []

    class V:
        def __init__(self):
            self.R, self.bk, self._case_date, self.disr = R, object(), None, []

        def verify_case(self, case):
            seen.append(self)
            self._case_date, self.disr = case["date"], case.get("disruptions") or []
            return case["id"]
    v = V()
    return Runtime(v, timetable_built_at="t", rules_version="r", stats={}), v, seen


def test_default_modes_no_bike():
    assert set(DEFAULT_MODES) == {"subway", "bus", "walk"}, "56 결정 — 기본은 지하철·버스·도보 · 자전거는 요청 시만"
    rt, v, _ = _fake_runtime()
    p = Planner(rt)
    assert p.modes == {"subway", "bus", "walk"}
    assert p.v is not v, "Planner 는 싱글턴이 아니라 복사본으로 판정한다(①)"
    assert p.v.bk is None, "자전거를 안 볼 때는 복사본에서 따릉이 표를 뗀다 — 후보 생성 전에 끊긴다(②)"
    assert v.bk is not None, "싱글턴의 따릉이 표는 그대로(다른 요청이 자전거를 볼 수 있다)"
    p2 = Planner(rt, modes=["subway", "bike"])
    assert p2.v.bk is v.bk, "bike 를 주면 표를 그대로 둔다"
    assert Planner(rt, modes=["subway"]).v.bk is None


def test_modes_contract():
    """GPT 56 #9 — None 만 기본 · 빈 목록·모르는 수단은 거절(조용히 넓히지 않는다)."""
    rt, _v, _ = _fake_runtime()
    assert Planner(rt, modes=None).modes == set(DEFAULT_MODES)
    assert Planner(rt, modes=["bike"]).modes == {"bike"}
    for bad in ([], set(), ["car"], ["subway", "tram"]):
        try:
            Planner(rt, modes=bad)
        except ValueError:
            continue
        raise AssertionError(f"modes={bad!r} 를 받아들였다")


def test_runtime_interleave_synthetic():
    """GPT 56 #7 — 결정적 합성 끼어들기. 판정 도중 다른 요청이 **싱글턴**을 부른다.
    싱글턴 직접 호출은 남의 사건을 돌려준다(실패 장면) · Runtime.verify_case(복사본)는 제 것을 돌려준다."""
    hook = {"on": False}

    class V:
        def __init__(self):
            self.disr = []

        def verify_case(self, case):
            self.disr = case["d"]
            if hook["on"]:
                hook["on"] = False
                singleton.verify_case({"d": ["남의 사건"]})
            return list(self.disr)
    singleton = V()
    rt = Runtime(singleton, timetable_built_at="t", rules_version="r", stats={})
    hook["on"] = True
    assert singleton.verify_case({"d": ["내 사건"]}) == ["남의 사건"], "합성 끼어들기가 안 먹었다(시험이 약하다)"
    hook["on"] = True
    assert rt.verify_case({"d": ["내 사건"]}) == ["내 사건"], "복사본인데 남의 사건이 섞였다"


def test_log_depth_per_thread():
    """GPT 56 #1 — install() 의 호출 깊이가 스레드마다. 두 최상위 호출을 겹치면 둘 다 기록된다(실패 장면: 공유 dict 면 1)."""
    gate = threading.Barrier(2, timeout=10)

    class FakeV:
        R, tt = {"rules_version": "t"}, None

        def verify_case(self, case):
            gate.wait()                     # 둘이 동시에 판정 중인 순간을 만든다
            time.sleep(0.05)
            return case["id"]

    class Log:
        n_judge_error = 0

        def __init__(self):
            self.rows = []

        def open(self):
            pass

        def close(self):
            pass

        def record(self, case, r, **kw):
            self.rows.append(case["id"])
    lg = Log()
    with jl.install(FakeV, lg):
        ts = [threading.Thread(target=FakeV().verify_case, args=({"id": k},)) for k in ("a", "b")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert sorted(lg.rows) == ["a", "b"], f"겹친 최상위 호출 기록 {lg.rows} — 깊이가 스레드 사이에 샜다"


def test_runtime_verify_case_copies():
    rt, v, seen = _fake_runtime()
    assert rt.verify_case({"id": "a", "date": "2026-09-29"}) == "a"
    assert seen and seen[0] is not v, "Runtime.verify_case 는 건마다 복사본에서 돈다"
    assert v._case_date is None and v.disr == [], "싱글턴의 건별 상태는 안 바뀐다"


# ── 데이터가 필요한 축 ──────────────────────────────────────────────────────
_RT = None


class _Skip(Exception):
    pass


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:          # 시간표가 없는 기기 — 데이터 축은 SKIP
            _RT = e
    return None if isinstance(_RT, Exception) else _RT


def _need_data():
    if _runtime() is None:
        try:
            import pytest
            pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
        except ImportError:
            raise _Skip()


def _doc(date=None):
    d = json.loads(IN.read_text(encoding="utf-8"))
    if date:
        for it in d["items"]:
            for k in ("starts_at", "ends_at"):
                if it.get(k):
                    it[k] = date + it[k][10:]
    return d


def _plan(d, runtime, **kw):
    out = plan(d["places"], d["items"], d.get("party_size"), d.get("constraints"),
               runtime=runtime, routes=d.get("routes"), **kw)
    out.pop("basis", None)                 # decided_at(벽시계 분)은 대조에서 뺀다(24 주의)
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def _state(rt):
    v = rt._v
    return (v._case_date, v.disr, v._leg_cache, v.lfd_capped)


def _singleton_clean(rt, before):
    """건별 상태 넷이 그대로(_leg_cache 는 **같은 객체**여야 — 재할당도 없었다 · GPT 56 #2)."""
    now = _state(rt)
    return now[0] == before[0] and now[1] is before[1] and now[2] is before[2] and now[3] == before[3]


def test_bike_not_called_by_default():
    """B — 기본 plan() 은 자전거 구간 판정을 부르지 않는다. 실패 장면: 56 전에는 기본(=전부)이 자전거 후보마다 불렀다."""
    _need_data()
    rt = _runtime()
    V = type(rt._v)
    calls = {"n": 0}
    orig = V.verify_leg_bike

    def counted(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)
    V.verify_leg_bike = counted
    try:
        d = _doc()
        got = json.loads(_plan(d, rt))
        assert calls["n"] == 0, f"기본 plan() 이 자전거 구간을 {calls['n']}번 판정했다(따릉이 실시간·라우터 호출)"
        # 자전거를 켜면 판정을 부르고 자전거 옵션이 실린다(58) — 라우터는 요약 픽스처 · 실시간 없음(복사본에서)
        from app.modules.travel_ops.mobility.engine.bike import BikeRouter
        fx = json.loads(GH_FIX.read_text(encoding="utf-8"))
        rt2 = copy.copy(rt)
        rt2._v = copy.copy(rt._v)
        rt2._v.bike_live = None
        rt2._v.bike_router = BikeRouter(None, fx["routes"], fx.get("pbf_date"))
        with_bike = json.loads(_plan(d, rt2, modes=MODES_ALL + ["bike"]))
        if rt._v.bk is not None:
            assert calls["n"] > 0, "bike 를 주면 자전거 후보를 만든다(따릉이 표가 있는 기기)"
    finally:
        V.verify_leg_bike = orig
    want = json.loads(GOLD_ALL.read_text(encoding="utf-8"))
    assert got == want, "기본 결과가 지하철·버스·도보 골든과 다르다 — 기본 수단이 바뀌었나"
    if rt._v.bk is not None:
        # 58 뒤집음 ① — 섞어 주면 자전거는 판정되지만 추천되지 않는다: 몸통(items·routes)은 기본과 같고
        #   구간마다 left_out 에 자전거 이유(56: 「켜도 결과 전부 같음」 — 자전거가 판정 밖에서 사라졌다)
        assert (with_bike["items"], with_bike["routes"]) == (got["items"], got["routes"]), "섞어 준 bike 가 계획을 바꿨다"
        assert with_bike != got, "자전거 이유가 left_out 에 안 남았다(58 이전 모양)"
        for k in got["routes"]:
            assert any(e["label"].startswith("자전거(따릉이)") for e in with_bike["left_out"].get(k, [])), (k, with_bike["left_out"].get(k))
        # 58 뒤집음 ② — 자전거만 요청: 옵션이 전부 자전거(56: 「옵션 0 · skipped」)
        only = json.loads(_plan(d, rt2, modes=["bike"]))
        assert only["routes"] and all([o["id"] for o in r["options"]] == ["bike"] for r in only["routes"].values()), only


def test_interleave_injection():
    """I — 판정 도중 다른 요청이 같은 싱글턴에 끼어든 모양(결정적). ⓐ 싱글턴 직접 = 섞임(실패 장면) ⓑ 복사본 끔 = 섞임 ⓒ 56 = 같음."""
    _need_data()
    rt = _runtime()
    V = type(rt._v)
    orig = V._chain
    arm = {"n": 0}

    def chain(self, *a, **k):
        if arm["n"] > 0:
            arm["n"] -= 1
            saved = arm["n"]
            arm["n"] = 0                   # 끼어든 요청 안에서는 다시 끼어들지 않는다
            rt._v.verify_case(HOSTILE)     # ★ 다른 요청 — 언제나 **싱글턴**에
            arm["n"] = saved
        return orig(self, *a, **k)

    before = (rt._v._case_date, rt._v.disr, rt._v._leg_cache, rt._v.lfd_capped)
    case = {"id": "own", "date": "2026-09-29", "stage": "planning",
            "legs": [{"line": "02호선", "from": "을지로3가", "to": "성수"}], "depart_at": 760,
            "no_alternatives": True}
    d = _doc()
    V._chain = chain
    try:
        # ⓐ 싱글턴에 직접 — 끼어든 요청의 사건(2호선 운행중단)이 내 판정에 묻는다
        ref = rt._v.verify_case(case)
        arm["n"] = 1
        got = rt._v.verify_case(case)
        assert ref.verdict == "feasible", ref.verdict
        assert got.verdict != ref.verdict, "끼어들기 흉내가 안 먹었다 — 이 시험이 섞임을 못 잡는다"

        # ⓑ 복사본을 끄면(56 전 Planner) plan() 결과가 달라진다
        want = _plan(d, rt)
        saved_bk = rt._v.bk
        plan_mod.copy = NS(copy=lambda x: x)
        try:
            arm["n"] = 5
            bad = _plan(d, rt)
        finally:
            plan_mod.copy = copy
            rt._v.bk = saved_bk            # 복사본 없이 돌면 Planner 가 싱글턴의 따릉이 표를 뗀다 — 되돌린다
            rt._v._case_date, rt._v.disr, rt._v._leg_cache, rt._v.lfd_capped = before
        assert bad != want, "복사본 없이도 같다 — 끼어들기가 plan() 에 안 닿았다(시험이 약하다)"

        # ⓒ 56 판 — 끼어들어도 같다
        arm["n"] = 5
        good = _plan(d, rt)
        assert good == want, "복사본을 쓰는데도 끼어든 요청이 plan() 결과를 바꿨다"
    finally:                               # GPT 56 #6 — 실패해도 패치·싱글턴 상태를 되돌린다(다음 시험 오염 방지)
        V._chain = orig
        plan_mod.copy = copy
        rt._v._case_date, rt._v.disr, rt._v._leg_cache, rt._v.lfd_capped = before


def test_threads_8x80():
    """T — 24 ① 과 같은 조건 + first_visit=False · 스레드 8 × 80건 · **캐시가 빈 새 Runtime 에서 스레드부터**(GPT 56 #3)
    · 기준은 다른 Runtime 의 순차 결과 · 끝나고 새 Runtime 싱글턴 건별 상태가 그대로 · 경쟁 뒤 순차 재실행도 같다."""
    _need_data()
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    ref_rt = _runtime()
    inputs = {"화 09-29": _doc(), "일 09-27": _doc("2026-09-27"), "토 10-03": _doc("2026-10-03")}
    nf = _doc()
    nf["constraints"] = dict(nf.get("constraints") or {}, first_visit=False)
    inputs["화 09-29 재방문"] = nf
    cold = build_verifier(quiet=True)          # 후보 그래프(_cg)·요금망(_NETS)·판정 캐시가 빈 판정기
    before = _state(cold)
    keys = [list(inputs)[i % len(inputs)] for i in range(TOTAL)]
    got, errs, lock = {}, [], threading.Lock()
    start = threading.Barrier(THREADS, timeout=120)

    def job(i):
        k = keys[i]
        if i < THREADS:
            start.wait()                       # 첫 8건은 동시에 출발 — 캐시 최초 생성이 겹치게
        try:
            out = _plan(inputs[k], cold)
        except Exception as e:  # noqa: BLE001
            with lock:
                errs.append(f"{k}: {type(e).__name__}: {e}")
            return
        with lock:
            got.setdefault(k, []).append(out)

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(job, range(TOTAL)))
    assert not errs, f"예외 {len(errs)} — {errs[:2]}"
    ref = {k: _plan(d, ref_rt) for k, d in inputs.items()}
    assert len(set(ref.values())) == len(inputs), "입력마다 결과가 달라야 섞임이 보인다(요일형·first_visit)"
    bad = [k for k, outs in got.items() for o in outs if o != ref[k]]
    assert sum(len(v) for v in got.values()) == TOTAL
    assert not bad, f"{TOTAL}건 중 다른 Runtime 순차와 다름 {len(bad)} — {bad[:3]}"
    assert _singleton_clean(cold, before), "plan() 이 싱글턴의 건별 상태를 건드렸다 — 어딘가 복사본을 안 쓴다"
    after = {k: _plan(d, cold) for k, d in inputs.items()}
    assert after == ref, "경쟁 뒤 순차 재실행이 기준과 다르다(공유 캐시 오염)"


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
    print(f"[plan56] {n - fails - skips}/{n} 통과 · SKIP {skips} · 실패 {fails}")
    sys.exit(1 if fails else 0)
