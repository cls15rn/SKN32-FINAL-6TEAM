# -*- coding: utf-8 -*-
"""판정기를 프로세스당 하나 세운다. 이동 모듈을 부르는 쪽(`plan()` · 코어 배선)의 유일한 무거운 자리.

  from app.modules.travel_ops.mobility.engine.runtime import build_verifier
  v = build_verifier()          # 한 번. 약 33초 · 상주 약 91MB

왜 전부 올리나 (2026-09-14 실측, 조사 스크립트 `probe_timetable_load.py` · 저장소 밖)
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
    # ☆`[73 후속 · 3-4]` 실 시간표는 gz 가 있으면 그것(75 데이터 git · 75 MB → 1.8 MB) · 없으면 텍스트. 판정기는 확장자로 연다.
    from .paths import timetable_file                        # noqa: E402
    return {"timetable": timetable_file(p),
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

    def __init__(self, verifier, *, timetable_built_at, rules_version, stats, source_mtimes=None, build_kw=None):
        self._v = verifier
        self.timetable_built_at = timetable_built_at
        self.rules_version = rules_version
        self.stats = stats
        # ☆`[2026-09-29 문제목록 #32]` 적재한 파일의 수정 시각 — get_verifier 가 바뀐 것을 알아채 다시 올린다
        self.source_mtimes = dict(source_mtimes or {})
        self.build_kw = dict(build_kw or {})

    @property
    def timetable_stale(self):
        return bool(self.stats.get("timetable_stale"))

    def changed_sources(self):
        """적재 뒤 바뀐(또는 사라진) 입력 파일 이름들."""
        out = []
        for k, (path, mt) in self.source_mtimes.items():
            p = Path(path)
            now = p.stat().st_mtime if p.exists() else None
            if now != mt:
                out.append(k)
        return out

    def verify_case(self, case):
        # 56 ① — 건마다 얕은 복사본에서 돈다. 판정기가 건마다 재할당하는 상태(_case_date · disr · _leg_cache ·
        #   lfd_capped)를 여러 스레드가 같은 객체에서 섞지 않게(plan.Planner · plan_estimate 와 같은 방식).
        return copy.copy(self._v).verify_case(case)


def build_verifier(*, paths=None, wanted=None, quiet=False, data_dir=None, gh_url=None, seoul_key=None,
                   guardrails_path=None, road_graph=None):
    """전부 올려 Runtime 을 만든다. 약 33초.

    paths  : 경로 일부만 바꿔 끼울 수 있다(시험용)
    wanted : None 이면 전부. 배치에서만 집합을 준다
    ☆`[2026-09-29 문제목록 #48]` 서버는 설정 값을 넘긴다 — data_dir(자료 폴더) · gh_url(자전거 라우터, "" 이면 끔) ·
      seoul_key(따릉이 실시간, "" 이면 끔) · guardrails_path(정책 수치 파일). None 이면 명령줄 관례(환경변수·.env)를 쓴다.
    ☆77-2(2026-10-03) road_graph — 택시·자동차 소요의 **서버 없는 파이썬 도로 라우터**. None 이면 환경변수
      MOBILITY_ROAD_GRAPH → "auto"(자료 폴더 mobility/road_graph_v1 이 있으면 켬) · "none"/"" 끔 · 폴더 경로.
      앞 판은 이 적재 경로에서 CarService 를 만들지 않아 택시 대안이 늘 근거없음이었다. 순서는 파이썬 라우터 →
      (gh_url 이 http 고 응답하면) GH → 근거없음. 도로 그래프 적재는 첫 택시 질의 때 한 번(약 13초 · +0.4 GB).
    """
    from . import paths as _paths
    if data_dir:
        _paths.configure(data_dir)
    elif _paths.SOURCE == "disabled":
        raise RuntimeError("이동 계산기가 꺼져 있다(서버 설정 mobility_data_dir 비움) — 판정기를 올리지 않는다")
    elif _paths.SOURCE == "unset":
        _paths.load_cli_env()               # 명령줄·시험 — 서버는 기동 때 configure 로 정한다
    if guardrails_path:
        from .guardrails import use
        use(guardrails_path)
    vt = _load_verify_time()
    P = default_paths()
    if paths:
        P = dict(P, **{k: Path(v) for k, v in paths.items()})

    # station_exits 는 없어도 돈다(역 좌표로 대신) — 단 경고를 찍는다
    missing = [k for k, v in P.items() if k not in ("meta", "station_exits", "bike_stations", "bus_profile") and not Path(v).exists()]
    if missing:
        # #24 — 어디를 봤는지 같이 말한다(자료 폴더 · 그 값이 어디서 왔나). 배포 확인은 datacheck.py
        raise RuntimeError(f"판정기 입력이 없다: {missing} (자료 폴더 {_paths.DATA_DIR} · 출처 {_paths.SOURCE}) — "
                           f"python -m app.modules.travel_ops.mobility.engine.datacheck 로 확인")
    if not paths:
        from .datacheck import check as _datacheck
        dc = _datacheck(verify_hash=False)       # 판 명세가 있으면 크기까지 맞춘다(해시는 기동 확인이 본다)
        if dc["mismatched"]:
            raise RuntimeError(f"이동 자료가 판 명세와 다르다: {dc['mismatched']} (명세 {dc['manifest']})")

    rules = json.loads(Path(P["rules"]).read_text(encoding="utf-8"))
    from .guardrails import resolve as _resolve_guardrails
    rules = _resolve_guardrails(rules)       # #49 — 정책 수치(value_from)를 guardrails.yaml 에서 채운다
    # ☆#5 — 덮는 해를 아는 달력. 표 밖의 날짜는 평일로 짐작하지 않고 CalendarOutOfRange 로 멈춘다
    from .timeutil import HolidayCalendar
    holidays = HolidayCalendar.from_doc(json.loads(Path(P["holidays"]).read_text(encoding="utf-8")))
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
    # #48 — 서버는 설정 값(seoul_key)을 넘긴다. "" 는 끔, None 은 명령줄 관례(환경변수)
    bike_live = (vt.BikeLive(key=seoul_key) if seoul_key else None) if seoul_key is not None else vt.BikeLive.from_env()
    # 라우터는 21번 car.py 의 make_router 로 — 23 이 CarService 를 끼울 때 같은 객체를 나눠 쓴다.
    gh = (gh_url if gh_url is not None else os.environ.get("MOBILITY_GH_URL"))         or (None if gh_url == "" else ((rules.get("car") or {}).get("graphhopper") or {}).get("url", {}).get("value"))
    bike_router = None
    gh_live = None                       # 77-2 — 응답하는 GH 만 택시·자동차 뒤 라우터로도 쓴다(자전거와 같은 객체)
    if gh and str(gh).startswith("http"):
        from .car import make_router
        rt = make_router(gh)
        if rt.info():
            gh_live = rt
            bike_router = vt.BikeRouter(rt, {}, (rules.get("bike") or {}).get("pbf_date") or "2026-09-18")
    # 77-2 — 택시·자동차: 파이썬 도로 라우터 → (있으면) GH → 근거없음. 소요 자료(TOPIS 프로파일)가 없으면 CarService 없음(종전).
    car, car_why = None, "router_off"     # car_why: CarService 가 없는 이유(수단별 후보의 택시 칸이 이유를 가른다)
    road_spec = road_graph if road_graph is not None else (os.environ.get("MOBILITY_ROAD_GRAPH") or "auto")
    from .car import CarGraph, CarService, NoRouter
    from .road_router import resolve as _road_resolve
    if road_spec not in ("", "none") and _road_resolve(road_spec) is None:
        car_why = "no_graph"             # 켜라고 했는데 차도 그래프 파일(또는 scipy)이 없다
    if _road_resolve(road_spec) is not None or gh_live is not None:     # 폴더 확인만(적재는 첫 택시 질의 때)
        cgraph = CarGraph.load(None, holidays)          # 약 2초 — 라우터가 하나라도 있을 때만
        if cgraph is None:
            car_why = "no_profile"       # 도로 소요 자료(TOPIS 프로파일 · 도로급 계수)가 없다
        else:
            road = _road_resolve(road_spec, speed=cgraph.static_kmh)    # 정적 시간 가중(GH 와 같은 잣대 · 본인 10/3)
            if road is not None or gh_live is not None:
                car = CarService(cgraph, gh_live or NoRouter(), rules, road=road)
                car_why = None
    # 혼잡도(v0.8 · 39번 방 · @ 부품) — 파일이 없으면 가산 없음(근거없음). 판정기 CLI 와 같은 두 파일.
    cg_dir = Path(P["timetable"]).parent
    cg_data = vt.Congestion.load([cg_dir / "congestion_v1.jsonl", cg_dir / "congestion_line9_v1.jsonl"], wanted)
    # 버스 구간 통행시간 프로파일(v0.9 · 41번 방) — 파일이 없으면 종전 모델(거리 ÷ 표정속도)
    bus_prof = vt.BusSegProfile.load(P["bus_profile"])
    verifier = vt.Verifier(tt, lo, rules, holidays, tw, bus, sc, ex, car=car, bk=bk, bike_live=bike_live, bike_router=bike_router,
                           cg_data=cg_data, bus_prof=bus_prof)
    verifier.car_why = car_why

    # ── 시간표 '판'. meta 의 built_at 이 있으면 그것, 없으면 행의 수집일.
    #    둘은 다른 값이다 — 어느 쪽인지 접두어로 남긴다. 판정 이력이 "어느 판으로 냈는지"를 잃으면 안 된다.
    built, meta_d = None, {}
    meta = Path(P["meta"])
    if meta.exists():
        try:
            meta_d = json.loads(meta.read_text(encoding="utf-8")) or {}
            built = meta_d.get("built_at")
        except (ValueError, OSError):
            built, meta_d = None, {}
    built_at = f"built:{built}" if built else f"fetched:{tt.fetched_at}"

    # ☆`[2026-09-29 문제목록 #32]` 시간표가 오래됐는지 — 기준(일)은 guardrails mobility.staleness.timetable_warn_days.
    #   앞 판은 규칙에 기준만 있고 코드가 보지 않았다. 판정 불가가 아니라 재수집 신호다 — 경고로 싣는다.
    # ☆`[89 · 2026-10-01]` 나이는 **수집일**로 잰다(앞 판은 built_at = 만든 시각). 만든 시각을 보면 옛 원자료를
    #   다시 빌드만 해도 신선해 보인다. meta 의 원천별 수집일(tago·seoul) 중 **가장 오래된 것** → 없으면 행 fetched_at
    #   → 그것도 없으면 built_at(종전). 어느 값으로 쟀는지 stats["timetable_age_basis"] 에 남긴다.
    collected = sorted(str(v) for v in (meta_d.get("tago_fetched_at"), meta_d.get("seoul_fetched_at")) if v)
    if collected:
        stamp, age_basis = collected[0], "meta_fetched"
    elif tt.fetched_at:
        stamp, age_basis = tt.fetched_at, "row_fetched"
    else:
        stamp, age_basis = built, ("built" if built else None)
    age_days, stale = None, False
    if stamp:
        from datetime import datetime, timezone
        try:
            t0 = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - t0).days
            stale = age_days > rules["staleness"]["timetable_warn_days"]["value"]
        except ValueError:
            age_days, stale = None, True     # 시각을 못 읽으면 오래된 것으로 본다(모르는 채 신선하다고 하지 않는다)

    stats = {"timetable_rows": tt.rows, "timetable_stations": len(tt.stations),
             "skipped_no_dep": tt.skipped_no_dep,
             "bus_routes": len(bus.by_id) if bus else 0,
             "transfer_pairs": len(tw.pairs) if tw else 0,
             "station_coords": len(sc.by_key) if sc else 0,
             "station_exits": sum(len(x) for x in ex.exits.values()) if ex else 0,
             "bike_stations": len(bk.rows) if bk else 0,
             "bike_live": bool(bike_live), "bike_router": bool(bike_router),
             "car_router": (("road_graph_v1" if car.road else "") + ("+gh" if not isinstance(car.router, NoRouter) else "")
                            if car else None),
             "timetable_age_days": age_days, "timetable_age_basis": age_basis, "timetable_stale": stale, "data_dir_source": _paths.SOURCE}
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

    if stale and not quiet:
        print(f"[mobility] ! 시간표 수집이 {age_days}일 전이다(기준 {rules['staleness']['timetable_warn_days']['value']}일) — 재수집이 필요하다")
    mtimes = {k: (str(v), Path(v).stat().st_mtime) for k, v in P.items() if Path(v).exists()}
    return Runtime(verifier, timetable_built_at=built_at,
                   rules_version=rules["rules_version"], stats=stats, source_mtimes=mtimes,
                   build_kw={"paths": paths, "wanted": wanted, "quiet": True, "data_dir": data_dir,
                             "gh_url": gh_url, "seoul_key": seoul_key, "guardrails_path": guardrails_path,
                             "road_graph": road_graph})


def get_verifier(**kw):
    """프로세스당 하나. 여러 번 불러도 한 번만 올린다.

    ☆`[2026-09-29 문제목록 #32]` 적재한 입력 파일이 바뀌었으면 **다시 올린다**(앞 판은 첫 판을 끝까지 썼다).
      다시 올리는 동안 다른 요청은 옛 판을 쓴다 — 새 판이 다 올라간 뒤에만 바꾼다. 실패하면 옛 판을 두고 예외를 올린다.
    """
    global _SINGLETON
    if _SINGLETON is None:
        with _LOCK:
            if _SINGLETON is None:
                _SINGLETON = build_verifier(**kw)
    elif _SINGLETON.changed_sources():
        with _LOCK:
            if _SINGLETON.changed_sources():
                _SINGLETON = build_verifier(**(_SINGLETON.build_kw or kw))
    return _SINGLETON
