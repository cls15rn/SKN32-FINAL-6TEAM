# -*- coding: utf-8 -*-
"""booking · offers — 열차 · 고속버스 · 시외버스 운행 비교. `[2026-10-14]` 예약 모듈 확장(항공 → 기차 · 버스).

에이전트(예약 모듈 `instances/booking/`) → 이 모듈 → TAGO 어댑터(`ports/data_sources/tago_transit.py`) → 결과. 항공 비교(`flights.py`)와 같은 자리.

- 고객이 말한 곳 이름(「서울」 「부산」 「동대구」 「광주」)을 **역 · 터미널**로 옮긴다 — 열차는 역 사전(`STATIONS`: 이름 → 역 이름 · 시 · 도)으로
  시 · 도의 역 목록에서 찾고, 버스는 공급자 터미널 목록을 이름 조각으로 찾는다. 「서울」처럼 터미널이 여럿이면(서울경부 · 서울호남 · 동서울)
  행선지 권역의 기본 터미널부터 묻고 운행이 있으면 멈춘다(`_prefer`, 10-15) — 어느 터미널인지는 결과 줄과 `terminals` 에 적는다.
- 버스는 고속을 먼저 보고, 고객이 「시외」라고 했거나 고속에 결과가 없을 때 시외를 본다(호출 수를 아끼려고 — 하루 한도 `rate_tago_per_day`).
- 돌려주는 모양은 항공과 같은 골격 — `options`(운행 하나씩, 출발 시각순) · `sources`(`tago` 의 status · count · seconds · 부른 횟수) ·
  `resolved`(출발 · 도착으로 쓴 역 · 터미널 이름) · `elsewhere`(예매 사이트 첫 화면 링크) · `confirmed_at`. 운행마다 `links` — 조건을 채워
  여는 예매 주소(SRT · 트립닷컴 · 버스타고, `transit_links.py`) — 가 붙고, 없으면 빈 목록이다.
- `[2026-10-15]` 열차로 서울역 ↔ 인천공항(1 · 2터미널)이면 **AREX 직통**(`arex.py`) — TAGO 를 부르지 않고 요일 시간표 파일에서 답한다.
  결과에 `timetable`(평일 · 휴일 중 어느 표인지 · 근거 · 수집일)이 붙고, 운행의 `kind` 는 `arex`, 요금 · 열차 번호는 없다.
- 예매 · 결제는 하지 않는다. 요금은 공급자의 어른 1인 값 그대로(조회 시점 참고값).
"""
from __future__ import annotations

from datetime import UTC, datetime
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.domains.travel_ops.components.booking.offers import arex
from app.domains.travel_ops.components.booking.offers.transit_links import run_links

#: 고객이 말하는 이름 → (열차 역 이름, 시 · 도). 역 목록에서 「역 이름으로 시작」하는 것을 고른다(「울산(통도사)」 · 「김천(구미)」).
#: 도시 이름은 그 도시의 고속철 역으로 — 대구 → 동대구 · 광주 → 광주송정 · 창원 → 창원중앙 · 여수 → 여수엑스포 · 청주 → 오송(우리가 고른 값).
STATIONS: dict[str, tuple[str, str]] = {
    "서울": ("서울", "서울"), "서울역": ("서울", "서울"), "용산": ("용산", "서울"), "수서": ("수서", "서울"), "청량리": ("청량리", "서울"),
    "영등포": ("영등포", "서울"), "행신": ("행신", "경기"), "광명": ("광명", "경기"), "수원": ("수원", "경기"), "평택": ("평택", "경기"),
    "천안아산": ("천안아산", "충남"), "천안": ("천안", "충남"), "아산": ("천안아산", "충남"), "오송": ("오송", "충북"), "청주": ("오송", "충북"),
    "대전": ("대전", "대전"), "서대전": ("서대전", "대전"), "김천구미": ("김천(구미)", "경북"), "김천": ("김천", "경북"), "구미": ("구미", "경북"),
    "동대구": ("동대구", "대구"), "대구": ("동대구", "대구"), "경산": ("경산", "경북"), "경주": ("경주", "경북"), "신경주": ("경주", "경북"),
    "포항": ("포항", "경북"), "안동": ("안동", "경북"), "영주": ("영주", "경북"), "울산": ("울산(통도사)", "울산"), "통도사": ("울산(통도사)", "울산"),
    "부산": ("부산", "부산"), "부산역": ("부산", "부산"), "구포": ("구포", "부산"), "밀양": ("밀양", "경남"), "마산": ("마산", "경남"),
    "창원": ("창원중앙", "경남"), "창원중앙": ("창원중앙", "경남"), "진주": ("진주", "경남"), "익산": ("익산", "전북"), "전주": ("전주", "전북"),
    "정읍": ("정읍", "전북"), "남원": ("남원", "전북"), "광주송정": ("광주송정", "광주"), "광주": ("광주송정", "광주"), "나주": ("나주", "전남"),
    "목포": ("목포", "전남"), "순천": ("순천", "전남"), "여수": ("여수엑스포", "전남"), "여수엑스포": ("여수엑스포", "전남"),
    "강릉": ("강릉", "강원"), "춘천": ("춘천", "강원"), "원주": ("원주", "강원"), "만종": ("만종", "강원"), "평창": ("평창", "강원"),
    "진부": ("진부(오대산)", "강원"), "동해": ("동해", "강원"), "제천": ("제천", "충북"), "충주": ("충주", "충북"), "논산": ("논산", "충남"),
    "공주": ("공주", "충남"), "인천공항": ("인천공항T1", "인천"), "검암": ("검암", "인천"),
    # `[2026-10-15]` 공항철도 직통(`arex.py`)이 받는 이름 — TAGO 기차에는 운행이 없어(0편) 이 이름들은 AREX 시간표로 간다
    "인천공항T1": ("인천공항T1", "인천"), "인천공항1터미널": ("인천공항T1", "인천"), "인천공항 1터미널": ("인천공항T1", "인천"),
    "인천공항T2": ("인천공항T2", "인천"), "인천공항2터미널": ("인천공항T2", "인천"), "인천공항 2터미널": ("인천공항T2", "인천"),
}

#: 버스 터미널 조각 — 고객 말 → 공급자 터미널 이름에서 찾을 조각. 없으면 말한 이름 그대로 찾는다
TERMINAL_HINT: dict[str, str] = {"서울역": "서울", "부산역": "부산", "동대구": "대구", "광주송정": "광주", "창원중앙": "창원", "여수엑스포": "여수"}

#: 「서울」 같은 이름에 터미널이 여럿일 때 물어볼 최대 수(출발 × 도착 조합이 호출 수)
TERMINALS_EACH = 3

#: `[2026-10-15 고도화]` 터미널이 여럿인 출발지(서울)는 **행선지 권역으로 기본 터미널을 먼저** 묻고, 운행이 있으면 거기서 멈춘다(호출 10 → 3).
#: 권역은 역 사전(`STATIONS`)의 시 · 도 — 사전에 없는 곳은 기본 순서. 고객이 터미널 이름을 말하면(「동서울에서」) 이 규칙은 안 탄다(그 터미널만 찾힌다).
#: 순서는 우리가 정한 값(한국 고속 · 시외 노선망 상식) — 틀린 권역이면 다음 터미널로 넘어가므로 답이 비는 일은 없고 호출만 는다.
_HONAM = {"광주", "전남", "전북"}
_GANGWON = {"강원"}
_SOUTH_SUBURBS = {"충남", "경남", "전북", "전남", "광주"}            # 시외: 서울남부터미널이 맡는 쪽(충남 서해안 · 경남 서부 · 호남)
_TERMINAL_ORDER: dict[str, dict[str, tuple[str, ...]]] = {
    "express": {"honam": ("서울호남", "센트럴", "동서울", "서울경부"), "gangwon": ("동서울", "서울경부", "서울호남"),
                "default": ("서울경부", "동서울", "서울호남", "센트럴")},
    "suburbs": {"south": ("서울남부", "동서울", "서울경부"), "default": ("동서울", "서울남부", "서울경부")},
}


def _region(said: str) -> str | None:
    entry = STATIONS.get(said) or STATIONS.get(said.replace("역", ""))
    return entry[1] if entry else None


def _prefer(kind: str, terminals: list[dict[str, Any]], destination: str) -> list[dict[str, Any]]:
    """출발 터미널 후보를 행선지 권역의 기본 터미널 순으로 — 이름에 순서표의 조각이 든 것을 앞으로, 나머지는 원래 순서."""
    if len(terminals) < 2:
        return terminals
    region = _region(destination)
    table = _TERMINAL_ORDER[kind]
    if kind == "express":
        order = table["honam"] if region in _HONAM else table["gangwon"] if region in _GANGWON else table["default"]
    else:
        order = table["south"] if region in _SOUTH_SUBURBS else table["default"]
    rank = {t["name"]: next((i for i, piece in enumerate(order) if piece in t["name"]), len(order)) for t in terminals}
    return sorted(terminals, key=lambda t: rank[t["name"]])
#: 버스 종류(고속 · 시외)마다 운행 조회를 부를 최대 수 — `[10-15]` 전체 8 에서 종류별 4 로: 고속이 전부 0편이면(서울→광주 10-16 실호출)
#: 예산을 다 써 시외는 묻지도 못했다. 기본 터미널 순서(`_prefer`)라 앞 조합이 가장 그럴듯하다.
MAX_SCHEDULE_CALLS = 4

#: 예매 사이트 **첫 화면** — 조건을 채워 여는 주소는 `transit_links`(SRT · 트립닷컴 · 버스타고)에 있고, 여기는 그것이 없을 때의 대비.
#: `[2026-10-14 저녁]` 코레일은 `letskorail.com` 이 `korail.com` 으로 넘어가고(302), 버스타고 루트는 404 라 주소를 바꿨다(직접 열어 봄).
BOOKING_SITES = {
    "train": [{"name": "코레일 승차권 예매(KTX · ITX · 무궁화)", "url": "https://www.korail.com/ticket/main"},
              {"name": "SRT 예매", "url": "https://etk.srail.kr/"}],
    "express": [{"name": "코버스 고속버스 예매", "url": "https://www.kobus.co.kr/"}],
    "suburbs": [{"name": "버스타고 시외버스 예매", "url": "https://www.bustago.or.kr/newweb/kr/index.do"},
                {"name": "티머니 시외버스 예매", "url": "https://txbus.t-money.co.kr/"}],
}


#: `[2026-10-15]` 서울 ↔ 인천공항 버스(공항리무진) 안내 — 운행표 · 예매 대신 노선 · 시간표 페이지. 공항리무진 공지(2026-04, 10-15 확인):
#: 서울 → 공항은 「온라인을 포함한 사전 예약제가 없음」(선착순, 교통카드 · 현금), 공항 → 서울은 매표소 · 무인발권기. 일부 노선만 앱 예매(노선마다 다름)
AIRPORT_BUS_SITES = [{"name": "공항리무진 노선 · 시간표", "url": "https://www.airportlimousine.co.kr/sub/sub01.php"}]


FAILED = object()      # 어댑터가 못 불렀다(장애 · 한도) — 「이름을 못 찾음」과 가른다(검토 10-14)


def _station(adapter: Any, said: str, calls: list[str]) -> dict[str, Any] | None | object:
    """말한 이름 → 역 {id, name}. 사전에 없거나 목록에 없으면 `None`, 어댑터가 못 불렀으면 `FAILED`."""
    entry = STATIONS.get(said) or STATIONS.get(said.replace("역", "")) or STATIONS.get(said + "역")
    if entry is None:
        return None
    wanted, city = entry
    calls.append(f"train_stations:{city}")
    stations = adapter.train_stations(city)
    if stations is None:
        return FAILED
    exact = [s for s in stations if s["name"] == wanted]
    starts = [s for s in stations if s["name"].startswith(wanted)]
    holds = [s for s in stations if wanted in s["name"]]
    for group in (exact, starts, holds):
        if group:
            return group[0]
    return None


#: `[2026-10-15]` 도시 이름으로 터미널을 찾을 때, 이름 끝 괄호에 **다른 곳**이 붙은 터미널은 그 도시 출발로 보지 않는다 — 실호출(10-15 09:57,
#: 서울→인천공항 시외)에서 「동서울(홍천)」이 서울 출발 후보 셋 중 하나를 차지했다. 「센트럴시티(서울)」처럼 괄호가 그 도시면 남긴다.
#: 도시 이름만 — 「부산동부(노포)」(노포는 부산 안)처럼 괄호가 동네인 곳이 있어 모든 이름에 걸지 않는다.
_CITY_KEYWORDS = {"서울"}
_TAIL = re.compile(r"\(([^)]*)\)\s*$")


def _elsewhere(name: str, keyword: str) -> bool:
    tail = _TAIL.search(name)
    return tail is not None and keyword not in tail.group(1)


def _terminals(adapter: Any, kind: str, said: str, calls: list[str]) -> list[dict[str, Any]] | object:
    keyword = TERMINAL_HINT.get(said, said)
    calls.append(f"bus_terminals:{kind}:{keyword}")
    found = adapter.bus_terminals(kind, keyword)
    if found is None:
        return FAILED
    if keyword in _CITY_KEYWORDS:
        found = [t for t in found if not _elsewhere(t["name"], keyword)]
    exact = [t for t in found if t["name"] == keyword]
    starts = [t for t in found if t["name"].startswith(keyword) and t not in exact]
    rest = [t for t in found if t not in exact and t not in starts]
    return exact + starts + rest                           # 자르기는 부르는 쪽이(`_prefer` 뒤에 `TERMINALS_EACH`) — 동서울이 잘리지 않게(10-15)


#: 버스 운행은 공급자(TAGO)가 가까운 날만 준다 — 10-14 저녁 관찰: 10-16 까지 있고 10-17 부터 0편(팀장 점검 LB-03). 이 날 수 밖인데 0편이면
#: 「운행 없음」이 아니라 「아직 안 줌」일 수 있어, 오늘 같은 구간에 운행이 있는지 한 번 더 물어 가른다(호출 1회).
BUS_WINDOW_DAYS = 1


def _beyond_window(depart_date: str) -> bool:
    try:
        wanted = datetime.strptime(depart_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (wanted - datetime.now(ZoneInfo("Asia/Seoul")).date()).days > BUS_WINDOW_DAYS


def _runs_today(adapter: Any, kinds: list[str], pair: tuple[str, dict[str, Any], dict[str, Any]] | None, calls: list[str]) -> bool:
    """같은 터미널 쌍을 오늘 날짜로 한 번 — 오늘은 있는데 그날은 없으면 공급자가 아직 안 준 것이다."""
    if pair is None:
        return False
    bus_kind, dep, arr = pair
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    calls.append(f"bus_schedules:{bus_kind}:{dep['name']}→{arr['name']}:today")
    rows = adapter.bus_schedules(bus_kind, dep_id=dep["id"], arr_id=arr["id"], date=today)
    return bool(rows)


def _sort_key(option: dict[str, Any]) -> tuple[str, str]:
    return (option.get("date") or "", option.get("depart") or "99:99")


def search_transit(travel: Any, *, mode: str, origin: str, destination: str, depart_date: str,
                   adults: int | None = None, kind: str | None = None) -> dict[str, Any]:
    """열차(`mode="train"`) 또는 버스(`mode="bus"`) 운행 비교 한 번(편도 한 날). 오는 편은 부르는 쪽이 날짜 · 방향을 바꿔 한 번 더 부른다.

    `kind` 는 버스일 때 고객이 말한 종류(express · suburbs) — 없으면 고속 먼저, 없으면 시외.
    """
    adapter = getattr(travel, "transit_schedule", None) if travel is not None else None
    started = time.perf_counter()
    calls: list[str] = []
    result: dict[str, Any] = {
        "mode": mode, "query": {"origin": origin, "destination": destination, "depart_date": depart_date, "adults": adults, "kind": kind},
        "options": [], "resolved": {"origin": None, "destination": None}, "sources": {}, "elsewhere": [],
        "confirmed_at": datetime.now(UTC).isoformat()}
    if mode == "train" and arex.pair(origin, destination):
        # `[2026-10-15]` 서울역 ↔ 인천공항은 AREX 직통 — TAGO 기차에 없어(0편) 시간표 파일에서 답한다(공급자 호출 0). 상태 칸 이름은 그대로 `tago`
        #   (팀 · 스크립트가 읽는 자리) — 어디서 왔는지는 `timetable` · 운행의 `source` 가 말한다
        got = arex.runs(origin, destination, depart_date, holiday=getattr(travel, "holiday", None) if travel is not None else None)
        options = (got or {}).get("options") or []
        revised = bool(got and got["timetable"].get("revised"))
        result.update({"options": options, "resolved": (got or {}).get("resolved") or result["resolved"],
                       "elsewhere": [dict(arex.SITE), *([dict(arex.NOTICE)] if revised else [])],
                       **({"timetable": got["timetable"]} if got else {})})
        result["sources"]["tago"] = {"status": "revised" if revised else "ok" if got else "unresolved", "count": len(options), "calls": [],
                                     "seconds": round(time.perf_counter() - started, 1), "via": "arex_timetable",
                                     **({} if got else {"unresolved": [origin, destination]})}
        return result
    if mode == "bus" and arex.pair(origin, destination):
        # `[2026-10-15]` 서울 ↔ 인천공항 버스는 공항리무진(공항버스) — TAGO 고속 · 시외에 운행이 없다(10-15 실호출: 고속 서울경부 · 동서울 → T1 · T2,
        #   시외 동서울 → 인천공항2터미널 · 인천공항행 전부 0편, 13회). 부르지 않고 안내로 답한다(`airport_bus`). 지방 ↔ 인천공항 시외는 그대로 묻는다
        result["elsewhere"] = [dict(site) for site in AIRPORT_BUS_SITES]
        result["sources"]["tago"] = {"status": "airport_bus", "count": 0, "calls": [], "seconds": round(time.perf_counter() - started, 1),
                                     "via": "airport_bus_guide"}
        return result
    if adapter is None:
        result["sources"]["tago"] = {"status": "failed", "reason": "transit_schedule 소스 없음(ACOP_TAGO_API_KEY)"}
        return result
    options: list[dict[str, Any]] = []
    status = "ok"
    unresolved: list[str] = []
    if mode == "train":
        dep, arr = _station(adapter, origin, calls), _station(adapter, destination, calls)
        if dep is FAILED or arr is FAILED:
            status = "failed"
        elif dep is None or arr is None:
            unresolved = [name for name, found in ((origin, dep), (destination, arr)) if found is None]
            status = "unresolved"
        else:
            result["resolved"] = {"origin": dep["name"], "destination": arr["name"]}
            calls.append("train_schedules")
            rows = adapter.train_schedules(dep_id=dep["id"], arr_id=arr["id"], date=depart_date)
            if rows is None:
                status = "failed"
            else:
                options = rows
        result["elsewhere"] = list(BOOKING_SITES["train"])
    else:
        kinds = [kind] if kind in ("express", "suburbs") else ["express", "suburbs"]
        used: list[str] = []
        first_pair: tuple[str, dict[str, Any], dict[str, Any]] | None = None
        broken = paired = False
        for bus_kind in kinds:
            deps, arrs = _terminals(adapter, bus_kind, origin, calls), _terminals(adapter, bus_kind, destination, calls)
            if deps is FAILED or arrs is FAILED:
                broken = True
                continue
            if not deps or not arrs:
                if not unresolved:
                    unresolved = [name for name, found in ((origin, deps), (destination, arrs)) if not found]
                continue
            paired = True
            deps, arrs = _prefer(bus_kind, deps, destination)[:TERMINALS_EACH], arrs[:TERMINALS_EACH]
            first_pair = first_pair or (bus_kind, deps[0], arrs[0])
            if len(deps) > 1:
                result["terminals"] = {"origin": [t["name"] for t in deps], "rule": _region(destination) or "default"}
            got_any = False
            for dep in deps:
                for arr in arrs:
                    if len([c for c in calls if c.startswith(f"bus_schedules:{bus_kind}:")]) >= MAX_SCHEDULE_CALLS:
                        break
                    calls.append(f"bus_schedules:{bus_kind}:{dep['name']}→{arr['name']}")
                    rows = adapter.bus_schedules(bus_kind, dep_id=dep["id"], arr_id=arr["id"], date=depart_date)
                    if rows is None:
                        status = "failed" if not options else status
                        continue
                    got_any = got_any or bool(rows)
                    options.extend(rows)
                if got_any:
                    break                                  # 기본 터미널에 운행이 있으면 다른 출발 터미널은 안 묻는다(`[10-15]` 호출 아끼기)
            if got_any:
                used.append(bus_kind)
                unresolved = []
                if kind is None:
                    break                                  # 고속에 있으면 시외는 안 본다(호출 아끼기)
        if options:
            status = "ok"
            names_dep = sorted({option["origin"] for option in options if option.get("origin")})
            names_arr = sorted({option["destination"] for option in options if option.get("destination")})
            result["resolved"] = {"origin": " · ".join(names_dep) or None, "destination": " · ".join(names_arr) or None}
        elif broken or status == "failed":
            status = "failed"
        elif unresolved and not paired:
            status = "unresolved"                        # 터미널을 한 쌍도 못 찾았을 때만 「못 찾음」
        elif _beyond_window(depart_date) and _runs_today(adapter, kinds, first_pair, calls):
            status, unresolved = "not_yet", []           # 공급자가 그 날짜를 아직 안 준다(팀장 점검 LB-03) — 「운행 없음」과 가른다
        else:
            status, unresolved = "ok", []                 # 터미널은 찾았는데 그날 운행이 없다
        result["elsewhere"] = [site for bus_kind in (used or kinds) for site in BOOKING_SITES[bus_kind]]
    options.sort(key=_sort_key)
    for option in options:                                 # 운행마다 조건을 채워 여는 예매 링크(없으면 빈 목록 → 첫 화면 링크)
        option["links"] = run_links(option, adults=adults)
    result["options"] = options
    result["sources"]["tago"] = {"status": status, "count": len(options), "calls": calls,
                                 "seconds": round(time.perf_counter() - started, 1),
                                 **({"unresolved": unresolved} if unresolved else {})}
    return result


__all__ = ["BOOKING_SITES", "STATIONS", "TERMINALS_EACH", "search_transit"]
