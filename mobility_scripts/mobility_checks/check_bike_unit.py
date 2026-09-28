# -*- coding: utf-8 -*-
"""자전거(따릉이) 판정 — 라우터가 있어야만 보이는 가지를 **합성 픽스처**로 한 번씩 밟는다 (v0.7 · 22번 방).

회귀(bike_legs_v1.json)는 라우터 없이 돌아 승차 소요·초과 경고·요금·도착 시각 가지를 못 본다.
여기서는 거리·시간을 **지어낸 값**으로 넣어(실측 아님 — source_id 에 synthetic 표기) 그 가지가 실제로 우는지 본다.
  ① 승차 51분 → MOB_W_BIKE_OVERTIME · 도착 = 출발 + 도보 + 3 + 51 + 3 + 도보
  ② 승차 20분 → 경고 없음 · 이용권 1h 1,000원
  ③ fare(): 65분 → 초과 5분 200원 · 74분 → 초과 14분 → 3단위 600원
  ④ 실시간 0대 → 다음 대여소 · 전부 0 → 불가
  ⑤ 규칙을 끄면(enabled=false) 근거없음 — 켜기 전 상태가 이것이었다
실행: python mobility_scripts/mobility_checks/check_bike_unit.py
"""
import json, sys, copy
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.verify_time import Verifier      # noqa: E402
from app.modules.travel_ops.mobility.engine.geo import StationCoords        # noqa: E402
from app.modules.travel_ops.mobility.engine.bike import (BikeStations, BikeLive,  # noqa: E402
                                                        BikeRouter, fare)

fails = []


def ck(cond, msg):
    print(("  OK  " if cond else "  FAIL") + "  " + msg)
    if not cond:
        fails.append(msg)


def main():
    rules = json.loads((REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "rules_v0.3.json").read_text(encoding="utf-8"))
    holidays = set(json.loads((REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "holidays_2026_2027.json").read_text(encoding="utf-8"))["holidays"])
    try:
        from app.modules.travel_ops.mobility.engine.paths import PROCESSED   # 엔진 paths 로 읽는다(41 · 67 뒤 mobility_scripts 로도 되지만 엔진 값이 정본)
        sc = StationCoords.load(PROCESSED / "mobility" / "station_coords.json")
        bk = BikeStations.load(PROCESSED / "mobility" / "bike_stations_v1.jsonl")
    except Exception as e:          # noqa: BLE001
        raise SystemExit(f"입력을 못 읽었다: {e}")
    if sc is None or bk is None:
        raise SystemExit("station_coords.json / bike_stations_v1.jsonl 이 없다")

    ja, sg = sc.by_name["잠실"], sc.by_name["성수"]
    A = [s for _d, s in bk.stations_near(ja["lat"], ja["lng"], 300) if BikeStations.qr_ok(s)][0]   # 잠실역 6번출구
    B = bk.stations_near(sg["lat"], sg["lng"], 300)[0][1]                                            # 유원지식산업센터 앞
    K = BikeRouter.key

    def fx(ride_s):
        # ★ 합성값 — 실측 아님. 도보 200m/50m, 승차 ride_s 초
        return {K("foot", ja["lat"], ja["lng"], A["lat"], A["lon"]): {"distance_m": 200, "time_s": 150, "source_id": "synthetic"},
                K("foot", B["lat"], B["lon"], sg["lat"], sg["lng"]): {"distance_m": 50, "time_s": 40, "source_id": "synthetic"},
                K("bike", A["lat"], A["lon"], B["lat"], B["lon"]): {"distance_m": 7000, "time_s": ride_s, "source_id": "synthetic"}}

    def run(ride_s, live_counts=None, R=rules):
        v = Verifier(None, None, R, holidays, None, None, sc, None, bk=bk,
                     bike_live=BikeLive.from_fixture({"checked_at": "synthetic", "counts": live_counts or {A["stationId"]: 3}}),
                     bike_router=BikeRouter(None, fx(ride_s), "synthetic"))
        return v.verify_leg_bike(0, {"mode": "bike", "from": "잠실", "to": "성수"}, 14 * 60, "weekday", {})

    print("[1] 승차 51분 — 초과 경고 · 도착 시각")
    r = run(51 * 60)
    codes = [w["code"] for w in r.warnings]
    ck(r.verdict == "feasible" and r.grade == "추정", f"성립·추정 (실제 {r.verdict}·{r.grade})")
    ck("MOB_W_BIKE_OVERTIME" in codes, f"MOB_W_BIKE_OVERTIME 이 붙는다 — {codes}")
    # 도보 200m/1.04/60 = 3.2 → 4분 · 50m → 1분 · 3 + 51 + 3
    ck(r.arrive_min == 14 * 60 + 4 + 3 + 51 + 3 + 1, f"도착 = 14:00 + 4+3+51+3+1 = 15:02 (실제 {r.arrive_min})")
    ck(r.walk_min == 5, f"구간 안 도보 5분 (실제 {r.walk_min})")
    ck("이용권 1h 1,000원" in r.reason, f"요금 문구 — {r.reason}")

    print("[2] 승차 20분 — 경고 없음")
    r = run(20 * 60)
    codes = [w["code"] for w in r.warnings]
    ck("MOB_W_BIKE_OVERTIME" not in codes, f"초과 경고 없음 — {codes}")
    ck("MOB_W_BIKE_LIVE_UNKNOWN" not in codes, "실시간 픽스처가 있으면 LIVE_UNKNOWN 없음")
    ck(r.ride_min == 20.0 and r.arrive_min == 14 * 60 + 4 + 3 + 20 + 3 + 1, f"승차 20분 · 도착 14:31 (실제 {r.arrive_min})")

    print("[3] fare()")
    F = rules["bike"]["ddareungi"]["fare"]
    f = fare(F, 65)
    ck(f["pass"] == "1h" and f["overtime_min"] == 5 and f["overtime_won"] == 200 and f["total_won"] == 1200,
       f"65분 → 1h + 초과 5분 200원 = 1,200 (실제 {f})")
    f = fare(F, 74)
    ck(f["overtime_min"] == 14 and f["overtime_won"] == 600, f"74분 → 초과 14분 → 3단위 600원 (실제 {f})")
    f = fare(F, 60)
    ck(f["overtime_won"] == 0, f"60분 → 초과 없음 (실제 {f})")

    print("[4] 실시간 0대")
    nxt = [s for _d, s in bk.stations_near(ja["lat"], ja["lng"], 300) if BikeStations.qr_ok(s)][1]
    r = run(20 * 60, {A["stationId"]: 0, nxt["stationId"]: 2})
    ck(r.verdict == "feasible" and nxt["name"] in r.reason, f"첫 대여소 0대 → 다음({nxt['name']})으로 — {r.reason[:60]}")
    r = run(20 * 60, {A["stationId"]: 0, nxt["stationId"]: 0})
    ck(r.verdict == "infeasible" and "0대" in r.reason, f"전부 0대 → 불가 — {r.reason}")

    print("[5] 규칙을 끄면 근거없음")
    R2 = copy.deepcopy(rules)
    R2["bike"]["ddareungi"]["enabled"] = False
    r = run(20 * 60, R=R2)
    ck(r.verdict == "unknown" and r.grade == "근거없음", f"enabled=false → unknown·근거없음 (실제 {r.verdict}·{r.grade})")

    print("[6] 응답을 버리는가 — BikeLive.get 결과에 개수·시각·source_id 만 있다")
    L = BikeLive.from_fixture({"checked_at": "t", "counts": {"ST-1": 4}}).get("ST-1")
    ck(set(L) == {"available", "checked_at", "source_id"}, f"키 {sorted(L)}")
    Rr = BikeRouter(None, fx(600), "synthetic").route("bike", A["lat"], A["lon"], B["lat"], B["lon"])
    ck(set(Rr) == {"distance_m", "time_s", "basis", "source_id"}, f"라우터 결과 키 {sorted(Rr)} — 형상 없음")

    print(f"\n실패 {len(fails)} 건")
    return 1 if fails else 0


def test_bike_unit():          # 67: pytest 수집용 — 좌표표·대여소 표(실데이터)가 있어야 돈다 · 없는 기기는 SKIP
    from app.modules.travel_ops.mobility.engine.paths import PROCESSED
    need = [PROCESSED / "mobility" / "station_coords.json", PROCESSED / "mobility" / "bike_stations_v1.jsonl"]
    if not all(p.exists() for p in need):
        import pytest
        pytest.skip("data not present (station_coords.json / bike_stations_v1.jsonl)")
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
