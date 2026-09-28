# final_project_cs/tests/unit/travel/mobility/test_plan_estimate_v1.py — 48번 방 · 계획용 이동 추정(P1) 시험
# 실행: 저장소 루트에서
#   $env:PYTHONPATH = "final_project_cs"
#   python final_project_cs/tests/unit/travel/mobility/test_plan_estimate_v1.py      # 단위 + (시간표가 있으면) 데이터 축
#   python -m pytest tests/unit/travel/mobility/test_plan_estimate_v1.py
# 실패하면 종료코드 1.
#
# 축
#   U  단위 — 날짜 유형(우리 달력) · 분위(최근순위) · 시간대 창. 데이터 없이 돈다
#   D  데이터 — 실제 시간표·버스 프로파일·혼잡도로 추정을 돌려 성질을 본다
#      · 범위 순서 p10 ≤ p50 ≤ p90 ≤ worst_max · 여유 키 0
#      · 독립 재판정 — 표본의 경로를 판정기에 **legs 케이스로 따로** 넣어 같은 소요가 나오는가
#      · 공휴일(추석)은 일요일과 같은 판(시간표·혼잡도 요일축) · 일요일엔 출퇴근 주의가 없다
#      · 버스 범위는 구간 프로파일 p10/p90 · 막차(경로 기준 + 뒤를 잇는 경로 · 버스 막차) · 첫차 · 판정기 상태 무변경
#      · 토요일 재차율 ≥100 셀은 출퇴근 주의가 아니다
#   G  GPT 대조 1차(2026-09-25 · 7건) 잠금 — 공유 판정기 무변경(동시 실행 중에도) · 막차 뒤 공백/꼬리 구분 · 1분 경계 ·
#      섞인 불가 이유는 단정 안 함 · 같은 분 다른 방향 혼잡도 · 버스 출퇴근 주의 평일만 · 창·간격 검증 · 범위 설명
#      + 기본 수단에서 자전거 라우터 호출 0(노트북 1시간 정지 원인)
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.modules.travel_ops.mobility.engine.plan_estimate import (  # noqa: E402
    Estimator, _cautions, day_info, estimate, pct, slot_window)

HOTEL = {"name": "명동 호텔", "lat": 37.5636, "lon": 126.9826}           # 32 예시와 같은 장소
SEONGSU = {"name": "성수 쇼룸", "lat": 37.5445, "lon": 127.056}
WEEKDAY, SUNDAY, CHUSEOK = "2026-09-29", "2026-09-27", "2026-09-25"
NO_MARGIN = {"margin_min", "slack_min", "buffer_min", "last_feasible_depart_min", "arrive_by_min"}

_RT, _CACHE = None, {}


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


def _skip_if_no_data():
    if _runtime() is None:
        try:
            import pytest
            pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
        except ImportError:
            raise _Skip()


def _est(frm, to, d, slot, **kw):
    key = (str(frm), str(to), d, slot, tuple(sorted((k, str(v)) for k, v in kw.items())))
    if key not in _CACHE:
        sm = []
        r = estimate(frm, to, d, slot, runtime=_runtime(), samples=sm, **kw)
        _CACHE[key] = (r, sm)
    return _CACHE[key]


def _keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from _keys(v)


# ── U 단위 ────────────────────────────────────────────────────────────────
def test_day_info():
    x = day_info(date(2026, 9, 25))
    assert (x["kind"], x["holiday_name"], x["timetable"], x["congestion"]) == ("공휴일", "추석", "holiday", "sunday")
    x = day_info(date(2026, 9, 26))
    assert (x["weekday"], x["kind"], x["congestion"]) == ("토", "공휴일", "sunday"), "토요일과 겹친 공휴일은 공휴일(혼잡도 sunday)"
    x = day_info(date(2026, 10, 10))
    assert (x["kind"], x["timetable"], x["congestion"]) == ("토요일", "holiday", "saturday")
    x = day_info(date(2026, 9, 29))
    assert (x["kind"], x["timetable"], x["congestion"], x["in_calendar"]) == ("평일", "weekday", "weekday", True)
    assert "holiday_name" not in x
    assert day_info(date(2028, 1, 3))["in_calendar"] is False, "공휴일 표가 덮지 않는 해는 표시한다"


def test_pct():
    xs = list(range(1, 11))
    assert (pct(xs, 0.1), pct(xs, 0.5), pct(xs, 0.9)) == (1, 5, 9), "최근순위 — ceil(q·n) 번째"
    assert pct([], 0.5) is None
    assert pct([7], 0.1) == pct([7], 0.9) == 7
    assert pct([3, None, 1], 0.5) == 1


def test_slot_window():
    assert slot_window("오전") == ("오전", (360, 720))
    assert slot_window("night") == ("밤", (1320, 1680)), "밤은 운행일 끝(28:00)까지"
    try:
        slot_window("새벽")
    except ValueError:
        pass
    else:
        raise AssertionError("모르는 시간대는 멈춘다")


# ── D 데이터 ─────────────────────────────────────────────────────────────
def test_order_and_no_margin():
    _skip_if_no_data()
    for args in [(HOTEL, SEONGSU, WEEKDAY, "오후"), ("서울역", "이태원", WEEKDAY, "오후"), ("사당", "강남", WEEKDAY, "오전")]:
        r, _ = _est(*args)
        e = r["eta"]
        assert r["verdict"] == "feasible"
        assert e["p10_min"] <= e["p50_min"] <= e["p90_min"] <= e["worst_max_min"], f"범위 순서 {args[:2]} {e}"
        assert not (set(_keys(r)) & NO_MARGIN), f"여유 키가 새었다: {set(_keys(r)) & NO_MARGIN}"
        assert r["window"]["n"] == len(range(*slot_window(args[3])[1], 5))


def test_recheck_independent():
    """표본의 경로를 legs 케이스로 판정기에 따로 넣는다 — 같은 출발 시각에서 같은 예정 소요가 나와야 한다."""
    _skip_if_no_data()
    rt = _runtime()
    r, sm = _est(HOTEL, SEONGSU, WEEKDAY, "오후")
    E = Estimator(rt)
    wl = rt._v._walk_limit({})
    wa, wb = E._point(HOTEL, wl)["walk"], E._point(SEONGSU, wl)["walk"]
    got = []
    for x in sm[::7]:
        o = rt._v.verify_case({"id": "re", "date": WEEKDAY, "depart_at": x["t"] + wa, "legs": x["legs"],
                               "no_alternatives": True}).out
        assert o["verdict"] == "feasible", x
        assert wa + o["eta_min"] + wb == x["eta"], f"{x['t']} 재판정 {wa + o['eta_min'] + wb} ≠ 표본 {x['eta']}"
        assert wa + o["eta_worst_min"] + wb == x["worst"]
        got.append(x["eta"])
    assert got, "재판정 표본이 없다"
    assert r["eta"]["p50_min"] == pct([x["eta"] for x in sm if x["choice"]], 0.5)


def test_holiday_is_sunday_board():
    """추석(금)은 시간표·혼잡도 모두 일요일 판 — 같은 경로·같은 창이면 일요일과 값이 같다."""
    _skip_if_no_data()
    a, _ = _est(HOTEL, SEONGSU, CHUSEOK, "저녁")
    b, _ = _est(HOTEL, SEONGSU, SUNDAY, "저녁")
    assert a["day"]["kind"] == "공휴일" and b["day"]["kind"] == "일요일"
    assert a["eta"] == b["eta"] and a["routes"] == b["routes"]


def test_commute_crowding_weekday_only():
    _skip_if_no_data()
    r, _ = _est("사당", "강남", WEEKDAY, "오전")
    cc = [c for c in r["cautions"] if c["code"] == "commute_crowding"]
    assert cc and cc[0]["pct"] >= 80 and cc[0]["station"] == "사당", r["cautions"]
    assert all(x["range"] == "timetable" for x in r["routes"]), "06:00 창 — 첫차 전 버스 표본이 섞이지 않는다"
    s, _ = _est("사당", "강남", SUNDAY, "오전")
    assert not [c for c in s["cautions"] if c["code"] == "commute_crowding"], "일요일엔 출퇴근 주의 없음"


def test_saturday_crowding_not_commute():
    """토요일 남태령 4호선 상행 10:30 재차율 104%(J-CONG-05 셀) — 표본엔 남지만 출퇴근 주의로는 안 올린다."""
    _skip_if_no_data()
    r, sm = _est("남태령", "사당", "2026-10-10", "오전")
    assert r["day"]["kind"] == "토요일" and r["day"]["congestion"] == "saturday"
    top = max((x["crowd"] for x in sm if x.get("crowd")), key=lambda c: c[0])
    assert top[0] >= 100 and top[3] == "남태령", top
    assert not [c for c in r["cautions"] if c["code"] == "commute_crowding"], r["cautions"]


def test_bus_range_profile():
    _skip_if_no_data()
    r, sm = _est("서울역", "이태원", WEEKDAY, "오후", modes=["bus"])
    ok = [x for x in sm if x["choice"]]
    assert ok and all(x["range"] == "bus_profile" for x in ok), {x["range"] for x in ok}
    assert all(x["lo"] <= x["eta"] <= x["hi"] for x in ok)
    assert any(x["lo"] < x["eta"] for x in ok) and any(x["hi"] > x["eta"] for x in ok), "버스 범위가 0 폭이다"
    e = r["eta"]
    assert e["p10_min"] < e["p50_min"] < e["p90_min"], e


def test_night_last_service():
    _skip_if_no_data()
    r, _ = _est(HOTEL, SEONGSU, WEEKDAY, "밤")
    ls = [c for c in r["cautions"] if c["code"] == "last_service"]
    assert ls and ls[0]["route"].startswith("02호선"), r["cautions"]
    assert ls[0]["carried_by"] and all(k.startswith("버스 N") for k in ls[0]["carried_by"]), "지하철 뒤를 심야버스가 잇는다"
    b, _ = _est("서울역", "이태원", WEEKDAY, "밤", modes=["bus"])
    ls = [c for c in b["cautions"] if c["code"] == "last_service"]
    assert ls and ls[0]["route"].startswith("버스 ") and ls[0]["none_from"], \
        "버스 막차 — multi 가 불가 버스 후보를 코드 없이 접어도 경계 재판정으로 잡는다"
    s, _ = _est(HOTEL, SEONGSU, WEEKDAY, "밤", modes=["subway"])
    ls = [c for c in s["cautions"] if c["code"] == "last_service"]
    assert ls and not ls[0]["carried_by"] and ls[0]["none_from"], "지하철만이면 그 뒤는 없다"


def test_first_service_window():
    _skip_if_no_data()
    rt = _runtime()
    sm = []
    r = Estimator(rt, modes=["subway"]).estimate(HOTEL, SEONGSU, WEEKDAY, "오전", window=(240, 360), samples=sm)
    fs = [c for c in r["cautions"] if c["code"] == "first_service"]
    assert fs and fs[0]["first_ok"] and not fs[0]["carried_by"], r["cautions"]
    assert r["window"]["from"] == "04:00"


def test_no_station():
    _skip_if_no_data()
    r, _ = _est({"name": "먼 곳", "lat": 37.20, "lon": 127.60}, SEONGSU, WEEKDAY, "오후")
    assert r["verdict"] == "no_data" and "지하철역" in r["reason"]


def test_verifier_untouched():
    """추정이 판정기 상태를 바꾸지 않는다 — 역산 스위치 복구 · 같은 케이스의 밖 판이 앞뒤로 같다."""
    _skip_if_no_data()
    v = _runtime()._v
    case = {"id": "u", "date": WEEKDAY, "depart_at": "14:00", "arrive_by": "14:40",
            "legs": [{"line": "02호선", "from": "을지로입구", "to": "성수"}]}
    before = v.verify_case(dict(case)).out
    _est(HOTEL, SEONGSU, WEEKDAY, "저녁")
    _CACHE.clear()
    _est(HOTEL, SEONGSU, WEEKDAY, "저녁")
    assert v.lfd_enabled is True
    assert v.verify_case(dict(case)).out == before



# ── G GPT 대조 1차 잠금 ───────────────────────────────────────────────────
class _FakeV:
    R = {"congestion": {"levels": {"혼잡": {"gte": 80}}}}


class _FakeEst:
    v = _FakeV()

    def __init__(self, slow=20):
        self.slow = slow

    def _bus_slow(self, info, d, tt_day, boards):
        return self.slow


def _row(t, choice=None, by=None, codes=(), **kw):
    return {"t": t, "choice": choice, "by": by or {}, "codes": list(codes), "near_last": False, **kw}


def _night_rows():
    """주 경로 S 가 22:05 까지 · 22:10 비고 · 22:15 N버스 · 22:20~22:25 비고(창 끝)."""
    S, N = "지하철 S", "버스 N1"
    return [_row(1320, S, {S: "ok"}), _row(1325, S, {S: "ok"}), _row(1330, None, {S: "after_last"}, ["after_last"]),
            _row(1335, N, {S: "after_last", N: "ok"}), _row(1340, None, {S: "after_last"}, ["after_last"]),
            _row(1345, None, {S: "after_last"}, ["after_last"])], S


def test_g2_none_from_tail_and_gaps():
    """GPT 2 — 중간 공백은 gaps, none_from 은 창 끝까지 이어지는 꼬리만."""
    rows, S = _night_rows()
    ok = [x for x in rows if x["choice"]]
    ok[:] = [x for x in ok if x["choice"] == S] * 2 + [x for x in ok if x["choice"] != S]      # S 가 주 경로
    probe = lambda r, t: ("feasible", None, "feasible") if t <= 1327 else ("infeasible", "after_last", "infeasible")
    c = [x for x in _cautions(rows, ok, 10, _FakeEst(), date(2026, 9, 29), "weekday", probe) if x["code"] == "last_service"][0]
    assert c["gaps"] == [["22:10", "22:10"]], c
    assert c["none_from"] == "22:20", c
    assert c["last_ok"] == "22:07" and c["last_ok_sample"] == "22:05", "GPT 3 — 경계는 1분 단위로 다시 판정"


def test_g2_gap_only_no_none_from():
    rows, S = _night_rows()
    rows[-2] = _row(1340, "버스 N1", {S: "after_last", "버스 N1": "ok"})
    rows[-1] = _row(1345, "버스 N1", {S: "after_last", "버스 N1": "ok"})
    ok = [x for x in rows if x["choice"] == S] * 3 + [x for x in rows if x["choice"] and x["choice"] != S]
    probe = lambda r, t: ("feasible", None, "feasible") if t <= 1325 else ("infeasible", "after_last", "infeasible")
    c = [x for x in _cautions(rows, ok, 10, _FakeEst(), date(2026, 9, 29), "weekday", probe) if x["code"] == "last_service"][0]
    assert c["none_from"] is None and c["gaps"] == [["22:10", "22:10"]], c
    assert "창 끝까지" not in c["text"]


def test_g3_mixed_codes_not_asserted():
    """GPT 3 — 성립 표본이 없어도 이유가 섞였으면 「모두 막차 이후」라 단정하지 않는다."""
    rows = [_row(1320, codes=["before_first"]), _row(1325, codes=["after_last"])]
    cs = _cautions(rows, [], 10, _FakeEst(), date(2026, 9, 29), "weekday", None)
    assert [c["code"] for c in cs] == ["no_service"] and cs[0]["codes"] == {"before_first": 1, "after_last": 1}, cs
    rows = [_row(1320, codes=["after_last"]), _row(1325, codes=["after_last", "after_last"])]
    cs = _cautions(rows, [], 10, _FakeEst(), date(2026, 9, 29), "weekday", None)
    assert cs[0]["code"] == "last_service" and cs[0]["route"] is None


def test_g5_bus_peak_weekday_only():
    """GPT 5 — 버스 출퇴근 주의(bus_peak)도 평일만."""
    bus = {"route": "421", "route_id": "R", "a": {}, "b": {}, "stops": []}
    rows = [_row(1020, "버스 421", {"버스 421": "ok"}, _bus=bus, board=1025)]
    wk = _cautions(rows, rows, 10, _FakeEst(20), date(2026, 9, 29), "weekday", None)
    hd = _cautions(rows, rows, 10, _FakeEst(20), date(2026, 9, 27), "holiday", None)
    assert [c["code"] for c in wk] == ["bus_peak"], wk
    assert not [c for c in hd if c["code"] == "bus_peak"], hd


def test_g4_dir_same_minute():
    """GPT 4 — 같은 분에 U·D 편성이 있으면 도착 분으로 가른다 · 그래도 안 갈리면 방향을 안 정한다(혼잡도 안 봄)."""
    class Dep:
        def __init__(self, m, d):
            self.min, self.dir = m, d

    class P:
        def __init__(self, path):
            self.path = path

    class Lo:
        def travel_min_on_path(self, line, path, to):
            return {"U": 12, "D": 5}[path]

    class V:
        lo = Lo()

        def __init__(self, deps):
            self.deps = deps

        def candidates(self, line, frm, to, day_type):
            return [(d, P(d.dir), False) for d in self.deps], {}, False

    e = object.__new__(Estimator)
    e.v = V([Dep(600, "U"), Dep(600, "D")])
    assert e._dir_of("02호선", "a", "b", "weekday", 600, 605) == "D", "선택 편성(도착 10:05)은 D"
    assert e._dir_of("02호선", "a", "b", "weekday", 600, 612) == "U"
    e.v = V([Dep(600, "U"), Dep(600, "D")])
    Lo.travel_min_on_path = lambda self, line, path, to: 5
    assert e._dir_of("02호선", "a", "b", "weekday", 600, 605) is None, "도착까지 같으면 방향 미정 — 혼잡도 생략"


def test_g6_window_step_validated():
    _skip_if_no_data()
    for kw in ({"step": -5}, {"step": 0}):
        try:
            Estimator(_runtime()).estimate(HOTEL, SEONGSU, WEEKDAY, "오후", **kw)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 간격이 통과했다 {kw}")
    for w in ((200, 300), (700, 700), (1600, 1700)):
        try:
            Estimator(_runtime()).estimate(HOTEL, SEONGSU, WEEKDAY, "오후", window=w)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 창이 통과했다 {w}")


def test_g1_shared_verifier_never_touched():
    """GPT 1 — 추정 중에도 공유 판정기의 역산 스위치가 한 번도 바뀌지 않는다(다른 스레드에서 지켜본다)."""
    _skip_if_no_data()
    import threading
    rt = _runtime()
    v = rt._v
    assert Estimator(rt).v is not v
    seen, done = set(), threading.Event()

    def run():
        try:
            Estimator(rt).estimate(HOTEL, SEONGSU, WEEKDAY, "저녁")
            Estimator(rt).estimate("서울역", "이태원", WEEKDAY, "저녁")
        finally:
            done.set()
    th = threading.Thread(target=run)
    th.start()
    while not done.is_set():
        seen.add(v.lfd_enabled)
        done.wait(0.001)
    th.join()
    assert seen == {True} and v.lfd_enabled is True, seen


def test_g7_basis_wording():
    _skip_if_no_data()
    r, _ = _est(HOTEL, SEONGSU, WEEKDAY, "오후")
    b = r["eta"]["basis"]
    assert "80% 예측구간" in b and "하한·상한 추정치의 분위" in b, b


def test_g8_no_bike_router_calls_by_default():
    """결정 8 — 기본 수단(지하철·버스·도보)에서는 자전거 후보를 안 만든다 → 라우터가 떠 있어도 경로 탐색 호출 0.
    (노트북 2026-09-25: GraphHopper 를 켜 둔 채 reg48 가 1시간 넘게 돌았다 — 표본마다 자전거 경로 탐색)"""
    _skip_if_no_data()
    rt = _runtime()
    v = rt._v

    class Spy:
        url, calls = "spy", 0

        def available(self):
            return True

        def route(self, *a, **k):
            Spy.calls += 1
            return None

    old = v.bike_router
    v.bike_router = Spy()
    try:
        Estimator(rt).estimate(HOTEL, SEONGSU, WEEKDAY, "오후", window=(720, 740))
        assert Spy.calls == 0, f"기본 수단에서 자전거 경로 탐색 {Spy.calls}회"
        assert v.bk is not None, "공유 판정기의 대여소 표는 그대로"
        Estimator(rt, modes=["subway", "bike"]).estimate(HOTEL, SEONGSU, WEEKDAY, "오후", window=(720, 740))
        assert Spy.calls > 0, "bike 를 달라고 하면 자전거 후보를 본다(시험이 무는지)"
    finally:
        v.bike_router = old


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
    print(f"[estimate] {n - fails - skips}/{n} 통과 · SKIP {skips} · 실패 {fails}")
    sys.exit(1 if fails else 0)
