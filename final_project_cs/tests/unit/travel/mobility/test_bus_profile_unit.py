# final_project_cs/tests/unit/travel/mobility/test_bus_profile_unit.py — 41 적용(v0.9) 버스 구간 프로파일 단위 시험 (합성 자료 · pytest 없이도 돈다)
# 실행(저장소 루트):  python final_project_cs/tests/unit/travel/mobility/test_bus_profile_unit.py
# 적용 GPT 대조 12 — 실제 자료로 만들기 어려운 반례를 합성 프로파일로 잠근다:
#   진입 시각대 · 요일형 한쪽 결측(부분) · 양끝 ID 불일치 · 중복 키 · 구간 끊김 · 대체 실패 · 시간 경계 역전 · min_days
import gzip, json, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
for _p in (REPO / "final_project_cs",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.bus_profile import BusSegProfile, worst_not_before_best, board_caps  # noqa: E402


def H(v, hour=None, n=30):
    """24시간 칸. hour 를 주면 그 시간만 v, 나머지는 None."""
    nn = [n if hour is None or h == hour else 0 for h in range(24)]
    vv = [v if hour is None or h == hour else None for h in range(24)]
    return {"n": nn, "p10": vv, "p50": vv, "p90": vv}


def seg(rid, fs, fid, tid, weekday=None, holiday=None):
    r = {"route_id": rid, "route_nm": rid, "from_id": fid, "to_id": tid, "from_seq": fs, "to_seq": fs + 1, "dist_m": 600}
    if weekday:
        r["weekday"] = weekday
    if holiday:
        r["holiday"] = holiday
    return r


def build(recs):
    f = tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False)
    f.close()
    with gzip.open(f.name, "wt", encoding="utf-8") as g:
        g.write(json.dumps({"_meta": True, "source_id": "synthetic", "dates": [20260601, 20260913]}) + "\n")
        for r in recs:
            g.write(json.dumps(r) + "\n")
    return BusSegProfile.load(f.name)


STOPS = [{"seq": i, "station_id": f"S{i}", "sect_dist_m": 600} for i in range(1, 6)]
ONE = lambda _t: ("weekday",)
TWO = lambda _t: ("weekday", "holiday")
NOFB = lambda _d: None
FB3 = lambda _d: 3.0     # 대체 모델: 구간당 3분


def test_entry_hour():
    # 17시 칸 600초(10분) · 18시 칸 60초(1분). 17:55 출발 → 첫 구간 17시(10분) → 18:05 → 둘째 구간 18시(1분)
    hh = {"n": [0] * 24, "p10": [None] * 24, "p50": [None] * 24, "p90": [None] * 24}
    hh["n"][17] = hh["n"][18] = 30
    for k in ("p10", "p50", "p90"):
        hh[k][17], hh[k][18] = 600, 60
    p = build([seg("R", 1, "S1", "S2", hh), seg("R", 2, "S2", "S3", hh)])
    w = p.walk("R", STOPS, 1, 3, 17 * 60 + 55, ONE, "p50", 5, NOFB)
    assert abs(w.minutes - 11) < 1e-6 and w.complete, w


def test_two_daytypes_max_and_half():
    p = build([seg("R", 1, "S1", "S2", H(120), H(300)),       # 두 칸 다 있음 → 늦은 쪽 5분
               seg("R", 2, "S2", "S3", H(120), None)])        # holiday 없음 → 남은 칸 2분 · 부분
    w = p.walk("R", STOPS, 1, 3, 600, TWO, "p50", 5, NOFB)
    assert abs(w.minutes - 7) < 1e-6, w
    assert w.used == 1 and w.half == 1 and w.edge == 2 and not w.complete, w


def test_id_mismatch_falls_back():
    p = build([seg("R", 1, "S1", "SX", H(120))])               # 도착 ID 가 정류장 표(S2)와 다르다
    w = p.walk("R", STOPS, 1, 2, 600, ONE, "p50", 5, FB3)
    assert w.mismatch == 1 and w.fb == 1 and abs(w.minutes - 3) < 1e-6 and not w.complete, w


def test_duplicate_key_stops_load():
    try:
        build([seg("R", 1, "S1", "S2", H(60)), seg("R", 1, "S1", "S2", H(90))])
    except ValueError:
        return
    raise AssertionError("중복 구간을 조용히 덮어썼다")


def test_gap_in_stops_not_reached():
    p = build([seg("R", 1, "S1", "S2", H(60))])
    stops = [STOPS[0], STOPS[1], {"seq": 9, "station_id": "S9"}]   # b=3 이 표에 없다
    w = p.walk("R", stops, 1, 3, 600, ONE, "p50", 5, FB3)
    assert w.minutes is None, w


def test_fallback_failure_reports_seq():
    p = build([seg("R", 1, "S1", "S2", H(60))])                 # 2→3 구간 없음 · 대체도 못 함
    w = p.walk("R", STOPS, 1, 3, 600, ONE, "p50", 5, NOFB)
    assert w.minutes is None and w.fail_seq == 2 and w.used == 1, w


def test_min_days():
    p = build([seg("R", 1, "S1", "S2", H(60, n=4))])            # 4일뿐 → min_days 5 미만 → 대체
    w = p.walk("R", STOPS, 1, 2, 600, ONE, "p50", 5, FB3)
    assert w.fb == 1 and abs(w.minutes - 3) < 1e-6, w


def test_worst_not_before_best_inversion():
    # 적용 GPT 5 의 예 — best 17:59 진입 20분(도착 18:19) · worst 18:00 진입 5분(도착 18:05) → worst 를 19분으로
    ride, adj = worst_not_before_best(17 * 60 + 59, 20, 18 * 60, 5)
    assert adj and abs(ride - 19) < 1e-6, (ride, adj)
    ride, adj = worst_not_before_best(600, 10, 605, 12)          # 역전 아님 — 그대로
    assert not adj and ride == 12


# ── 적용 2차 GPT 대조(2026-09-25) 반례 ──
def test_start_stop_missing():
    # 정류장 표 [1,3,4] · 요청 2→4 — 3→4 만 세고 끝내면 안 된다(2차 GPT 8)
    p = build([seg("R", 3, "S3", "S4", H(60))])
    stops = [STOPS[0], STOPS[2], STOPS[3]]
    w = p.walk("R", stops, 2, 4, 600, ONE, "p50", 5, FB3)
    assert w.minutes is None and w.fail_seq == 2, w


def test_out_of_range_48h():
    p = build([seg("R", 1, "S1", "S2", H(60))])
    w = p.walk("R", STOPS, 1, 2, 2 * 1440 + 5, ONE, "p50", 5, FB3)
    assert w.minutes is None and w.out_of_range, w


def test_missing_ids_stop_load():
    r = seg("R", 1, "S1", "S2", H(60))
    r.pop("to_id")
    try:
        build([r])
    except ValueError:
        return
    raise AssertionError("식별자 없는 구간을 받아들였다")


def test_quantile_order_violation_stop_load():
    bad = H(60)
    bad["p90"] = [30] * 24          # p90 < p50
    try:
        build([seg("R", 1, "S1", "S2", bad)])
    except ValueError:
        return
    raise AssertionError("p50 > p90 칸을 받아들였다")


def test_board_caps_inversion():
    # 2차 GPT 3 의 예 — 막차 기점 17:50 · 첫 구간 p50 5분·p90 10분 · 둘째 구간 17시 20분 / 18시 5분(p50 = p90)
    #   p50 누적: 17:55 진입 → 17시 20분 → 18:15 · p90 누적: 18:00 진입 → 18시 5분 → 18:05 → worst 상한은 18:15 로
    first = {"n": [30] * 24, "p10": [300] * 24, "p50": [300] * 24, "p90": [600] * 24}
    second = {"n": [30] * 24, "p10": [1200] * 24, "p50": [1200] * 24, "p90": [1200] * 24}
    for k in ("p10", "p50", "p90"):
        second[k][18] = 300
    p = build([seg("R", 1, "S1", "S2", first), seg("R", 2, "S2", "S3", second)])
    cb, cw = board_caps(p, "R", STOPS, 3, 17 * 60 + 50, ONE, 5)
    assert cb == 18 * 60 + 15 and cw == cb, (cb, cw)


def test_board_caps_origin_without_profile():
    # 기점 승차는 프로파일이 없어도 막차 시각 그대로(2차 GPT 7) · 중간 정류장은 모름
    assert board_caps(None, "R", STOPS, 1, 1340, ONE, 5) == (1340, 1340)
    assert board_caps(None, "R", STOPS, 3, 1340, ONE, 5) == (None, None)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"  FAIL {name}: {e!r}")
    print(f"실패 {fails}")
    sys.exit(1 if fails else 0)
