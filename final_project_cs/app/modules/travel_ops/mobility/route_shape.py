# -*- coding: utf-8 -*-
"""지도에 그릴 **경로선** — 우리 도로 그래프로 직접 계산해 화면에 내려준다(외부 길찾기 API 호출 없음) · 2026-10-04.

왜: 화면은 지금 이동을 구글 지도 **링크**로만 보여 준다(`trip_api.map_view` — 한국에서 구글은 자동차·도보 길찾기를 안 주고, 대중교통은 들를 곳을
받지 않아 선을 못 그렸다). 우리는 지도 원본(OSM)에서 만든 도로 그래프(`engine/graph_router.py`)와 역 좌표가 있어, 같은 계산이 이미 내는 **형상**을
그대로 내려 줄 수 있다. 외부 서비스의 한도·약관·결과 저장 제한에 걸리지 않고, 소요·요금을 낸 계산과 같은 길이다.

무엇을 내나(이동 항목마다 하나)
  mode     walk · bike · taxi · subway · bus · mixed · unknown — 일정이 **계획 수단으로 고른** 후보의 종류
  line     GeoJSON LineString `[[경도, 위도], …]`(점 수는 MAX_POINTS 로 줄인다)
  source   local_road_graph(도로 그래프로 계산) · stations(역 좌표 순서 + 양 끝 걸음은 도로 그래프) · straight_line(두 장소 직선 — 마지막 수단)
  grade    추정(그래프 계산) · 근거없음(직선)  — 확정은 없다(지도 원본 기준이라 실제 보도·횡단·출입구와 다를 수 있다)
  note     왜 그렇게 그렸는지 한 줄(예: 「버스 노선 형상은 없어 직선으로 잇는다」)

대중교통: 지하철은 **탄 역 좌표를 순서대로 잇는다**(실제 선로 곡선이 아니다 — 선로 형상 자료가 없다) + 양 끝 걸음은 도로 그래프 foot 길.
         버스는 정류장 목록이 일정에 없어(노선 번호만) 직선으로 잇는다.
못 구했을 때(길찾기 꺼짐 · 지도 밖 · 경로 없음)는 직선 + `grade=근거없음` 으로 내리고 이유를 note 에 적는다 — 조용히 빼지 않는다.
"""
from __future__ import annotations

import math
from typing import Any

MAX_POINTS = 240                                  # 한 선의 점 상한 — 화면이 그리기에 충분하고 응답이 가볍다


def _hav(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(d))


def _length(coords) -> float:
    return sum(_hav(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _thin(coords, limit=MAX_POINTS):
    """점이 limit 보다 많으면 고르게 솎는다(처음·끝은 남긴다)."""
    if len(coords) <= limit:
        return coords
    step = (len(coords) - 1) / (limit - 1)
    out = [coords[round(i * step)] for i in range(limit - 1)]
    out.append(coords[-1])
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


def _stations(option: dict | None, sc) -> list[list[float]]:
    """계획 후보의 `uses`(「2호선:을지로입구」 …)를 탄 순서대로 역 좌표 [[lng, lat], …] 로. 좌표를 못 찾는 역은 건너뛴다."""
    if sc is None:
        return []
    from .wiring import engine_line
    out: list[list[float]] = []
    for u in (option or {}).get("uses") or []:
        line, sep, name = str(u).partition(":")
        if not sep or line.startswith("버스") or line.startswith("도로") or not name:
            continue
        rec = sc.get(engine_line(line), name)
        if rec and rec.get("lat") is not None and rec.get("lng") is not None:
            pt = [float(rec["lng"]), float(rec["lat"])]
            if not out or out[-1] != pt:
                out.append(pt)
    return out


def _planned(route_def: dict | None) -> dict | None:
    if not route_def:
        return None
    return next((o for o in route_def.get("options") or [] if o.get("id") == route_def.get("planned")), None)


def build_shape(a: tuple[float, float], b: tuple[float, float], route_def: dict | None, *, router, sc) -> dict[str, Any]:
    """장소 a → b (둘 다 (경도, 위도)) 한 구간의 경로선. 계획 수단(route_def.planned)을 따른다."""
    option = _planned(route_def)
    mode = _kind(option)
    straight = [list(a), list(b)]
    note: str | None = None
    if mode in ("walk", "bike", "taxi"):
        coords, why = _route(router, a, b, {"walk": "foot", "bike": "bike", "taxi": "car"}[mode])
        if coords:
            line, source, grade = coords, "local_road_graph", "추정"
        else:
            line, source, grade, note = straight, "straight_line", "근거없음", f"길찾기로 못 그려 직선으로 잇는다 — {why}"
    elif mode in ("subway", "mixed"):
        stops = _stations(option, sc)
        if len(stops) >= 2:
            head, why_h = _route(router, a, tuple(stops[0]), "foot")
            tail, why_t = _route(router, tuple(stops[-1]), b, "foot")
            line = (head or [list(a), stops[0]]) + stops + (tail or [stops[-1], list(b)])
            source, grade = "stations", "추정"
            parts = []
            if not head:
                parts.append(f"출발 장소→역 걸음은 직선({why_h})")
            if not tail:
                parts.append(f"역→도착 장소 걸음은 직선({why_t})")
            if mode == "mixed":
                parts.append("버스 구간은 정류장 목록이 없어 직선으로 잇는다")
            parts.append("역 사이는 역 좌표를 순서대로 잇는다(실제 선로 곡선이 아님)")
            note = " · ".join(parts)
        else:
            line, source, grade, note = straight, "straight_line", "근거없음", "탄 역 좌표를 못 찾아 직선으로 잇는다"
    elif mode == "bus":
        line, source, grade, note = straight, "straight_line", "근거없음", "버스는 정류장 목록이 일정에 없어(노선 번호만) 직선으로 잇는다"
    else:
        line, source, grade, note = straight, "straight_line", "근거없음", "계획 수단을 몰라 직선으로 잇는다"
    line = _thin(line)
    return {"mode": mode, "line": {"type": "LineString", "coordinates": line}, "source": source, "grade": grade,
            "distance_m": round(_length(line)), "note": note}


def shapes_for_items(items) -> list[dict[str, Any]]:
    """여행 항목들에서 **앞뒤에 좌표 있는 장소가 있는 이동 항목마다** 경로선 하나. 좌표 없는 장소가 끼면 그 이동은 건너뛴다(그릴 곳이 없다)."""
    ordered = sorted(items, key=lambda it: it.seq)

    def xy(it):
        p = it.place or {}
        lat, lon = p.get("latitude"), p.get("longitude")
        return (float(lon), float(lat)) if lat is not None and lon is not None else None

    router, sc = _engine()
    out = []
    for n, it in enumerate(ordered):
        if it.kind != "mobility":
            continue
        before = next((x for x in reversed(ordered[:n]) if x.kind != "mobility" and xy(x)), None)
        after = next((x for x in ordered[n + 1:] if x.kind != "mobility" and xy(x)), None)
        if before is None or after is None:
            continue
        shape = build_shape(xy(before), xy(after), (it.detail or {}).get("route_def"), router=router, sc=sc)
        out.append({"item_id": str(it.item_id), "from_item_id": str(before.item_id), "to_item_id": str(after.item_id),
                    "from": (before.place or {}).get("name") or before.title, "to": (after.place or {}).get("name") or after.title,
                    **shape})
    return out


#: 접수 확인 화면 `moves[].mode`(계산기가 고른 후보의 종류) → `build_shape` 가 읽는 후보 id 접두. estimate(직선 어림)는 직선으로 내린다
_REVIEW_MODE_ID = {"walk": "walk", "subway": "subway", "bus": "bus", "transit": "subway_bus"}


def shapes_for_review(review: dict[str, Any] | None) -> list[dict[str, Any]]:
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
    out = []
    for m in review.get("moves") or []:
        a_it, b_it = items.get(m.get("from")), items.get(m.get("to"))
        a, b = xy(a_it), xy(b_it)
        if a is None or b is None:
            continue
        mode = m.get("mode")
        uses = m.get("uses") or []
        if mode in ("subway", "transit") and not uses:
            shape = build_shape(a, b, None, router=router, sc=sc)
            shape["note"] = "저장된 검사에 탄 역 정보가 없어 직선으로 잇는다(새로 접수하면 역 좌표를 따라 그린다)"
        else:
            opt_id = _REVIEW_MODE_ID.get(mode)
            route_def = ({"planned": opt_id, "options": [{"id": opt_id, "uses": uses}]} if opt_id else None)
            shape = build_shape(a, b, route_def, router=router, sc=sc)
        out.append({"from_item_id": str(m["from"]), "to_item_id": str(m["to"]),
                    "from": (a_it.get("place") or {}).get("name") or a_it.get("title"),
                    "to": (b_it.get("place") or {}).get("name") or b_it.get("title"), **shape})
    return out
