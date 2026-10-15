# -*- coding: utf-8 -*-
"""TAGO 열차 · 고속버스 · 시외버스 **실제 호출** 탐침 — 예약 모듈 확장이 쓰는 동작 이름 · 응답 칸이 맞는지 본다. `[2026-10-14]`

주소는 2026-03 개편판(`…/1613000/<이름>/Get<동작>`)이고 도시 코드 목록 셋만 13:45 에 확인했다. 역 목록 · 터미널 목록 · 운행 조회 동작 이름은
같은 규칙으로 옮긴 **추정**이라 이 스크립트로 확인한다(「확인 안 함」을 지우는 근거). 응답은 저장하지 않는다 — 숫자 · 이름만 출력.

실행 위치: final_project_cs (키는 .env.apikeys 의 ACOP_TAGO_API_KEY 또는 공통 키 — 값은 출력하지 않는다)
  python -m scripts.probe_tago_transit                          # 서울→부산 기차 · 서울→부산 고속 · 서울→강릉 시외, 날짜는 오늘+2
  python -m scripts.probe_tago_transit 2026-11-06 서울 부산      # 날짜 · 출발 · 도착 지정
  python -m scripts.probe_tago_transit --module                 # 어댑터 대신 booking 모듈(search_transit)로 — 팀이 보는 모양 그대로
  python -m scripts.probe_tago_transit --keys                   # 운행 응답 첫 줄의 칸 이름 · 자릿수만(값 없음) — 시각 칸 모양 확인용

호출 수: 기본 실행 기준 열차 3(도시 · 역 2) + 1(운행) · 고속 2(터미널) + ≤6(운행) · 시외 2 + ≤6 — 하루 한도(rate_tago_per_day 1000) 안.
"""
import platform
import sys
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.core.settings import get_settings
from app.domains.travel_ops.ports.data_sources.tago_transit import EXPRESS, SUBURBS, TRAIN, TagoTransit


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_module = "--module" in sys.argv
    day = args[0] if args else (date.today() + timedelta(days=2)).isoformat()
    origin, destination = (args[1], args[2]) if len(args) >= 3 else ("서울", "부산")
    settings = get_settings()
    key = settings.public_data_key(getattr(settings, "tago_api_key", "") or "")
    if not key:
        print("TAGO 키가 없어 멈춤(ACOP_TAGO_API_KEY 또는 공통 키)")
        return
    tago = TagoTransit(service_key=key)
    print(f"기기 {platform.node()} · 시각 {datetime.now():%Y-%m-%d %H:%M} · {origin} → {destination} · {day}")

    if "--keys" in sys.argv:
        stamp = day.replace("-", "")
        print("[열차]", tago.shape(TRAIN, "GetStrtpntAlocFndTrainInfo", {"depPlaceId": "NAT010000", "arrPlaceId": "NAT014445", "depPlandTime": stamp}))
        print("[고속]", tago.shape(EXPRESS, "GetStrtpntAlocFndExpbusInfo", {"depTerminalId": "NAEK010", "arrTerminalId": "NAEK700", "depPlandTime": stamp}))
        print("[시외]", tago.shape(SUBURBS, "GetStrtpntAlocFndSuberbsBusInfo", {"depTerminalId": "NAI3112001", "arrTerminalId": "NAI0100000", "depPlandTime": stamp}))
        print(f"-- 미스 {dict(tago.misses)}")
        return
    if use_module:
        from app.domains.travel_ops.components.booking.offers import search_transit

        from app.domains.travel_ops.ports.data_sources.incheon_airport_bus import AirportBusInfo

        travel = SimpleNamespace(transit_schedule=tago, airport_bus=AirportBusInfo(service_key=key))   # `[10-15]` 서울 ↔ 인천공항 버스 판
        for mode, kind in (("train", None), ("bus", "suburbs" if "--suburbs" in sys.argv else None)):
            started = time.perf_counter()
            got = search_transit(travel, mode=mode, origin=origin, destination=destination, depart_date=day, adults=2, kind=kind)
            source = got["sources"]["tago"]
            print(f"\n== {mode} · {time.perf_counter() - started:.1f}초 · 상태 {source['status']} · 운행 {source['count']}편 · "
                  f"호출 {len(source['calls'])}회 · 옮긴 곳 {got['resolved']}")
            board = got.get("airport_bus")
            if board:                                                  # `[10-15]` 공항버스 판 — 노선 수 · 앞 노선 3개(번호 · 요금 · 시각 수)만
                print(f"  공항버스: {board['direction']} · {board['terminal']} · {board['day_type']} · 노선 {len(board['routes'])} · 번호 없는 줄 {board['skipped_unnumbered']}")
                for route in board["routes"][:3]:
                    print(f"    {route['number']} · {route.get('price')}원 · 타는 곳 {route.get('ride')} · 시각 {len(route['times'])}개 {route['times'][:3]}")
            for option in got["options"][:6]:
                print(f"  {option.get('grade')} {option.get('number') or ''} {option.get('origin')} {option.get('depart')} → "
                      f"{option.get('destination')} {option.get('arrive')} · {option.get('minutes')}분 · "
                      f"{'어른 ' + format(option['price'], ',') + '원' if isinstance(option.get('price'), int) else '요금 없음(예매 화면)'}"
                      f" · 링크 {[link['site'] for link in option.get('links') or []] or '없음(첫 화면)'}"
                      f"{' · 시각 원값 ' + option['depart_raw'] if option.get('depart_raw') else ''}")   # LB-10 — 못 읽은 줄의 값 모양
            for link in (got["options"][0].get("links") if got["options"] else None) or []:   # 첫 운행의 조건 채운 주소 — 열어 볼 것
                print(f"  → {link['name']}: {link['url']}")
            if source.get("unresolved"):
                print(f"  못 찾은 곳: {source['unresolved']}")
            if not got["options"] or got.get("terminals"):
                print(f"  호출: {source['calls']} · 터미널 규칙: {got.get('terminals')}")   # 0편이거나 터미널이 여럿일 때 어디를 물었는지
        print(f"\n-- 미스 {dict(tago.misses)}")
        return

    # 어댑터 동작 하나씩 — 동작 이름이 틀리면 여기서 None(미스 사유에 NO_OPENAPI_SERVICE)
    started = time.perf_counter()
    cities = tago.train_cities()
    print(f"\n[열차] 도시 {len(cities)}곳 · {time.perf_counter() - started:.1f}초 · 예 {[c['name'] for c in cities[:3]]}")
    lists = {}
    for city in ("서울", "부산"):
        started = time.perf_counter()
        lists[city] = tago.train_stations(city)
        stations = lists[city]
        print(f"  역 목록 {city}: {None if stations is None else len(stations)}곳 · {time.perf_counter() - started:.1f}초"
              + (f" · 예 {[s['name'] for s in stations[:4]]}" if stations else ""))
    dep = next((s for s in (lists["서울"] or []) if s["name"] == "서울"), None)
    arr = next((s for s in (lists["부산"] or []) if s["name"] == "부산"), None)
    if dep and arr:
        started = time.perf_counter()
        rows = tago.train_schedules(dep_id=dep["id"], arr_id=arr["id"], date=day)
        print(f"  운행 서울→부산 {day}: {None if rows is None else len(rows)}편 · {time.perf_counter() - started:.1f}초")
        for row in (rows or [])[:4]:
            print(f"    {row['grade']} {row['number']} {row['depart']} → {row['arrive']} · {row['minutes']}분 · 어른 {row['price']}원")
    for kind, dest in (("express", "부산"), ("suburbs", "강릉")):
        started = time.perf_counter()
        deps, arrs = tago.bus_terminals(kind, "서울"), tago.bus_terminals(kind, dest)
        print(f"\n[{'고속' if kind == 'express' else '시외'}] 터미널 서울 {None if deps is None else len(deps)}곳 "
              f"{[t['name'] for t in (deps or [])[:4]]} · {dest} {None if arrs is None else len(arrs)}곳 {[t['name'] for t in (arrs or [])[:3]]}"
              f" · {time.perf_counter() - started:.1f}초")
        if deps and arrs:
            started = time.perf_counter()
            rows = tago.bus_schedules(kind, dep_id=deps[0]["id"], arr_id=arrs[0]["id"], date=day)
            print(f"  운행 {deps[0]['name']}→{arrs[0]['name']} {day}: {None if rows is None else len(rows)}편 · {time.perf_counter() - started:.1f}초")
            for row in (rows or [])[:4]:
                print(f"    {row['grade']} {row['depart']} → {row['arrive']} · {row['minutes']}분 · 어른 {row['price']}원")
    print(f"\n-- 미스 {dict(tago.misses)}")


if __name__ == "__main__":
    main()
