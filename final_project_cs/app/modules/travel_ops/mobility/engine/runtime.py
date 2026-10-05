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


_CAR_GRAPHS: dict = {}


class _LazyCar:
    """택시·자동차 판정 서비스 — 속도 프로파일 그래프(약 125MB · 2초)는 **처음 택시를 물을 때** 올린다.

    서버를 띄울 때 올리지 않는 이유: 택시 대안을 한 번도 안 묻는 프로세스(일꾼 등)가 그 메모리를 쓰지 않게 한다.
    올릴 수 없으면(그래프 자료 없음) RouterDown 으로 답한다 — 판정기는 이를 「택시 소요 근거없음」으로 낸다.
    ☆101(2026-10-05 · 합치기) 우리 택시 칸·택시로 메우기가 쓰는 `arrive_by`(도착 목표에 맞춘 가장 늦은 출발)도 같은 서비스로
      넘긴다. 속도 자료가 없을 때의 RouterDown 은 갈래 `no_profile` 을 단다(택시 칸이 이유를 가른다)."""

    def __init__(self, router, rules, holidays):
        from .paths import PROCESSED
        self._router, self._rules, self._holidays = router, rules, holidays
        # 자료 폴더는 **판정기를 세울 때** 정해 둔다 — 첫 택시 때 전역 상태를 읽으면 그 사이 폴더 설정이 바뀐(시험 초기화 · 다시 설정) 뒤를 본다
        self._graph_dir = Path(PROCESSED) / "mobility" / "graph"
        self._svc = None
        self._lock = threading.Lock()

    def _service(self):
        if self._svc is None:
            with self._lock:
                if self._svc is None:
                    from .car import CarGraph, CarService, RouterDown
                    # 같은 자료·같은 공휴일이면 그래프를 나눠 쓴다(판정기를 다시 세울 때마다 125MB 를 새로 올리지 않는다)
                    key = (str(self._graph_dir), frozenset(self._holidays))
                    cg = _CAR_GRAPHS.get(key)
                    if cg is None:
                        cg = CarGraph.load(self._graph_dir, self._holidays)
                        if cg is None:
                            raise RouterDown("도로망 속도 자료(graph/topis_class_factor_v1.json 등)가 없다 - 택시·자동차는 근거없음",
                                             code="no_profile")
                        _CAR_GRAPHS[key] = cg
                    self._svc = CarService(cg, self._router, self._rules)
        return self._svc

    def leg(self, s, e, depart, taxi=False, kind="중형"):
        return self._service().leg(s, e, depart, taxi=taxi, kind=kind)

    def arrive_by(self, s, e, arrive, taxi=True, kind="중형"):
        return self._service().arrive_by(s, e, arrive, taxi=taxi, kind=kind)


def build_verifier(*, paths=None, wanted=None, quiet=False, data_dir=None, seoul_key=None,
                   guardrails_path=None, local_router=False):
    """전부 올려 Runtime 을 만든다. 약 33초.

    paths  : 경로 일부만 바꿔 끼울 수 있다(시험용)
    wanted : None 이면 전부. 배치에서만 집합을 준다
    ☆`[2026-09-29 문제목록 #48]` 서버는 설정 값을 넘긴다 — data_dir(자료 폴더) · seoul_key(따릉이 실시간, "" 이면 끔) ·
      guardrails_path(정책 수치 파일). None 이면 명령줄 관례(환경변수·.env)를 쓴다.
    ☆99(2026-10-04) 경로 서버(gh_url)를 지웠다 — 인자 gh_url 은 없다(연결부 wiring.configure 는 팀 설정 칸 값을 받기만 하고
      넘기지 않는다).
    ☆101(2026-10-05 · 합치기) local_router — 저장소 안 도로 그래프 위 파이썬 길찾기(팀장 graph_router)를 켠다. 켜면 택시·
      자동차 소요, 자전거 승차 소요, 걷기 거리(장소↔역·정류장 · 장소↔장소)가 길 기준이 된다. 기본은 끔(시험·명령줄) — 서버는
      설정 `mobility_local_router`(기본 켬)를 wiring 이 넘긴다. 우리 옛 인자 road_graph · 환경변수 MOBILITY_ROAD_GRAPH 는 없앴다.
      도로 그래프 적재는 첫 길 묻기 때 한 번(약 12초 · +0.25 GB) — 서버는 기동 뒤 미리 올린다(wiring._warm_local_router).
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
    # 따릉이(v0.7 · 22번 방). 대여소 목록 + 실시간 거치 조회. 승차 소요는 길찾기(local_router)가 있을 때만 낸다(101 · 없으면 근거없음).
    bk = vt.BikeStations.load(str(P["bike_stations"]))
    # #48 — 서버는 설정 값(seoul_key)을 넘긴다. "" 는 끔, None 은 명령줄 관례(ACOP_SEOUL_OPENAPI_KEY · bike.BikeLive.from_env)
    bike_live = (vt.BikeLive(key=seoul_key) if seoul_key else None) if seoul_key is not None else vt.BikeLive.from_env()
    # ☆101(2026-10-05 · 합치기) 길찾기는 팀장 graph_router.GraphRouter 하나 — 택시·자동차(_LazyCar) · 자전거·걷기(BikeRouter)가 같은
    #   객체를 나눠 쓴다. 경로 서버 가지(주소 읽기 · make_router(서버))는 99 대로 없다. local_router 가 꺼져 있거나 도로 그래프
    #   파일이 없으면 라우터 없음 → 택시·자전거 소요는 근거없음, 걷기는 직선 × 우회계수.
    bike_router = None
    router_obj, router_kind = None, None
    car, car_why = None, "router_off"     # car_why: 택시 서비스가 없는 이유(수단별 후보의 택시 칸이 이유를 가른다 · 77-2)
    if local_router:
        from .graph_router import GraphRouter
        gr = GraphRouter.default()
        if gr.available():
            router_obj, router_kind = gr, "local"
        else:
            car_why = "no_graph"          # 켜라고 했는데 도로 그래프 파일이 없다
    if router_obj is not None:
        bike_router = vt.BikeRouter(router_obj, {}, (rules.get("bike") or {}).get("pbf_date") or "2026-09-18")
        # ☆서비스 런타임은 전엔 택시 서비스(car)를 한 번도 끼우지 않았다 - 라우터가 떠 있어도 택시는 늘 「소요 근거없음」이었다.
        #   속도 프로파일 자료가 없으면 첫 호출에서 RouterDown(근거없음 · 갈래 no_profile)으로 답한다(_LazyCar).
        car = _LazyCar(router_obj, rules, holidays)
        car_why = None
    # 혼잡도(v0.8 · 39번 방 · @ 부품) — 파일이 없으면 가산 없음(근거없음). 판정기 CLI 와 같은 두 파일.
    cg_dir = Path(P["timetable"]).parent
    cg_data = vt.Congestion.load([cg_dir / "congestion_v1.jsonl", cg_dir / "congestion_line9_v1.jsonl"], wanted)
    # 버스 구간 통행시간 프로파일(v0.9 · 41번 방) — 파일이 없으면 종전 모델(거리 ÷ 표정속도)
    bus_prof = vt.BusSegProfile.load(P["bus_profile"])
    verifier = vt.Verifier(tt, lo, rules, holidays, tw, bus, sc, ex, car=car, bk=bk, bike_live=bike_live,
                           bike_router=bike_router, cg_data=cg_data, bus_prof=bus_prof)
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
             "bike_live": bool(bike_live), "bike_router": bool(bike_router), "router": router_kind, "car": bool(car),
             "car_router": (Path(str(router_obj.dir)).name if (car and getattr(router_obj, "dir", None)) else None),
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
              f"라우터 {router_kind if bike_router else 'off(소요 근거없음)'}")

    if stale and not quiet:
        print(f"[mobility] ! 시간표 수집이 {age_days}일 전이다(기준 {rules['staleness']['timetable_warn_days']['value']}일) — 재수집이 필요하다")
    mtimes = {k: (str(v), Path(v).stat().st_mtime) for k, v in P.items() if Path(v).exists()}
    return Runtime(verifier, timetable_built_at=built_at,
                   rules_version=rules["rules_version"], stats=stats, source_mtimes=mtimes,
                   build_kw={"paths": paths, "wanted": wanted, "quiet": True, "data_dir": data_dir,
                             "seoul_key": seoul_key, "guardrails_path": guardrails_path,
                             "local_router": local_router})


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
