# app/modules/travel_ops/mobility/engine/verify_time_cli.py — 시각 검증기의 명령줄 · 회귀 대조 · 화면 출력
#
# ☆`[2026-10-04 문제목록 #58]` verify_time.py 3,200줄 중 서비스가 안 쓰는 600줄(화면 출력 show · 사례 파일 읽기 load_cases ·
#   회귀 대조 check_expect · 명령줄 main)을 이 파일로 옮겼다. 판정 본체(Verifier·Timetable 등)는 verify_time.py 에 그대로다.
#   서비스 런타임은 이 파일을 부르지 않는다(runtime.py 는 verify_time 만 쓴다). 시험·명령줄만 쓴다.
#   옛 이름(verify_time.load_cases · check_expect · build_verifier_for_cases · main · show · MARK · OUT_MARK)은
#   verify_time.py 의 `__getattr__` 이 이 파일로 이어 주므로 부르는 쪽은 바꾸지 않아도 된다.
# ☆101(2026-10-05 · 합치기) 이동 담당 쪽 명령줄 수정(92 그 편이 서는가 대조 · 93·94 수단별 후보 대조 · 97 택시 메우기 대조 ·
#   99 `--road-graph` · 키 이름)을 이 파일 위에 얹었다. 달라진 것: 경로 서버 인자(`--gh-url` · `--allow-router-down`)는 없다 ·
#   `--road-graph` 가 정하는 길찾기는 팀장 graph_router 하나 · 자전거 요약 픽스처 인자(`--bike-fixture` · `--bike-record`)는
#   팀장 판대로 다시 있다(자전거 승차 소요를 다시 낸다 — 본인 10/5).
#
# 실행(저장소 루트에서 · 종전과 같다 — verify_time 이 이 파일의 main 을 부른다):
#   python -m app.modules.travel_ops.mobility.engine.verify_time --cases <사례.json> --check-expect
import argparse, collections, json, sys
from pathlib import Path

from .verify_time import (BIKE_NO_ROUTE, BikeLive, BikeRouter, BikeStations, BusRoutes, BusSegProfile,
                          CandidateGraph, CarGraph, CarService, Congestion, HolidayCalendar, LineOrder, OUT_VERDICTS,
                          RULES_DIR, StationCoords, StationExits, Timetable, TransferWalk, VERDICTS, Verifier, dedup_warn,
                          fmt_min, leg_txt, to_service_min)
from .car_fixtures import FixtureRouter


MARK = {"feasible": "성립", "infeasible": "불가",
        "rejected_by_limit": "탈락", "unknown": "근거없음"}
OUT_MARK = {"feasible": "성립", "infeasible": "불가"}


def show(case, res, verbose=False):
    print(f"\n[{res.id}] {case.get('note','')}")
    print(f"  {case['date']}({res.day_type}) {case.get('depart_at')} 출발"
          + (f" · {case['arrive_by']} 도착 필요" if case.get("arrive_by") else ""))
    for d in case.get("disruptions") or []:
        w = {"line_closed": d.get("line"), "route_closed": f"버스 {d.get('route')}",
             "route_detour": f"버스 {d.get('route')} 우회",
             "stop_skip": f"버스 {d.get('route') or '전 노선'} 정류장 {d.get('ars')}",
             "station_skip": f"{d.get('line')} {d.get('station')}",
             "edge_closed": f"{d.get('line')} {'–'.join(d.get('between') or [])}"}.get(d.get("kind"))
        print(f"  ◆ 이슈 {w} — {d.get('note') or d.get('kind')} "
              f"[{d.get('grade','추정')}] {d.get('source','core.current_state.replan')}")
    print(f"  판정 {MARK[res.verdict]} [{res.grade}] — {res.reason}")
    if res.relief:
        print(f"  완화 조건: {res.relief}")
    if res.out:
        o = res.out
        bits = [f"밖 {OUT_MARK[o['verdict']]}"]
        if o.get("code"):
            bits.append(f"이유 {o['code']}")
        if o.get("arrive_min") is not None:
            bits.append(f"예정 {fmt_min(o['arrive_min'])}")
        if o.get("margin_min") is not None:
            bits.append(f"+@{o['margin_min']}분")
        if o.get("slack_min") is not None:
            bits.append(f"여유 {o['slack_min']}분")
        if o.get("last_feasible_depart_min") is not None:
            bits.append(f"늦어도 {fmt_min(o['last_feasible_depart_min'])} 출발")
        if o.get("eta_min") is not None:
            bits.append(f"소요 {o['eta_min']}분(중앙값)")
        print("  " + " · ".join(bits))
    for w in dedup_warn(res.warnings):
        print(f"  ! [{w['code']}] {w['text']}")
    if res.candidates is not None:
        print(f"  후보 {len(res.candidates)}개 (순위 없음 — 순서는 규칙 candidates.기준 의 순서다)")
        for c in res.candidates:
            arr = f" → 도착 {fmt_min(c['arrive_min'])}" if c["arrive_min"] is not None else ""
            w = (f" (도보 {c['walk_in_min']}+{c['walk_out_min']}분 포함)"
                 if c["walk_in_min"] or c["walk_out_min"] else "")
            tie = f" ≈동급 #{','.join(map(str, c['tie_with']))}" if c["tie_with"] else ""
            print(f"    #{c['n']} [{'·'.join(c['criteria'])}] {c['label']} — {MARK[c['verdict']]} "
                  f"{fmt_min(c['depart_min'])} 출발{arr}{w} · 환승 {c['transfers']} · 도보 {c['walk_min']:g}분 "
                  f"[{c['grade']}]{tie}")
            if c["verdict"] != "feasible":
                print(f"       {c['reason']}")
            cw = [w["code"] for w in dedup_warn(c.get("warnings"))]
            if cw:
                print(f"       ! {' '.join(cw)}")
            if verbose:
                for l in c["legs_result"]:
                    extra = (f" · 승차 {l.ride_min:g}분[{l.ride_grade}] → 도착 {fmt_min(l.arrive_min)}"
                             if l.ride_min is not None else "")
                    print(f"       - {l.label}: {MARK[l.verdict]} {l.reason}{extra}")
        if res.axis_best:
            print("    축별 사실(순위 아님): " + " · ".join(
                f"{ax} 최소 #{','.join(map(str, ns))}" for ax, ns in res.axis_best.items()))
        for t in res.ties:
            print(f"    ≈ 동급 #{t['a']} · #{t['b']} — 도착 차이 {t['delta_min']}분 ≤ 불확실성 {t['band_min']}분 · "
                  + " · ".join(f"{k} {v[0]} vs {v[1]}" for k, v in t["axes"].items())
                  + (" (축에서도 차이 없음)" if t["same_on_axes"] else ""))
        for dcand in res.dropped_candidates:
            lg = " → ".join(leg_txt(l) for l in dcand["legs"])
            why = dcand.get("why") or f"생성기 추정 {dcand['est_min']}분 — 허용 소요 배수 초과"
            print(f"    ✗ 뺀 후보 [{'·'.join(dcand['criteria'])}] {lg} — {why}")
        if res.bus_rejected:
            print(f"    · 버스 직행 후보 중 성립 안 함 {len(res.bus_rejected)}개: "
                  + " / ".join(f"{b['label']}({MARK[b['verdict']]}: {b['reason']})" for b in res.bus_rejected))
    if res.taxi:
        print(f"  대안 {len(res.alternatives)}개 (순위 없음 — 총소요 등급이 추정이라 순위 자체가 추정이 된다)")
        for al in res.alternatives:
            arr = f" → 도착 {fmt_min(al['arrive_min'])}" if al["arrive_min"] is not None else " → 도착 미상"
            w = ""
            if al.get("walk_in_min") or al.get("walk_out_min"):
                w = f" (도보 {al.get('walk_in_min',0)}+{al.get('walk_out_min',0)}분 포함)"
            print(f"    · [{al['axis']}] {al['label']} — {fmt_min(al['depart_min'])} 출발{arr}{w} [{al['grade']}]")
            # 대안의 경고도 보인다 — 공항버스 별도 요금처럼 **그 대안을 고를지 바꾸는** 말이
            # 여기 있는데 안 찍혀 안 보였다(2026-09-10). 케이스 경고와 겹치는 줄은 뺀다.
            seen_codes = {x["code"] for x in res.warnings}
            for aw in dedup_warn(al.get("warnings")):
                if aw["code"] not in seen_codes:
                    print(f"      ! [{aw['code']}] {aw['text']}")
        if res.taxi:
            tx = res.taxi
            if tx["verdict"] == "feasible":
                print(f"    · [수단교체] 택시 — {fmt_min(tx['depart_min'])} 출발 → 도착 {fmt_min(tx['arrive_min'])} · "
                      f"{tx['reason']} [{tx['grade']}]")
                seen_codes = {x["code"] for x in res.warnings}
                for aw in dedup_warn(tx.get("warnings")):
                    if aw["code"] not in seen_codes:
                        print(f"      ! [{aw['code']}] {aw['text']}")
            else:
                print(f"    · [수단교체] 택시 — {tx['reason']} [근거없음]")
        if verbose and res.alt_tried:
            print("    열거한 후보: " + ", ".join(f"{lb}={MARK[v]}" for _ax, lb, v in res.alt_tried))
    if verbose:
        for l in res.legs:
            extra = ""
            if l.ride_min is not None:
                extra = f" · 승차 {l.ride_min:g}분[{l.ride_grade}] → 도착 {fmt_min(l.arrive_min)}"
            print(f"    - {l.label}: {MARK[l.verdict]} {l.reason}{extra}")
            if l.dropped:
                print("      거른 행: " + ", ".join(f"{k} {v}" for k, v in sorted(l.dropped.items())))
        for e in res.evidence:
            print(f"      · [{e['grade']}] {e['source_id']} — {e['claim']}")


def load_cases(path, only=None):
    """케이스 파일(JSON) → cases 목록. only 를 주면 그 id 하나만."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = doc["cases"] if isinstance(doc, dict) else doc
    if only:
        cases = [c for c in cases if c.get("id") == only]
        if not cases:
            raise SystemExit(f"케이스 {only} 가 없다")
    return cases


def road_graph_default(environ=None):
    """`--road-graph` 를 안 줬을 때의 값 — "auto"(자료 폴더의 도로 그래프가 있으면 켠다).
    ☆99(2026-10-04) 회귀 대조(--check-expect)도 **켠 채**가 기본이다. ☆101 환경변수 MOBILITY_ROAD_GRAPH 는 읽지 않는다
      (켜고 끄는 칸은 서버 설정 mobility_local_router 하나 · 명령줄은 이 인자)."""
    return "auto"


def road_of(spec):
    """`--road-graph` 값 → 길찾기 객체. auto = 자료 폴더의 도로 그래프(graph_router.GraphRouter.default · 없으면 None) ·
    <폴더> = 그 폴더 · fixture:<파일> = 합성 경로 대역(car_fixtures.FixtureRouter) · none/빈 값 = 없음(택시·자전거 근거없음)."""
    spec = str(spec or "")
    if spec in ("", "none"):
        return None
    if spec.startswith("fixture:"):
        return FixtureRouter(spec[len("fixture:"):])
    from .graph_router import GraphRouter
    gr = GraphRouter.default() if spec == "auto" else GraphRouter(spec)
    return gr if gr.available() else None


def build_verifier_for_cases(args, cases):
    """CLI 인자(argparse Namespace 또는 같은 속성을 가진 객체) + 케이스 목록 → (Verifier, ctx).

    71번 방(2026-09-29): main() 안에 있던 데이터 올리기 블록을 그대로 옮겼다 — pytest 회귀
    (tests/unit/travel/mobility/test_regression_cases.py)가 CLI 와 **같은 적재**를 쓰기 위해서다.
    판정 경로·출력은 바뀌지 않았다. ctx = {record, bike_router, bike_live, rules} (main 의 끝맺음용).
    """
    if not all((args.timetable, args.order, args.transfer_walk, args.bus_route,
                args.bus_stops, args.station_coords, args.station_exits)):
        from .paths import PROCESSED, timetable_file
        args.timetable = args.timetable or str(timetable_file(PROCESSED / "mobility"))   # 73 후속 3-4: gz 우선
        args.order = args.order or str(PROCESSED / "mobility" / "line_station_order_v1.json")
        args.transfer_walk = args.transfer_walk or str(PROCESSED / "mobility" / "transfer_walk_v1.json")
        args.bus_route = args.bus_route or str(PROCESSED / "mobility" / "bus_route_v1.jsonl")
        args.bus_stops = args.bus_stops or str(PROCESSED / "mobility" / "bus_stops_v1.jsonl")
        args.station_coords = args.station_coords or str(PROCESSED / "mobility" / "station_coords.json")
        args.station_exits = args.station_exits or str(PROCESSED / "mobility" / "station_exits_v1.json")
    if not args.bike_stations:
        from .paths import PROCESSED
        args.bike_stations = str(PROCESSED / "mobility" / "bike_stations_v1.jsonl")


    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    holidays = HolidayCalendar.from_doc(json.loads(Path(args.holidays).read_text(encoding="utf-8")))   # #5 덮는 해를 안다
    lo = LineOrder.load(args.order)
    tw = TransferWalk.load(args.transfer_walk,
                           rules["measured_baseline"]["kakao_walk_speed_mps"]["value"])
    bus = BusRoutes.load(args.bus_route, args.bus_stops)
    sc = StationCoords.load(args.station_coords)
    ex = StationExits.load(args.station_exits)
    bk = BikeStations.load(args.bike_stations)
    bike_live = None
    if args.bike_live == "env":
        bike_live = BikeLive.from_env()            # ACOP_SEOUL_OPENAPI_KEY — 환경변수 → final_project_cs/.env → .env.apikeys
        if bike_live is None:
            print(f"  ! --bike-live env 인데 {BikeLive.KEY_NAME} 가 없다 — 가용은 근거없음으로 낸다")
    elif args.bike_live and args.bike_live != "none":
        bike_live = BikeLive.from_fixture(json.loads(Path(args.bike_live).read_text(encoding="utf-8")))
    # 자전거 경로 요약 픽스처(거리·시간만 · 형상 없음) — 속성이 없는 호출(인자 묶음을 직접 만드는 쪽)은 없는 것으로
    fixture = (json.loads(Path(args.bike_fixture).read_text(encoding="utf-8"))
               if getattr(args, "bike_fixture", None) else {})
    record = None
    if getattr(args, "bike_record", None):
        rp = Path(args.bike_record)
        record = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
        fixture = dict(record, **fixture) if fixture else dict(record)

    # 시간표는 케이스에 나오는 (노선, 역) 만 올린다 — 46만 행을 통째로 들지 않는다.
    # ★ 19번 방(2026-09-19): 대안이 쓸 (노선, 역) 도 같이 올린다. 종전에는 케이스 구간의 노선만 올려서
    #   ⓑ 노선교체(다른 노선)와 버스 구간의 수단교체(지하철) 후보가 시간표 없음 → 근거없음으로 죽었다.
    #   런타임(runtime.py)은 전체를 상주시키므로 이 차이는 회귀에서만 있었다.
    # ★ 20번 방(2026-09-20): multi 케이스는 legs 가 없다 — 생성기를 먼저 돌려 후보 구간의 (노선, 역)을 올린다.
    #   생성기는 시간표를 안 쓰므로 여기서 미리 돌릴 수 있다. 판정 때 다시 만들어도 같은 후보가 나온다.
    pre_legs = []
    for c in cases:
        if c.get("multi"):
            cg = CandidateGraph(lo, tw, rules, c.get("first_visit", True))
            for cand in cg.candidates(c["multi"]["from"], c["multi"]["to"], rules["candidates"]["기준"]["value"],
                                      origin_lines=c["multi"].get("from_lines"), dest_lines=c["multi"].get("to_lines")):
                pre_legs += cand.legs
    all_legs = [l for c in cases for l in c.get("legs") or []] + pre_legs
    wanted = {(l["line"], nm) for l in all_legs if l.get("line") for nm in (l["from"], l["to"])}
    # 87 — 혼합 후보(multi.mixed)는 끊는 역이 어디일지 미리 모른다 → 그런 케이스가 있으면 시간표를 통째로 올린다(런타임과 같다)
    full_tt = any((c.get("multi") or {}).get("mixed") for c in cases)
    lines_of = collections.defaultdict(set)
    for ln, L in lo.doc["lines"].items():
        for st in L["stations"]:
            lines_of[st["station_nm"]].add(ln)
    radius = rules["alternatives"]["정류장_반경_m"]["value"]
    for c in cases:
        for l in c.get("legs") or []:
            if l.get("line"):
                for ln in lines_of[l["from"]] & lines_of[l["to"]]:
                    wanted |= {(ln, l["from"]), (ln, l["to"])}
            elif l.get("mode") == "bus" and bus and sc:
                r = bus.route(str(l["route"]))
                seg = bus.segment(r.route_id, l["from"], l["to"]) if r else None
                if seg:
                    for row in seg[:2]:
                        for _d, v in sc.stations_near(row["lat"], row["lng"], radius):
                            wanted |= {(ln, v["station_nm"]) for ln in lines_of[v["station_nm"]]}
    tt = Timetable.load(args.timetable, None if full_tt else wanted)
    # 혼잡도(v0.8) — 케이스에 나오는 (노선, 역)만 올린다. 파일이 없으면 가산 없음(근거없음).
    cg_data = None
    if args.congestion != ["none"]:
        if not args.congestion:
            from .paths import PROCESSED
            args.congestion = [str(PROCESSED / "mobility" / "congestion_v1.jsonl"),
                               str(PROCESSED / "mobility" / "congestion_line9_v1.jsonl")]
        cg_data = Congestion.load(args.congestion, wanted)
    print(f"시간표 {args.timetable}")
    print(f"  유효 출발 {tt.rows:,}행 · 출발없음 {tt.skipped_no_dep:,}행 · "
          f"역 {len(tt.stations)} · 수집 {tt.fetched_at}")
    print(f"역 순서 {lo.built_at} · 규칙 {rules['rules_version']}({rules['effective_date']})")
    print(f"혼잡도 {'없음(가산 근거없음)' if not cg_data else f'{cg_data.rows:,}셀 · ' + ' · '.join(sorted(x for x in cg_data.source_ids if x))}")
    if tw is None:
        print("  ! 환승 거리표(transfer_walk_v1.json)를 못 찾았다 — 환승 도보는 근거없음으로 낸다")
    else:
        print(f"환승 거리표 {tw.built_at} · {len(tw.pairs)}쌍 · {len(tw.stations)}역")
    if bus:
        print(f"버스 {bus.fetched_at} · {len(bus.by_id)}노선 · 정류장 {sum(len(x) for x in bus.stops.values()):,}행")
    if sc:
        print(f"역 좌표 {sc.built_at} · {len(sc.by_key)}역")
    if ex is None:
        print("  ! 역 출구표(station_exits_v1.json)를 못 찾았다 — 정류장↔역 환승은 역 좌표로 잰다")
    else:
        print(f"역 출구 {ex.built_at} · {len(ex.exits)}역명 · {sum(len(v) for v in ex.exits.values()):,}출구 [{ex.grade}]")
    # 길찾기(101) — 택시·자동차 · 자전거·대여소 도보가 **같은 객체**를 나눠 쓴다(팀장 graph_router · 시험은 합성 경로 대역).
    #   속성이 없는 호출(인자 묶음을 직접 만드는 쪽)은 끈다 — 켜려면 road_graph 를 준다(pytest 회귀는 묶음마다 준다).
    road_spec = getattr(args, "road_graph", "none")
    if road_spec is None:
        road_spec = road_graph_default()
    road = road_of(road_spec)
    is_fixture = isinstance(road, FixtureRouter)
    # 자동차·택시(v0.6) — 소요 자료(TOPIS 프로파일)는 프로세스당 한 번. 경로는 위 길찾기 하나(99 — 경로 서버 삭제).
    car = None
    cg = CarGraph.load(args.graph_dir, holidays)
    if cg is None:
        print("  ! 도로망 그래프 자료(graph/topis_class_factor_v1.json 등)를 못 찾았다 — 택시·자동차는 근거없음으로 낸다")
    else:
        car = CarService(cg, road, rules)
        print(f"도로망 {len(cg.prof):,}셀 · 링크표 way {len(cg.seg):,} · 길찾기 "
              + ((f"fixture:{Path(str(road_spec)[len('fixture:'):]).name}" if is_fixture
                  else f"{getattr(road, 'dir', '?')}(회전 제약 없음 · 추정)") if road
                 else f"없음({road_spec}) — 택시·자동차는 근거없음"))
    # 자전거(22번 · 101 에서 되살림) — 위 길찾기를 **그대로** 쓴다(profile=bike/foot). 합성 경로 대역(fixture:)은 자전거 키가 없어
    #   붙이지 않는다. 자전거 요약 픽스처(--bike-fixture / --bike-record)가 있으면 그것을 먼저 본다.
    live_router = road if (road is not None and not is_fixture) else None
    pbf_date = (rules.get("bike") or {}).get("pbf_date") or "2026-09-18"
    bike_router = BikeRouter(live_router, fixture, pbf_date, record=record) if (live_router or fixture) else None
    if bk is None:
        print("  ! 따릉이 대여소(bike_stations_v1.jsonl)를 못 찾았다 — 자전거는 근거없음으로 낸다")
    else:
        how = " + ".join(x for x in ("로컬 길찾기" if live_router else "", f"픽스처 {len(fixture)}건" if fixture else "") if x)
        print(f"따릉이 대여소 {len(bk.rows):,}곳 · {bk.checked_at} · 승차 소요 {how or '없음(' + BIKE_NO_ROUTE + ')'}"
              f" · 실시간 {'env' if args.bike_live == 'env' else ('픽스처' if bike_live else '없음(가용 근거없음)')}")
    # 버스 구간 통행시간 프로파일(v0.9 · 41번 방) — 파일이 없으면 종전 모델(거리 ÷ 표정속도 · worst 스프레드 근거없음)
    bus_prof = None if args.bus_profile == "none" else BusSegProfile.load(args.bus_profile)
    if bus_prof is None:
        print("  ! 버스 구간 프로파일(bus_seg_profile_v1.jsonl.gz)을 못 찾았거나 끔 — 버스 승차는 표정속도 모델로 낸다")
    else:
        print(f"버스 구간 프로파일 {len(bus_prof.index):,}구간 · {bus_prof.dates} · {bus_prof.source_id}")
    v = Verifier(tt, lo, rules, holidays, tw, bus, sc, ex, car, bk, bike_live, bike_router, cg_data, bus_prof)
    return v, {"record": record, "bike_router": bike_router, "bike_live": bike_live, "rules": rules}


def check_expect(c, r):
    """케이스 c 의 expect 칸과 결과 r 을 대조한다 → (miss, skipped).

    71번 방(2026-09-29): main() 의 `--check-expect` 블록을 그대로 옮겼다(비교 칸·문구 동일).
    pytest 회귀와 CLI 가 같은 함수를 쓴다 — 대조 규칙이 두 군데 생기지 않게.
    miss 항목 = (case_id, 기대, 실제) · skipped 항목 = (case_id, 축 이름).
    """
    miss, skipped = [], []
    # ★ 판정값만 대조하면 부족하다. 2026-09-10 의 순환선 버그는 판정이 계속 '성립'이었고
    #   **도착 시각만** 73.3분으로 틀려 있었다. expect_arrive 가 그걸 잡는다.
    # ★ v0.8 — `expect` 는 **밖 판정 둘**(feasible/infeasible)이다. 옛 4값 기대는 `expect_internal` 로 옮긴다.
    #   옛 파일의 `expect: unknown|rejected_by_limit` 은 어휘 밖이라 MISS 다 — 그게 재정의 전 「전부 MISS 장면」이다.
    o = r.out or {}
    if c.get("expect"):
        if c["expect"] not in OUT_VERDICTS:
            miss.append((c["id"], f"expect '{c['expect']}' (어휘 밖 — 밖 판정은 {'/'.join(OUT_VERDICTS)})", o.get("verdict")))
            print(f"  >> MISS expect '{c['expect']}' 은 v0.8 어휘 밖이다 — expect_internal 로 옮긴다")
        elif c["expect"] != o.get("verdict"):
            miss.append((c["id"], f"밖 {OUT_MARK[c['expect']]}", f"밖 {OUT_MARK.get(o.get('verdict'), '없음')} ({o.get('code')})"))
            print(f"  >> MISS 밖 판정 기대 {OUT_MARK[c['expect']]} / 실제 {OUT_MARK.get(o.get('verdict'), '없음')} ({o.get('code')})")
    ei = c.get("expect_internal")
    if ei and ei != r.verdict:
        miss.append((c["id"], f"내부 {MARK[ei]}", f"내부 {MARK[r.verdict]}"))
        print(f"  >> MISS 내부 판정 기대 {MARK[ei]} / 실제 {MARK[r.verdict]}")
    er = c.get("expect_reason")
    if er is not None and o.get("code") != er:
        miss.append((c["id"], f"이유 {er}", str(o.get("code"))))
        print(f"  >> MISS 이유 코드 기대 {er} / 실제 {o.get('code')}")
    es = c.get("expect_slack_min")
    if es is not None:
        lo_, hi_ = es
        sv = o.get("slack_min")
        if sv is None or (lo_ is not None and sv < lo_) or (hi_ is not None and sv > hi_):
            miss.append((c["id"], f"여유 {lo_}~{hi_}분", str(sv)))
            print(f"  >> MISS 여유(slack_min) 기대 {lo_}~{hi_} / 실제 {sv}")
    em = c.get("expect_margin_min")
    if em is not None:
        lo_, hi_ = em
        mv = o.get("margin_min")
        if mv is None or (lo_ is not None and mv < lo_) or (hi_ is not None and mv > hi_):
            miss.append((c["id"], f"@ {lo_}~{hi_}분", str(mv)))
            print(f"  >> MISS @(margin_min) 기대 {lo_}~{hi_} / 실제 {mv}")
    if "expect_p90_eta" in c:
        # ★ v0.9(41) — p90_eta_min 은 스프레드 소스가 있을 때만 나온다. null 이면 「없어야 한다」.
        ep, pv = c["expect_p90_eta"], o.get("p90_eta_min")
        bad = (pv is not None) if ep is None else (pv is None or pv < ep[0] or pv > ep[1])
        if bad:
            miss.append((c["id"], f"p90_eta {ep}", str(pv)))
            print(f"  >> MISS p90_eta_min 기대 {ep} / 실제 {pv}")
    el = c.get("expect_last_depart")
    if el is not None:
        want = None if el is None else fmt_min(to_service_min(el))
        got = fmt_min(o["last_feasible_depart_min"]) if o.get("last_feasible_depart_min") is not None else None
        if want != got:
            miss.append((c["id"], f"늦어도 출발 {el}", str(got)))
            print(f"  >> MISS 마지막 성립 출발 기대 {el} / 실제 {got}")
    if "expect_last_depart_none" in c and c["expect_last_depart_none"] and o.get("last_feasible_depart_min") is not None:
        miss.append((c["id"], "늦어도 출발 없음", fmt_min(o["last_feasible_depart_min"])))
        print(f"  >> MISS 마지막 성립 출발이 없어야 하는데 {fmt_min(o['last_feasible_depart_min'])}")
    # ★ 완화 조건도 대조한다. 2026-09-10 에 24:50 요청이 '불가' 는 맞는데 완화 조건이
    #   "286분 뒤 첫차를 기다리면 성립" 으로 나간 적이 있다(막차를 4분 놓친 것인데).
    #   판정값만 보는 대조는 그걸 통과시켰다.
    am = c.get("expect_alt_min")
    if am is not None and len(r.alternatives) < am:
        miss.append((c["id"], f"대안 {am}개 이상", f"{len(r.alternatives)}개"))
        print(f"  >> MISS 대안 {am}개 이상 기대 / 실제 {len(r.alternatives)}개")
    # ★ 2026-09-14 신설 — 「없어야 한다」를 말할 축이 하나도 없었다.
    #   expect_alt_min/axis/arrive/warn_codes 는 전부 **있어야 한다**만 본다. 그래서
    #   `expect_alt_min: 0` 으로 잠근 척한 케이스 셋(ISSUE-06·ALT-03·ALT-04)은
    #   len < 0 이 영원히 거짓이라 **한 번도 검사된 적이 없다.**
    #   실제로 그 구멍으로 TOUR12 가 ISSUE-01 의 대안에 들어왔고 회귀 85건은 전부 통과했다.
    ax = c.get("expect_alt_max")
    if ax is not None and len(r.alternatives) > ax:
        got = [x["label"] for x in r.alternatives]
        miss.append((c["id"], f"대안 {ax}개 이하", f"{len(r.alternatives)}개: {got}"))
        print(f"  >> MISS 대안 {ax}개 이하 기대 / 실제 {len(r.alternatives)}개 — {got}")
    # ★ 수단별 대안 상한(2026-09-20 · 22번 방). 자전거 후보가 생기면서 「대안 0」 잠금(ISSUE-06·ALT-03)이
    #   자전거 하나로 풀린다 — 자전거는 지하철 이슈를 상속하지 않으므로 나오는 게 맞다. 잠금을 수단별로 옮긴다.
    axm = c.get("expect_alt_max_by_mode") or {}
    for md, mx in axm.items():
        got = [x["label"] for x in r.alternatives if x.get("mode", "subway") == md]
        if len(got) > mx:
            miss.append((c["id"], f"{md} 대안 {mx}개 이하", f"{len(got)}개: {got}"))
            print(f"  >> MISS {md} 대안 {mx}개 이하 기대 / 실제 {len(got)}개 — {got}")
    amn = c.get("expect_alt_min_by_mode") or {}
    for md, mn in amn.items():
        got = [x["label"] for x in r.alternatives if x.get("mode", "subway") == md]
        if len(got) < mn:
            miss.append((c["id"], f"{md} 대안 {mn}개 이상", f"{len(got)}개"))
            print(f"  >> MISS {md} 대안 {mn}개 이상 기대 / 실제 {len(got)}개")
    aar = c.get("expect_alt_arrive")
    if aar:
        got = [fmt_min(x["arrive_min"]) for x in r.alternatives]
        if fmt_min(to_service_min(aar)) not in got:
            miss.append((c["id"], f"대안 도착 {aar}", str(got)))
            print(f"  >> MISS 대안 도착 {aar} 가 없다 — 실제 {got}")
    aa = c.get("expect_alt_axis")
    if aa and aa not in [x["axis"] for x in r.alternatives]:
        miss.append((c["id"], f"대안 축 '{aa}'", str([x["axis"] for x in r.alternatives])))
        print(f"  >> MISS 대안 축 '{aa}' 가 없다")
    want = c.get("expect_relief_contains")
    if want and want not in (r.relief or ""):
        miss.append((c["id"], f"완화 조건에 '{want}'", f"'{r.relief}'"))
        print(f"  >> MISS 완화 조건에 '{want}' 가 없다 — 실제: {r.relief}")
    # ★ 경고 축(2026-09-13). 문장이 아니라 **코드**로 건다 — 문구를 다듬어도 회귀가 안 깨진다.
    #   케이스 경고와 대안 경고를 합쳐서 본다(공항 요금처럼 대안에만 붙는 것이 있다).
    wc = c.get("expect_warn_codes")
    if wc:
        got = {w["code"] for w in (r.warnings or [])}
        for al in (r.alternatives or []):
            got |= {w["code"] for w in (al.get("warnings") or [])}
        lack = [x for x in wc if x not in got]
        if lack:
            miss.append((c["id"], f"경고 {lack}", str(sorted(got))))
            print(f"  >> MISS 경고 {lack} 가 없다 — 실제 {sorted(got)}")
    # ★ 「없어야 한다」 경고 축 + 사유 문구 축(2026-09-20 · 22번 방). 외국인 안내가 foreign=false 에 붙으면 잡는다.
    wa = c.get("expect_warn_codes_absent")
    if wa:
        got = {w["code"] for w in (r.warnings or [])}
        for al in (r.alternatives or []):
            got |= {w["code"] for w in (al.get("warnings") or [])}
        bad = [x for x in wa if x in got]
        if bad:
            miss.append((c["id"], f"경고 {bad} 없음", str(sorted(got))))
            print(f"  >> MISS 경고 {bad} 가 없어야 하는데 있다 — 실제 {sorted(got)}")
    rc = c.get("expect_reason_contains")
    if rc:
        blob = " | ".join([r.reason or ""] + [(l.reason or "") for l in (r.legs or [])])
        if rc not in blob:
            miss.append((c["id"], f"사유에 '{rc}'", blob[:160]))
            print(f"  >> MISS 사유에 '{rc}' 가 없다 — 실제: {blob[:160]}")
    ea = to_service_min(c.get("expect_arrive")) if c.get("expect_arrive") else None
    if ea is not None and ea != r.arrive_min:
        miss.append((c["id"], f"도착 {fmt_min(ea)}", f"도착 {fmt_min(r.arrive_min)}"))
        print(f"  >> MISS 도착 기대 {fmt_min(ea)} / 판정 {fmt_min(r.arrive_min)}")
    # ★ 다목적 후보 축(2026-09-20 · 20번 방). 후보 수·기준·구간열·기준별 도착·동급·성립 상한·노선별 불가.
    #   「있어야 한다」와 「없어야 한다」를 둘 다 둔다(expect_tie: false · expect_feasible_max · expect_no_line_feasible).
    cs = r.candidates or []
    cm = c.get("expect_candidates_min")
    if cm is not None and len(cs) < cm:
        miss.append((c["id"], f"후보 {cm}개 이상", f"{len(cs)}개"))
        print(f"  >> MISS 후보 {cm}개 이상 기대 / 실제 {len(cs)}개")
    for cr in c.get("expect_criteria") or []:
        if not any(cr in x["criteria"] for x in cs):
            miss.append((c["id"], f"기준 '{cr}' 후보", str([x["criteria"] for x in cs])))
            print(f"  >> MISS 기준 '{cr}' 을 단 후보가 없다")
    for cr, legs in (c.get("expect_candidate_legs") or {}).items():
        want = [tuple(x) for x in legs]
        got = [[("자전거" if l.get("mode") == "bike" else l.get("line") or f"버스{l.get('route')}",
                 l["from"], l["to"]) for l in x["legs"]]
               for x in cs if cr in x["criteria"]]
        if not any([tuple(g) for g in gl] == want for gl in got):
            miss.append((c["id"], f"{cr} 구간열 {want}", str(got)))
            print(f"  >> MISS {cr} 구간열 기대 {want} / 실제 {got}")
    for cr, at in (c.get("expect_candidate_arrive") or {}).items():
        got = [fmt_min(x["arrive_min"]) for x in cs if cr in x["criteria"]]
        if fmt_min(to_service_min(at)) not in got:
            miss.append((c["id"], f"{cr} 도착 {at}", str(got)))
            print(f"  >> MISS {cr} 도착 기대 {at} / 실제 {got}")
    et = c.get("expect_tie")
    if et is not None and bool(r.ties) != et:
        miss.append((c["id"], f"동급 {'있음' if et else '없음'}", f"{len(r.ties or [])}쌍"))
        print(f"  >> MISS 동급 {'있음' if et else '없음'} 기대 / 실제 {len(r.ties or [])}쌍")
    for ax in c.get("expect_tie_axes") or []:
        if not any(ax in t["axes"] for t in (r.ties or [])):
            miss.append((c["id"], f"동급 이유 축 '{ax}'", str([list(t['axes']) for t in (r.ties or [])])))
            print(f"  >> MISS 동급 이유 축 '{ax}' 가 없다")
    fm = c.get("expect_feasible_max")
    nf = sum(1 for x in cs if x["verdict"] == "feasible")
    if fm is not None and nf > fm:
        miss.append((c["id"], f"성립 후보 {fm}개 이하", f"{nf}개"))
        print(f"  >> MISS 성립 후보 {fm}개 이하 기대 / 실제 {nf}개")
    # 87 — 혼합 후보 축: 실린 혼합 수 하한·상한 · 뺀 후보 이유에 들어 있어야 할 말
    nm_ = sum(1 for x in cs if any(cr.startswith("혼합") for cr in x["criteria"]))
    if c.get("expect_mixed_min") is not None and nm_ < c["expect_mixed_min"]:
        miss.append((c["id"], f"혼합 후보 {c['expect_mixed_min']}개 이상", f"{nm_}개"))
        print(f"  >> MISS 혼합 후보 {c['expect_mixed_min']}개 이상 기대 / 실제 {nm_}개")
    if c.get("expect_mixed_max") is not None and nm_ > c["expect_mixed_max"]:
        miss.append((c["id"], f"혼합 후보 {c['expect_mixed_max']}개 이하", f"{nm_}개"))
        print(f"  >> MISS 혼합 후보 {c['expect_mixed_max']}개 이하 기대 / 실제 {nm_}개")
    for w in c.get("expect_dropped_why") or []:
        whys = [d.get("why") or "" for d in (r.dropped_candidates or [])]
        if not any(w in x for x in whys):
            miss.append((c["id"], f"뺀 후보 이유 '{w}'", str(whys)[:200]))
            print(f"  >> MISS 뺀 후보 이유에 '{w}' 가 없다 — {str(whys)[:200]}")
    nl = c.get("expect_no_line_feasible")
    if nl:
        bad = [x["label"] for x in cs if x["verdict"] == "feasible"
               and any(l.get("line") == nl for l in x["legs"])]
        if bad:
            miss.append((c["id"], f"{nl} 후보 성립 없음", str(bad)))
            print(f"  >> MISS {nl} 을 쓰는 후보가 성립했다 — {bad}")
    # ★ 등급 · 경고 부재 · 자동차 구간 축(2026-09-20 · 21번 방).
    #   expect_warn_absent 는 「없어야 한다」 축 — 골목 100% 구간에 class 경고가 섞이면 안 된다(CAR-06).
    eg = c.get("expect_grade")
    if eg and r.grade != eg:
        miss.append((c["id"], f"등급 {eg}", r.grade))
        print(f"  >> MISS 등급 기대 {eg} / 판정 {r.grade}")
    wa = c.get("expect_warn_absent")
    if wa:
        got = {w["code"] for w in (r.warnings or [])}
        for al in (r.alternatives or []):
            got |= {w["code"] for w in (al.get("warnings") or [])}
        bad = [x for x in wa if x in got]
        if bad:
            miss.append((c["id"], f"경고 없음 {wa}", f"있음 {bad}"))
            print(f"  >> MISS 경고 {bad} 가 없어야 하는데 있다")
    etl = c.get("expect_taxi_leg")
    if etl:
        cl = next((l.car for l in r.legs if getattr(l, "car", None)), None) or {}
        cov = cl.get("coverage_pct") or {}
        checks = [("fare_won", cl.get("fare_won"), lambda a, b: a == b),
                  ("night_rate", cl.get("night_rate"), lambda a, b: a == b),
                  ("slow_s", cl.get("slow_s"), lambda a, b: a == b),
                  ("day_type", cl.get("day_type"), lambda a, b: a == b),
                  ("coverage_class_pct_min", cov.get("class"), lambda a, b: a is not None and a >= b),
                  ("coverage_default_pct_min", cov.get("default"), lambda a, b: a is not None and a >= b),
                  ("coverage_default_pct_max", cov.get("default"), lambda a, b: a is not None and a <= b)]
        for k, gotv, fn in checks:
            if k in etl and not fn(gotv, etl[k]):
                miss.append((c["id"], f"자동차 구간 {k} {etl[k]}", str(gotv)))
                print(f"  >> MISS 자동차 구간 {k} 기대 {etl[k]} / 실제 {gotv}")
    # ★ 택시 대안 축(2026-09-20 · 21번 방). verdict · arrive · fare_won(정확) · fare_min/max_won(범위) · warn_codes.
    #   ☆99(2026-10-04) 「라우터 없음이면 SKIP」(--allow-router-down)을 없앴다 — 차도 그래프가 저장소 안에 있어 어느 기기에서나
    #   값이 나온다. 그래프 없이 돌리면 택시가 근거없음이라 MISS 다(그게 맞다). expect_taxi: {"verdict": "unknown"} 는
    #   「택시도 못 낸다」를 잠근다. 돌려주는 skipped 는 늘 빈 목록(부르는 쪽 모양 유지).
    et = c.get("expect_taxi")
    if et is not None:
        tx = r.taxi or {}
        tmiss = []
        if not tx:
            tmiss.append(("택시 대안", "없음"))
        if et.get("verdict") and tx.get("verdict") != et["verdict"]:
            tmiss.append((f"택시 {MARK[et['verdict']]}", MARK.get(tx.get("verdict"), "없음")))
        if et.get("arrive") and fmt_min(to_service_min(et["arrive"])) != fmt_min(tx.get("arrive_min")):
            tmiss.append((f"택시 도착 {et['arrive']}", fmt_min(tx.get("arrive_min"))))
        fw = tx.get("fare_won")
        if et.get("fare_won") is not None and fw != et["fare_won"]:
            tmiss.append((f"택시 요금 {et['fare_won']:,}", str(fw)))
        if et.get("fare_min_won") is not None and (fw is None or fw < et["fare_min_won"]):
            tmiss.append((f"택시 요금 ≥ {et['fare_min_won']:,}", str(fw)))
        if et.get("fare_max_won") is not None and (fw is None or fw > et["fare_max_won"]):
            tmiss.append((f"택시 요금 ≤ {et['fare_max_won']:,}", str(fw)))
        if et.get("grade") and tx.get("grade") != et["grade"]:
            tmiss.append((f"택시 등급 {et['grade']}", str(tx.get("grade"))))
        got_w = {w["code"] for w in (tx.get("warnings") or [])}
        lack = [x for x in (et.get("warn_codes") or []) if x not in got_w]
        if lack:
            tmiss.append((f"택시 경고 {lack}", str(sorted(got_w))))
        for e_, g_ in tmiss:
            miss.append((c["id"], e_, g_))
            print(f"  >> MISS {e_} 기대 / 실제 {g_}")
    return miss, skipped


def main():
    from . import paths as _paths_cli
    _paths_cli.load_cli_env()           # #48 — 명령줄은 저장소 맨 위 .env 의 DATA_DIR 을 쓴다(서버는 configure)
    ap = argparse.ArgumentParser(description="이동 모듈 시각 검증기 v2 (지하철)")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--timetable")
    ap.add_argument("--order")
    ap.add_argument("--transfer-walk")
    ap.add_argument("--bus-route")
    ap.add_argument("--bus-stops")
    ap.add_argument("--station-coords")
    ap.add_argument("--station-exits")
    ap.add_argument("--bike-stations", help="따릉이 운영 대여소 jsonl (v0.7 · 22번 방). 없으면 자전거는 근거없음")
    ap.add_argument("--bike-fixture", help="자전거·대여소 도보 경로의 거리·시간 요약 픽스처 json — 길찾기 없이 회귀를 돌릴 때(형상 없음)")
    ap.add_argument("--bike-record", help="길찾기가 낸 자전거 경로의 거리·시간 요약을 이 픽스처 파일에 **추가** 기록한다(형상 없음)")
    ap.add_argument("--bike-live", default="none",
                    help="실시간 거치 조회: none(기본 · 근거없음) · env(ACOP_SEOUL_OPENAPI_KEY 로 실제 호출) · <픽스처 json 경로>")
    ap.add_argument("--bus-profile", help="버스 구간 통행시간 프로파일(v0.9 · 41번 방) · 'none' 이면 종전 모델(거리 ÷ 표정속도). "
                                         "기본 processed/mobility/bus_seg_profile_v1.jsonl.gz")
    ap.add_argument("--congestion", nargs="*",
                    help="혼잡도 jsonl(v0.8 @ 부품). 기본 processed/mobility/congestion_v1.jsonl + congestion_line9_v1.jsonl · 'none' 이면 안 읽는다")
    ap.add_argument("--rules", default=str(RULES_DIR / "rules_v0.3.json"))
    ap.add_argument("--holidays", default=str(RULES_DIR / "holidays_2026_2027.json"))
    ap.add_argument("--case", help="이 id 만 돌린다")
    ap.add_argument("--graph-dir", help="도로망 그래프 자료 폴더(기본 processed/mobility/graph)")
    ap.add_argument("--road-graph",
                    help="택시·자동차·자전거 경로 — 파이썬 길찾기(서버 없음): auto(자료 폴더의 road_graph_v2) · "
                         "none · <폴더> · fixture:<합성경로 파일>(시험 대역). 안 주면 auto")
    ap.add_argument("--check-expect", action="store_true", help="expect 와 대조하고 MISS 면 종료코드 1")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", help="판정 결과를 이 경로에 저장")
    args = ap.parse_args()

    cases = load_cases(args.cases, args.case)
    v, ctx = build_verifier_for_cases(args, cases)
    record, bike_router, bike_live, rules = ctx["record"], ctx["bike_router"], ctx["bike_live"], ctx["rules"]
    results, miss, skipped = [], [], []
    for c in cases:
        r = v.verify_case(c)
        results.append(r)
        show(c, r, args.verbose)
        if args.check_expect:
            m_, s_ = check_expect(c, r)
            miss += m_
            skipped += s_

    tally = collections.Counter(r.verdict for r in results)
    otally = collections.Counter((r.out or {}).get("verdict") for r in results)
    ctally = collections.Counter((r.out or {}).get("code") for r in results if (r.out or {}).get("code"))
    print("\n" + "─" * 60)
    print("판정(내부) " + " · ".join(f"{MARK[k]} {tally[k]}" for k in VERDICTS if tally[k]))
    print("판정(밖)   " + " · ".join(f"{OUT_MARK[k]} {otally[k]}" for k in OUT_VERDICTS if otally[k])
          + (" · 이유 " + " ".join(f"{k}:{n}" for k, n in ctally.most_common()) if ctally else ""))
    if args.check_expect:
        print(f"기대 대조 — 케이스 {len(cases)}건 중 어긋남 {len(miss)}건")
        for i, e, g in miss:
            print(f"  MISS {i}: 기대 {e} → {g}")
    if record is not None and bike_router is not None:
        Path(args.bike_record).write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"자전거 경로 요약 픽스처 {len(record)}건 → {args.bike_record} (호출 {bike_router.calls}회 · 형상 없음)")
    if bike_live is not None and bike_live.key:
        print(f"bikeList 호출 {bike_live.calls}회 (1일 한도 {rules['bike']['ddareungi']['live_check']['daily_quota']})")
    if args.json:
        Path(args.json).write_text(json.dumps(
            [r.__dict__ for r in results], ensure_ascii=False, default=lambda o: o.__dict__,
            indent=1), encoding="utf-8")
        print(f"판정 결과 → {args.json}")
    sys.exit(1 if miss else 0)
