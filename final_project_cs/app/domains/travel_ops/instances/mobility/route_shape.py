# -*- coding: utf-8 -*-
"""지도에 그릴 **경로선** — 우리 도로 그래프로 직접 계산해 화면에 내려준다(외부 길찾기 API 호출 없음) · 2026-10-04.

왜: 화면은 지금 이동을 구글 지도 **링크**로만 보여 준다(`trip_api.map_view` — 한국에서 구글은 자동차·도보 길찾기를 안 주고, 대중교통은 들를 곳을
받지 않아 선을 못 그렸다). 우리는 지도 원본(OSM)에서 만든 도로 그래프(`engine/graph_router.py`)와 역 좌표가 있어, 같은 계산이 이미 내는 **형상**을
그대로 내려 줄 수 있다. 외부 서비스의 한도·약관·결과 저장 제한에 걸리지 않고, 소요·요금을 낸 계산과 같은 길이다.

무엇을 내나(이동 항목마다 하나)
  mode     walk · bike · taxi · subway · bus · mixed · unknown — 일정이 **계획 수단으로 고른** 후보의 종류
  line     GeoJSON LineString `[[경도, 위도], …]`(점 수는 MAX_POINTS 로 줄인다)
  walks    걷는 구간 `[[처음, 끝], …]` — line 좌표 번호(줄인 뒤 기준). 걷기 이동은 전체, 대중교통은 양 끝 · 갈아타는 걸음 · 2026-10-15
  source   local_road_graph(도로 그래프로 계산) · stations(역 좌표 순서 + 양 끝 걸음은 도로 그래프) · straight_line(두 장소 직선 — 마지막 수단)
  grade    추정(그래프 계산) · 근거없음(직선)  — 확정은 없다(지도 원본 기준이라 실제 보도·횡단·출입구와 다를 수 있다)
  note     왜 그렇게 그렸는지 한 줄(예: 「버스 노선 형상은 없어 직선으로 잇는다」)

대중교통: 지하철은 **탄 역 좌표를 순서대로 잇는다**(실제 선로 곡선이 아니다 — 선로 형상 자료가 없다) + 양 끝 걸음은 도로 그래프 foot 길.
         저장된 승하차 문맥이 있으면 버스는 선택 노선의 정류장을 순서대로 잇고, 지하철은 선택 편성의 경유 방향을 따른다.
         정류장 경유선은 실제 도로 형상이 아니며 선택 문맥이 없는 옛 버스 일정은 직선으로 잇는다.
못 구했을 때(길찾기 꺼짐 · 지도 밖 · 경로 없음)는 직선 + `grade=근거없음` 으로 내리고 이유를 note 에 적는다 — 조용히 빼지 않는다.
"""
from __future__ import annotations

import math
from typing import Any

MAX_POINTS = 240                                  # 기본 응답의 한 선 점 상한 — 축소해 보기에 충분하고 응답이 가볍다
MAX_POINTS_DETAIL = 5000                          # `detail=true`(확대용 상세 선)의 상한 — 도로 그래프 원본 점이 이 안에 든다(8~15 km 택시 333~365점)
SIMPLIFY_TOLERANCE_M = 2.0                        # 점을 줄일 때 허용하는 모양 오차(미터) — ★우리가 고른 값이다. 아래 `_fit` 참고


def _hav(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(d))


def _length(coords) -> float:
    return sum(_hav(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _simplify(coords, tol_m, fixed=()):
    """Douglas–Peucker — 모양을 지키며 점을 줄인다. 원본의 모든 점이 줄인 선에서 `tol_m` 미터 안에 든다. 처음·끝은 남긴다."""
    return [coords[i] for i in _kept(coords, tol_m, fixed)]


def _kept(coords, tol_m, fixed=()) -> list[int]:
    """`_simplify` 가 남기는 점의 번호. `fixed` 번호는 늘 남긴다 — 걸음 구간 경계(`walks`)가 줄인 선에도 그대로 있게."""
    n = len(coords)
    if n <= 2 or tol_m <= 0:
        return list(range(n))
    kx = math.cos(math.radians(sum(c[1] for c in coords) / n)) * 111320.0
    ky = 110574.0
    pts = [((c[0]) * kx, (c[1]) * ky) for c in coords]
    keep = [False] * n
    marks = sorted({0, n - 1, *fixed})
    for m in marks:
        keep[m] = True
    stack = list(zip(marks, marks[1:]))
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = pts[i], pts[j]
        vx, vy = x2 - x1, y2 - y1
        vv = vx * vx + vy * vy
        far, far_d = -1, tol_m
        for k in range(i + 1, j):
            wx, wy = pts[k][0] - x1, pts[k][1] - y1
            t = 0.0 if vv == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / vv))
            d = math.hypot(wx - t * vx, wy - t * vy)
            if d > far_d:
                far, far_d = k, d
        if far >= 0:
            keep[far] = True
            stack.append((i, far))
            stack.append((far, j))
    return [i for i, k in enumerate(keep) if k]


def _fit(coords, limit, *, tol_m=SIMPLIFY_TOLERANCE_M):
    """점이 `limit` 이하가 되도록 **모양을 지키며** 줄인다 — 허용 오차를 `tol_m` 에서 시작해 모자라면 1.5배씩 키운다.

    ★전에는 점을 **고르게 건너뛰었다**(`_thin`). 그러면 모퉁이가 잘려 확대하면 직선처럼 보인다 —
      실측(2026-10-05 · 이 PC 도로 그래프): 택시 8 km 333점→240점에서 최대 8.1 m, 15 km 365점→240점에서 14.0 m 벗어났다.
      Douglas–Peucker 는 같은 점 수에서 최대 오차를 `tol_m` 안으로 묶는다(점 수가 상한을 넘으면 그때만 오차를 키운다)."""
    if len(coords) <= 2:
        return coords
    return [coords[i] for i in _fit_kept(coords, limit, tol_m=tol_m)]


def _fit_kept(coords, limit, *, tol_m=SIMPLIFY_TOLERANCE_M, fixed=()) -> list[int]:
    """`_fit` 이 남기는 점의 번호 — `fixed` 번호는 늘 남긴다(그 수가 상한을 넘으면 더 줄이지 않는다)."""
    out = _kept(coords, tol_m, fixed)
    while len(out) > max(limit, len(set(fixed)) + 2):
        tol_m *= 1.5
        out = _kept(coords, tol_m, fixed)
    return out


def _engine():
    """(원본 라우터 | None, 역 좌표 | None). 이동 계산기가 꺼져 있으면 (None, None)."""
    from . import wiring
    if wiring.mode() != "enabled":
        return None, None
    from .engine import runtime as engine_runtime
    try:
        v = engine_runtime.get_verifier(**wiring._STATE["kw"])._v
    except Exception:                              # noqa: BLE001 — 형상은 보조 정보라 계산기 오류로 화면 전체를 막지 않는다
        return None, None
    br = getattr(v, "bike_router", None)
    return getattr(br, "router", None), getattr(v, "sc", None)


def _line_order():
    """호선별 역 순서(`LineOrder`) | None. 이동 계산기가 꺼져 있으면 None — 그때는 일정에 담긴 역만 잇는다."""
    from . import wiring
    if wiring.mode() != "enabled":
        return None
    from .engine import runtime as engine_runtime
    try:
        return getattr(engine_runtime.get_verifier(**wiring._STATE["kw"])._v, "lo", None)
    except Exception:                              # noqa: BLE001 — 형상은 보조 정보라 계산기 오류로 화면 전체를 막지 않는다
        return None


def _bus_routes():
    """이미 적재된 노선별 정류장 순서. 지도 요청에서 외부 버스 API를 호출하지 않는다."""
    from . import wiring
    if wiring.mode() != "enabled":
        return None
    from .engine import runtime as engine_runtime
    try:
        return getattr(engine_runtime.get_verifier(**wiring._STATE["kw"])._v, "bus", None)
    except Exception:                              # noqa: BLE001 — 보조 형상에 자료가 없으면 이유와 직선을 내린다
        return None


def _route(router, a, b, profile):
    """라우터 형상 [[lng, lat], …] 와 이유. a, b = (lng, lat). 못 구하면 (None, 이유)."""
    if router is None:
        return None, "길찾기가 꺼져 있다"
    try:
        doc = router.route(a, b, profile=profile)
    except Exception as ex:                        # noqa: BLE001 — RouterDown·통신 오류 모두 직선으로 내린다
        return None, f"{type(ex).__name__}: {str(ex)[:80]}"
    paths = (doc or {}).get("paths") or []
    coords = (paths[0].get("points") or {}).get("coordinates") if paths else None
    if not coords or len(coords) < 2:
        return None, "길찾기가 경로를 주지 않았다"
    return [list(c) for c in coords], None


def _kind(option: dict | None) -> str:
    oid = str((option or {}).get("id") or "")
    if oid.startswith("walk"):
        return "walk"
    if oid.startswith("bike"):
        return "bike"
    if oid.startswith("taxi"):
        return "taxi"
    if oid.startswith("subway_bus") or oid.startswith("bus_subway"):
        return "mixed"
    if oid.startswith("subway"):
        return "subway"
    if oid.startswith("bus"):
        return "bus"
    return "unknown"


def _ride_names(lo, line: str, a: str, b: str) -> tuple[list[str], str | None]:
    """한 호선에서 탄 역 a → 내린 역 b 사이의 **모든 역 이름**(양 끝 포함)과 주의 문구. 노선 순서를 못 찾으면 [a, b] 그대로."""
    from .wiring import engine_line
    if lo is None or a == b:
        return [a, b], None
    el = engine_line(line)
    for x, y in ((a, b), (a + "역", b), (a, b + "역"), (a + "역", b + "역")):
        try:
            names = lo.path(el, x, y)
        except Exception:                          # noqa: BLE001 — 노선 순서 자료의 어떤 오류도 형상을 막지 않는다
            names = None
        if names and len(names) >= 2:
            warn = None
            try:
                if lo.is_loop(el):
                    warn = f"{line}은 순환선이라 짧은 쪽 방향으로 잇는다(실제 운행 방향과 다를 수 있다)"
            except Exception:                      # noqa: BLE001
                pass
            return list(names), warn
    return [a, b], None


def _stations(option: dict | None, sc, lo=None) -> tuple[list[list[float]], list[dict], list[str]]:
    """계획 후보의 `uses`(「2호선:을지로입구」 …)를 탄 순서대로 역 좌표 [[lng, lat], …] 로.

    ★`uses` 는 탄 역·갈아탄 역·내린 역뿐이다(`plan.uses_of`). `lo`(호선별 역 순서)가 있으면 **탄 역과 내린 역 사이의 모든 역**을 채운다 —
      전에는 그 사이가 직선 한 줄이라 확대해도 같았다. 좌표를 못 찾는 역은 건너뛴다(선은 끊기지 않는다).
    돌려주는 것: (좌표 목록, 탄 구간별 요약 `rides`, 주의 문구 목록)."""
    if sc is None:
        return [], [], []
    from .wiring import engine_line
    # 같은 호선이 이어지는 항목을 (탄 역, 내린 역) 짝으로 묶는다
    groups: list[tuple[str, list[str]]] = []
    for u in (option or {}).get("uses") or []:
        line, sep, name = str(u).partition(":")
        if not sep or line.startswith("버스") or line.startswith("도로") or not name:
            continue
        if groups and groups[-1][0] == line:
            groups[-1][1].append(name)
        else:
            groups.append((line, [name]))
    out: list[list[float]] = []
    rides: list[dict] = []
    warns: list[str] = []
    for line, names in groups:
        pairs = [(names[i], names[i + 1]) for i in range(0, len(names) - 1, 2)] or [(names[0], names[0])]
        if len(names) % 2 == 1 and len(names) > 1:          # 짝이 안 맞으면(중복 제거 탓) 안전하게 일정에 담긴 역만
            pairs = [(names[i], names[i + 1]) for i in range(len(names) - 1)]
        for a, b in pairs:
            full, warn = _ride_names(lo, line, a, b)
            if warn and warn not in warns:
                warns.append(warn)
            kept = []
            for nm in full:
                rec = sc.get(engine_line(line), nm)
                if rec and rec.get("lat") is not None and rec.get("lng") is not None:
                    pt = [float(rec["lng"]), float(rec["lat"])]
                    kept.append(nm)
                    if not out or out[-1] != pt:
                        out.append(pt)
            rides.append({"line": line, "from": a, "to": b, "stations": kept, "count": len(kept),
                          "filled": len(full) > 2})
    return out, rides, warns


def _planned(route_def: dict | None) -> dict | None:
    if not route_def:
        return None
    return next((o for o in route_def.get("options") or [] if o.get("id") == route_def.get("planned")), None)


def build_shape(a: tuple[float, float], b: tuple[float, float], route_def: dict | None, *, router, sc,
                detail: bool = False, lo=None, bus=None, context=None) -> dict[str, Any]:
    """장소 a → b (둘 다 (경도, 위도)) 한 구간의 경로선. 계획 수단(route_def.planned)을 따른다.

    `detail=False` 는 축소용(모양 오차 2 m 안 · 점 최대 `MAX_POINTS`), `True` 는 확대용 상세 선(원본에 가깝게 · 최대 `MAX_POINTS_DETAIL`)."""
    option = _planned(route_def)
    mode = _kind(option)
    straight = [list(a), list(b)]
    note: str | None = None
    rides = []
    walks: list[list[int]] = []                   # 걷는 구간 [처음, 끝](line 번호) — 화면이 걸음 모양으로 그린다(계약 `walks`)
    selected, context_error = None, None
    if mode in ("subway", "bus", "mixed") and context is not None:
        from .shape_transit import selected_option, segments
        selected, context_error = selected_option(context, (option or {}).get('id'))
        if selected is not None:
            modes = [leg.get('mode', 'subway') for leg in selected['legs']]
            consistent = (set(modes) == {mode} if mode != 'mixed'
                          else {'bus', 'subway'} <= set(modes) or modes.count('bus') >= 2)
            if not consistent:
                sections, context_error = None, '계획 수단과 저장된 경로 문맥이 불일치하여 직선으로 잇는다'
            else:
                sections, context_error = segments(selected, bus=bus, sc=sc, lo=lo, ride_names=_ride_names)
            if sections is not None:
                line, notes, previous = [], [], list(a)
                for points, ride, section_note in sections:
                    if previous != points[0]:
                        start = max(len(line) - 1, 0)     # 앞 탑승 조각의 마지막 점(첫 걸음은 선의 처음)부터
                        walk, why = _route(router, tuple(previous), tuple(points[0]), 'foot')
                        line.extend(walk or [previous, points[0]])
                        walks.append([start, len(line)])  # 이 탑승 조각의 첫 점까지
                        if walk is None:
                            notes.append(f'역·정류장 접근/환승 걸음은 직선({why})')
                    line.extend(points)
                    previous = points[-1]
                    rides.append(ride)
                    if section_note not in notes:
                        notes.append(section_note)
                if previous != list(b):
                    start = len(line) - 1
                    walk, why = _route(router, tuple(previous), b, 'foot')
                    line.extend(walk or [previous, list(b)])
                    walks.append([start, len(line) - 1])
                    if walk is None:
                        notes.append(f'역·정류장→도착 장소 걸음은 직선({why})')
                source = 'transit_stops' if mode == 'mixed' else 'bus_stops' if mode == 'bus' else 'stations'
                grade, note = '추정', ' · '.join(notes)
    if context_error:
        line, source, grade, note = straight, 'straight_line', '근거없음', context_error
        walks = []
    elif selected is not None:
        pass                                      # 저장된 선택 구간을 이미 그렸다. uses로 방향을 다시 고르지 않는다.
    elif mode in ("walk", "bike", "taxi"):
        coords, why = _route(router, a, b, {"walk": "foot", "bike": "bike", "taxi": "car"}[mode])
        if coords:
            line, source, grade = coords, "local_road_graph", "추정"
            if mode == "walk":
                walks = [[0, len(line) - 1]]
        else:
            line, source, grade, note = straight, "straight_line", "근거없음", f"길찾기로 못 그려 직선으로 잇는다 — {why}"
    elif mode in ("subway", "mixed"):
        stops, rides, warns = _stations(option, sc, lo)
        if len(stops) >= 2:
            head, why_h = _route(router, a, tuple(stops[0]), "foot")
            tail, why_t = _route(router, tuple(stops[-1]), b, "foot")
            head_line = head or [list(a), stops[0]]
            line = head_line + stops + (tail or [stops[-1], list(b)])
            walks = [[0, len(head_line)], [len(head_line) + len(stops) - 1, len(line) - 1]]
            source, grade = "stations", "추정"
            parts = []
            if not head:
                parts.append(f"출발 장소→역 걸음은 직선({why_h})")
            if not tail:
                parts.append(f"역→도착 장소 걸음은 직선({why_t})")
            if mode == "mixed":
                parts.append("버스 구간은 정류장 목록이 없어 직선으로 잇는다")
            filled = sum(1 for r in rides if r["filled"])
            if filled:
                parts.append("탄 역~내린 역 사이의 모든 역을 순서대로 잇는다(실제 선로 곡선이 아님)")
            else:
                parts.append("일정에 담긴 역만 순서대로 잇는다 — 중간 역을 못 채웠다(실제 선로 곡선이 아님)")
            parts.extend(warns)
            note = " · ".join(parts)
        else:
            line, source, grade, note = straight, "straight_line", "근거없음", "탄 역 좌표를 못 찾아 직선으로 잇는다"
    elif mode == "bus":
        line, source, grade, note = straight, "straight_line", "근거없음", "버스는 정류장 목록이 일정에 없어(노선 번호만) 직선으로 잇는다"
    else:
        line, source, grade, note = straight, "straight_line", "근거없음", "계획 수단을 몰라 직선으로 잇는다"
    kept = _fit_kept(line, MAX_POINTS_DETAIL if detail else MAX_POINTS, tol_m=0.5 if detail else SIMPLIFY_TOLERANCE_M,
                     fixed=[i for span in walks for i in span])
    at = {old: new for new, old in enumerate(kept)}
    line = [line[i] for i in kept]
    walks = [[at[s], at[e]] for s, e in walks if e > s]
    extra = {"rides": rides} if rides and source in ('stations', 'bus_stops', 'transit_stops') else {}
    return {**extra, "mode": mode, "line": {"type": "LineString", "coordinates": line}, "walks": walks, "source": source,
            "grade": grade, "distance_m": round(_length(line)), "note": note}


def shapes_for_items(items, *, detail: bool = False) -> list[dict[str, Any]]:
    """여행 항목들에서 **앞뒤에 좌표 있는 장소가 있는 이동 항목마다** 경로선 하나. 좌표 없는 장소가 끼면 그 이동은 건너뛴다(그릴 곳이 없다)."""
    ordered = sorted(items, key=lambda it: it.seq)

    def xy(it):
        p = it.place or {}
        lat, lon = p.get("latitude"), p.get("longitude")
        return (float(lon), float(lat)) if lat is not None and lon is not None else None

    router, sc = _engine()
    lo = _line_order()
    bus = _bus_routes()
    out = []
    for n, it in enumerate(ordered):
        if it.kind != "mobility":
            continue
        before = next((x for x in reversed(ordered[:n]) if x.kind != "mobility" and xy(x)), None)
        if (it.detail or {}).get("from_stay"):
            # `[2026-10-13]` 아침 「숙소 → 첫 일정」 — 출발지는 순서상 앞(전날 마지막 일정)이 아니라 그날 묵는 숙소다
            from app.domains.travel_ops.components.itinerary.itinerary_checks import origin_of
            before = origin_of(ordered, it)
            before = before if before is not None and xy(before) else None
        after = next((x for x in ordered[n + 1:] if x.kind != "mobility" and xy(x)), None)
        if before is None or after is None:
            continue
        shape = build_shape(xy(before), xy(after), (it.detail or {}).get("route_def"), router=router, sc=sc,
                            detail=detail, lo=lo, bus=bus, context=(it.detail or {}).get('departure_context'))
        out.append({"item_id": str(it.item_id), "from_item_id": str(before.item_id), "to_item_id": str(after.item_id),
                    "from": (before.place or {}).get("name") or before.title, "to": (after.place or {}).get("name") or after.title,
                    **shape})
    return out


#: 접수 확인 화면 `moves[].mode`(계산기가 고른 후보의 종류) → `build_shape` 가 읽는 후보 id 접두. estimate(직선 어림)는 직선으로 내린다
_REVIEW_MODE_ID = {"walk": "walk", "subway": "subway", "bus": "bus", "transit": "subway_bus", "taxi": "taxi"}


def shapes_for_review(review: dict[str, Any] | None, *, detail: bool = False) -> list[dict[str, Any]]:
    """★`[2026-10-04]` 접수 **확인 화면**(등록 전)의 이동마다 경로선 하나 — 저장된 검사(`items[]` · `moves[]`)에서 만든다. 등록 여행용
    `shapes_for_items` 와 같은 모양(`item_id` 만 없다). `from_item_id`·`to_item_id` 는 확인 화면의 `items[].id`(「0-3」)다.
    좌표 없는 장소가 낀 이동은 건너뛴다. 저장본에 탄 역 정보(`uses`)가 없는 옛 검사의 지하철·대중교통은 직선 + 이유로 내린다."""
    if not review:
        return []
    items = {it.get("id"): it for it in review.get("items") or [] if it.get("id") is not None}

    def xy(it):
        p = (it or {}).get("place") or {}
        lat, lon = p.get("latitude"), p.get("longitude")
        return (float(lon), float(lat)) if lat is not None and lon is not None else None

    router, sc = _engine()
    lo = _line_order()
    bus = _bus_routes()
    out = []
    for m in review.get("moves") or []:
        a_it, b_it = items.get(m.get("from")), items.get(m.get("to"))
        a, b = xy(a_it), xy(b_it)
        if a is None or b is None:
            continue
        mode = m.get("mode")
        uses = m.get("uses") or []
        context = m.get('departure_context')
        if mode in ("subway", "transit") and not uses and context is None:
            shape = build_shape(a, b, None, router=router, sc=sc, detail=detail, lo=lo)
            shape["note"] = "저장된 검사에 탄 역 정보가 없어 직선으로 잇는다(새로 접수하면 역 좌표를 따라 그린다)"
        else:
            opt_id = m.get('selected_option_id') if context is not None else _REVIEW_MODE_ID.get(mode)
            route_def = ({"planned": opt_id, "options": [{"id": opt_id, "uses": uses}]} if opt_id else None)
            shape = build_shape(a, b, route_def, router=router, sc=sc, detail=detail, lo=lo,
                                bus=bus, context=context)
        out.append({"from_item_id": str(m["from"]), "to_item_id": str(m["to"]),
                    "from": (a_it.get("place") or {}).get("name") or a_it.get("title"),
                    "to": (b_it.get("place") or {}).get("name") or b_it.get("title"), **shape})
    return out
