# -*- coding: utf-8 -*-
"""판정기를 프로세스당 하나 세운다. 이동 모듈을 부르는 쪽(`plan()` · 코어 배선)의 유일한 무거운 자리.

  from app.modules.travel_ops.mobility.engine.runtime import build_verifier
  v = build_verifier()          # 한 번. 약 33초 · 상주 약 91MB

왜 전부 올리나 (2026-09-14 실측, `mobility_scripts/probe_timetable_load.py`)
  Timetable.load(path, wanted) 의 wanted 는 **케이스 파일에서 뽑은 (노선,역) 집합**이다.
  배치에는 맞지만 서버는 요청마다 어느 역이 올지 미리 모른다. 셋을 재 봤다.

    A 요청마다 재로드(역 6개)   4,297행 ·  3.38초        ← 요청당 3.4초는 못 쓴다
    B 전부 상주               444,915행 · 32.78초 · 91MB  ← 채택
    C 파일 한 번 훑기(하한)    463,326행 ·  2.22초

  B 의 상주가 91MB 로 작다 — 186MB JSONL 이 메모리에서 오히려 준다(키 문자열이 행마다 반복되지 않는다).
  **기동 33초가 유일한 대가**이고 프로세스당 한 번이다.

  ★ 본안은 여전히 PG 질의다. Timetable 클래스 주석("DB 전환 뒤에는 이 클래스가 조회로 바뀐다")과
    ix_timetable_lookup 인덱스가 그 설계다. 다만 **B 가 되므로 어댑터는 저장소 전환을 기다리지 않는다.**

캐시를 두지 않는다
  기동 33초를 pickle 로 줄일 수 있지만 두지 않는다. **낡은 시간표를 조용히 내주는 캐시**는
  이 모듈이 막으려는 바로 그 종류의 결함이다. 33초는 프로세스당 한 번이고, 그 값에 비해 위험이 크다.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

PKG = Path(__file__).resolve().parent
_LOCK = threading.Lock()
_SINGLETON = None

# ★ 31번 방(2026-09-21) — 판정기가 같은 패키지 안으로 들어왔다.
#   종전에는 scripts/verify_time.py 를 spec_from_file_location 으로 **파일째** 들었다
#   ("CLI 스크립트라 패키지가 아니다"). 이제 형제 모듈이라 그 트릭이 필요 없다.
#   그 트릭을 남겨 두면 파일 로드된 모듈 안에서 상대 import 가 죽는다.
from . import verify_time as _vt                            # noqa: E402


def _load_verify_time():
    return _vt


def default_paths():
    from .paths import PROCESSED                            # noqa: E402
    p = PROCESSED / "mobility"
    c = PKG / "rules"
    return {"timetable": p / "timetable_v1.jsonl",
            "order": p / "line_station_order_v1.json",
            "transfer_walk": p / "transfer_walk_v1.json",
            "bus_route": p / "bus_route_v1.jsonl",
            "bus_stops": p / "bus_stops_v1.jsonl",
            "station_coords": p / "station_coords.json",
            "station_exits": p / "station_exits_v1.json",      # 19번 방 — 지하철↔버스 환승(OSM 출구, 추정)
            "bike_stations": p / "bike_stations_v1.jsonl",     # 22번 방 — 따릉이 운영 대여소(없어도 돈다 · 자전거 근거없음)
            "bus_profile": p / "bus_seg_profile_v1.jsonl.gz",  # 41번 방 — 버스 구간 통행시간(없어도 돈다 · 표정속도 모델)
            "meta": p / "timetable_v1_meta.json",
            "rules": c / "rules_v0.3.json",
            "holidays": c / "holidays_2026_2027.json"}


class Runtime:
    """판정기 하나 + 어댑터가 basis 로 쓸 출처 값."""

    def __init__(self, verifier, *, timetable_built_at, rules_version, stats):
        self._v = verifier
        self.timetable_built_at = timetable_built_at
        self.rules_version = rules_version
        self.stats = stats

    def verify_case(self, case):
        # 56 ① — 건마다 얕은 복사본에서 돈다. 판정기가 건마다 재할당하는 상태(_case_date · disr · _leg_cache ·
        #   lfd_capped)를 여러 스레드가 같은 객체에서 섞지 않게(plan.Planner · plan_estimate 와 같은 방식).
        return copy.copy(self._v).verify_case(case)


def build_verifier(*, paths=None, wanted=None, quiet=False):
    """전부 올려 Runtime 을 만든다. 약 33초.

    paths  : 경로 일부만 바꿔 끼울 수 있다(시험용)
    wanted : None 이면 전부. 배치에서만 집합을 준다
    """
    vt = _load_verify_time()
    P = default_paths()
    if paths:
        P = dict(P, **{k: Path(v) for k, v in paths.items()})

    # station_exits 는 없어도 돈다(역 좌표로 대신) — 단 경고를 찍는다
    missing = [k for k, v in P.items() if k not in ("meta", "station_exits", "bike_stations", "bus_profile") and not Path(v).exists()]
    if missing:
        raise RuntimeError(f"판정기 입력이 없다: {missing}")

    rules = json.loads(Path(P["rules"]).read_text(encoding="utf-8"))
    holidays = set(json.loads(Path(P["holidays"]).read_text(encoding="utf-8"))["holidays"])
    lo = vt.LineOrder.load(str(P["order"]))
    tt = vt.Timetable.load(str(P["timetable"]), wanted)
    tw = vt.TransferWalk.load(str(P["transfer_walk"]),
                              rules["measured_baseline"]["kakao_walk_speed_mps"]["value"])
    bus = vt.BusRoutes.load(str(P["bus_route"]), str(P["bus_stops"]))
    sc = vt.StationCoords.load(str(P["station_coords"]))
    ex = vt.StationExits.load(str(P["station_exits"]))
    # 따릉이(v0.7 · 22번 방). 실시간 조회는 .env SEOUL_OPENAPI_KEY, 라우터는 .env MOBILITY_GH_URL(21번과 같은 주소) — 둘 다 없으면 근거없음으로 낸다.
    bk = vt.BikeStations.load(str(P["bike_stations"]))
    import os
    bike_live = vt.BikeLive.from_env()
    # 라우터는 21번 car.py 의 make_router 로 — 23 이 CarService 를 끼울 때 같은 객체를 나눠 쓴다.
    gh = os.environ.get("MOBILITY_GH_URL") or ((rules.get("car") or {}).get("graphhopper") or {}).get("url", {}).get("value")
    bike_router = None
    if gh and str(gh).startswith("http"):
        from .car import make_router
        rt = make_router(gh)
        if rt.info():
            bike_router = vt.BikeRouter(rt, {}, (rules.get("bike") or {}).get("pbf_date") or "2026-09-18")
    # 혼잡도(v0.8 · 39번 방 · @ 부품) — 파일이 없으면 가산 없음(근거없음). 판정기 CLI 와 같은 두 파일.
    cg_dir = Path(P["timetable"]).parent
    cg_data = vt.Congestion.load([cg_dir / "congestion_v1.jsonl", cg_dir / "congestion_line9_v1.jsonl"], wanted)
    # 버스 구간 통행시간 프로파일(v0.9 · 41번 방) — 파일이 없으면 종전 모델(거리 ÷ 표정속도)
    bus_prof = vt.BusSegProfile.load(P["bus_profile"])
    verifier = vt.Verifier(tt, lo, rules, holidays, tw, bus, sc, ex, bk=bk, bike_live=bike_live, bike_router=bike_router,
                           cg_data=cg_data, bus_prof=bus_prof)

    # ── 시간표 '판'. meta 의 built_at 이 있으면 그것, 없으면 행의 수집일.
    #    둘은 다른 값이다 — 어느 쪽인지 접두어로 남긴다. 판정 이력이 "어느 판으로 냈는지"를 잃으면 안 된다.
    built = None
    meta = Path(P["meta"])
    if meta.exists():
        try:
            built = json.loads(meta.read_text(encoding="utf-8")).get("built_at")
        except (ValueError, OSError):
            built = None
    built_at = f"built:{built}" if built else f"fetched:{tt.fetched_at}"

    stats = {"timetable_rows": tt.rows, "timetable_stations": len(tt.stations),
             "skipped_no_dep": tt.skipped_no_dep,
             "bus_routes": len(bus.by_id) if bus else 0,
             "transfer_pairs": len(tw.pairs) if tw else 0,
             "station_coords": len(sc.by_key) if sc else 0,
             "station_exits": sum(len(x) for x in ex.exits.values()) if ex else 0,
             "bike_stations": len(bk.rows) if bk else 0,
             "bike_live": bool(bike_live), "bike_router": bool(bike_router)}
    if not quiet:
        # ★ 출발없음을 같이 찍는다(2026-09-14). 수집 행 수(463,326)와 올라간 행 수가 달라서,
        #   이 줄만 보면 "46만이라더니 44만이네"가 된다. 차이는 출발 시각이 '000000'(출발 없음)인 행이다.
        print(f"[mobility] 시간표 {tt.rows:,}행(+출발없음 {tt.skipped_no_dep:,} = 수집 "
              f"{tt.rows + tt.skipped_no_dep:,}) · 역 {len(tt.stations)} · 판 {built_at} · "
              f"규칙 {rules['rules_version']} · 버스 {stats['bus_routes']}노선")
    if tw is None:
        print("[mobility] ! 환승 거리표가 없다 — 환승 도보는 근거없음으로 낸다")
    if ex is None:
        print("[mobility] ! 역 출구표(station_exits_v1.json)가 없다 — 정류장↔역 환승은 역 좌표로 잰다")
    if bk is None:
        print("[mobility] ! 따릉이 대여소(bike_stations_v1.jsonl)가 없다 — 자전거는 근거없음으로 낸다")
    elif not quiet:
        print(f"[mobility] 따릉이 {len(bk.rows):,}곳 · 실시간 {'on' if bike_live else 'off(근거없음)'} · "
              f"라우터 {'on' if bike_router else 'off(소요 근거없음)'}")

    return Runtime(verifier, timetable_built_at=built_at,
                   rules_version=rules["rules_version"], stats=stats)


def get_verifier(**kw):
    """프로세스당 하나. 여러 번 불러도 한 번만 올린다."""
    global _SINGLETON
    if _SINGLETON is None:
        with _LOCK:
            if _SINGLETON is None:
                _SINGLETON = build_verifier(**kw)
    return _SINGLETON
