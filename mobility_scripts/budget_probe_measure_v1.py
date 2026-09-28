# -*- coding: utf-8 -*-
"""
budget_probe_measure_v1.py — 42번 방 후속: 대표 시나리오 6구간을 우리 엔진(GraphHopper + TOPIS 프로파일)으로 실측.

판정기·규칙은 안 만진다 — mobility_engine.car 의 CarGraph·GraphHopperClient·CarService 를 읽기만 한다.
좌표는 공공데이터 역 좌표(station_coords.json, KRIC)만 쓴다. 경로(좌표열·edge)는 저장하지 않고
거리·소요·택시요금(엔진 병산)·커버율 숫자만 JSON 으로 남긴다.

실행(저장소 루트, GraphHopper 서버가 떠 있는 상태 · PYTHONPATH 불필요):
    python mobility_scripts/budget_probe_measure_v1.py
    python mobility_scripts/budget_probe_v1.py --legs <위에서 나온 json 경로>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import importlib.util

# 엔진 패키지만 따로 올린다 — `app.modules.travel_ops` 로 import 하면 travel_ops/__init__.py 가
# 여섯 팀 모듈(openai 등)을 먼저 불러온다(mobility/engine/__init__.py 에 적힌 대가). 분석 스크립트라
# 팀 의존 없이 엔진만 읽는다. 엔진 코드는 그대로다(상대 import 만 쓰는 자기완결 패키지).
# ★ 69(2026-09-28) 이 별칭 로드는 `car`·`paths` 까지만 된다 — `options`(→ `...route_uses` 팀 공용)·`plan` 은
#   「beyond top-level package」로 실패한다. 같은 프로세스에서 정식 경로(`app.modules.travel_ops.mobility.engine`)와
#   이 별칭을 섞으면 모듈·클래스가 두 벌이 되므로 섞지 않는다(GPT 대조 #7).
_ENGINE = Path(__file__).resolve().parents[1] / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine"
_spec = importlib.util.spec_from_file_location("mobility_engine", _ENGINE / "__init__.py",
                                               submodule_search_locations=[str(_ENGINE)])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["mobility_engine"] = _pkg
_spec.loader.exec_module(_pkg)

from mobility_engine.car import CarGraph, CarService, GraphHopperClient, RouterDown  # noqa: E402
from mobility_engine.paths import PROCESSED, RULES_DIR  # noqa: E402

# 대표 시나리오 — 명동 숙박 2박3일. 날짜는 연휴를 피한 금·토·일(2026-10-16~18).
# (이름, 출발역, 도착역, 출발 시각, 공항구간)
SCENARIO = [
    ("Day1 인천공항T1→명동", "인천공항1터미널", "명동", "2026-10-16T18:00", True),
    ("Day2 명동→경복궁", "명동", "경복궁", "2026-10-17T10:00", False),
    ("Day2 경복궁→홍대입구", "경복궁", "홍대입구", "2026-10-17T13:00", False),
    ("Day2 홍대입구→강남", "홍대입구", "강남", "2026-10-17T16:00", False),
    ("Day2 강남→명동", "강남", "명동", "2026-10-17T19:00", False),
    ("Day3 명동→인천공항T1", "명동", "인천공항1터미널", "2026-10-18T10:00", True),
]


def station_xy(stations: dict, name: str) -> tuple[float, float]:
    for v in stations.values():
        if v.get("station_nm") == name:
            return (v["lng"], v["lat"])
    raise KeyError(f"station_coords.json 에 역이 없다: {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gh-url", default="http://localhost:8989")
    ap.add_argument("--out", default=str(PROCESSED / "mobility" / "budget_probe" / "budget_legs_measured_v1.json"))
    a = ap.parse_args()

    rules = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
    holidays = set(json.loads((RULES_DIR / "holidays_2026_2027.json").read_text(encoding="utf-8"))["holidays"])
    stations = json.loads((PROCESSED / "mobility" / "station_coords.json").read_text(encoding="utf-8"))["stations"]

    graph = CarGraph.load(holidays=holidays)
    if graph is None:
        print("그래프 자료가 없다 — DATA_DIR/travel/processed/mobility/graph 확인", file=sys.stderr)
        return 1
    router = GraphHopperClient(a.gh_url)
    if router.info() is None:
        print(f"GraphHopper 가 안 떠 있다: {a.gh_url} — gh_build.ps1 먼저", file=sys.stderr)
        return 1
    svc = CarService(graph, router, rules)

    legs = []
    for name, s_nm, e_nm, depart, is_airport in SCENARIO:
        s, e = station_xy(stations, s_nm), station_xy(stations, e_nm)
        try:
            r = svc.leg(s, e, dt.datetime.fromisoformat(depart), taxi=True)
        except RouterDown as ex:
            print(f"[{name}] 라우터 실패: {ex}", file=sys.stderr)
            return 1
        row = {
            "name": name, "from": s_nm, "to": e_nm, "depart": depart, "is_airport": is_airport,
            "dist_km": round(r["distance_m"] / 1000, 2),
            "topis_min": round(r["topis_time_s"] / 60, 1),
            "gh_min": round(r["gh_time_s"] / 60, 1) if r.get("gh_time_s") else None,
            "day_type": r["day_type"],
            "taxi_fare_won": r["fare_won"], "taxi_meter_won": r["meter_won"], "toll_won": r["toll_won"],
            "slow_min": round(r["slow_s"] / 60, 1), "night_rate": r["night_rate"],
            "coverage_pct": r["coverage_pct"], "grade": r["grade"],
            "warn": [w[0] for w in r["warn"]],
        }
        legs.append(row)
        print(f"{name:22s} {row['dist_km']:6.2f} km  {row['topis_min']:5.1f} 분  택시 {row['taxi_fare_won']:,}원  "
              f"커버 topis {row['coverage_pct']['topis']}% class {row['coverage_pct']['class']}%")

    out = {"measured_at": dt.datetime.now().isoformat(timespec="seconds"),
           "gh_version": (router.info() or {}).get("version"),
           "rules_version": rules.get("rules_version") or rules.get("version"),
           "coord_source": "station_coords.json (kric_station_standard)",
           "note": "경로 좌표·edge 는 저장하지 않는다. 거리·소요·요금 숫자만.",
           "legs": legs}
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[저장] {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
