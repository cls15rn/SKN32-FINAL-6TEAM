# -*- coding: utf-8 -*-
"""확인 화면의 **대체 후보** · **장소 검색** · **장소 사진** — 「지점 미정」이든 「닫는 날」이든 빈칸을 던지지 않고 고를 곳을 준다. `[2026-10-02 사용자 지시]`

★사용자 요구(2026-10-01): 이름이 모호하거나 못 맞는 곳에 **빈칸을 던지지 말고**, 가장 그럴듯한 하나를 먼저 채우고 다른 안과 고치는 길(검색 · 지도)을 준다.
  읽기 단계가 가까운 하나를 이미 골라 두었고(`pipeline._place_rows`), 이 모듈이 **다른 안 셋**과 **이름 검색**과 **사진**을 준다.

대체 후보 — 같은 날 앞뒤 일정에 가까운 순. 어디서 모으나:
    ① 이름이 모호한 곳(`picked_nearest` · `unresolved`)은 **같은 이름의 다른 지점**(카카오 키워드 검색, 앞뒤 일정 가운데에서 거리순). 지점이 안 나오면 아래 ②
    ② 같은 종류의 다른 곳 — 활동은 관광공사 목록(`place_catalog`, DB 만 · 같은 분류 우선, 운영시간은 새벽 작업이 읽어 둔 것), 식사는 요식 원장(DB 만, 그 시각에 여는 곳)
후보마다 **그 일정의 날짜 · 시각**에 맞는지 같은 검사 줄(장소 · 운영시간 · 휴무일 · 앞뒤 일정에 닿는 시간)로 보인다 — 맞는 곳(`fits`)이 먼저다.
★운영시간을 모르는 후보는 「모름」으로 보인다(후보에서 빼지 않는다 — 지점은 운영시간 표가 없는 카카오 값이 대부분이다). 다만 맞는 곳이 먼저다.

장소 검색 — 이름·분류로 DB(관광공사 목록 · 요식 원장)와 카카오를 함께 찾고, 앞뒤 일정에서 가까운 순으로 같은 검사를 붙인다.
★카카오 값은 저장하지 않는다(약관 — `kakao_local.py`). 이 모듈은 DB 에 아무것도 쓰지 않는다. 고객이 고르면 `edits` 로 **그 여행 전용 장소**로만 들어간다.
★`[2026-10-03 ui 세션 요청서 「장소 해석 개선」]` **지켜야 하는 것(시험으로 막는다)**: ①후보 · 검색 결과의 `kind` 는 일정의 `kind` 와 **같다** — 맞는 것이 없으면 다른 종류로 채우지 않고 빈 목록 + `no_same_kind`
  ②이름 없는 줄(`needs_choice`)은 줄에 적힌 **지역의 중심 · 반경**에서 같은 종류만 찾는다(앞뒤 일정의 한가운데는 지역이 없을 때만) ③예약했다고 적힌 줄(`needs_name`)은 후보를 권하지 않는다(`booked_needs_name`) —
  이름만 묻는다. 검색은 그대로 쓸 수 있다 ④후보 · 검색 결과 · 현재 장소에 종류 이름(`category`)을 채운다.
★사진은 저장하지 않는다 — 관광공사 사진 주소를 부를 때마다 받아 그대로 넘기고 출처 표시를 붙인다(`TourApiPlace.images`).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

from app.domains.travel_ops.components.itinerary.itinerary_checks import Part, check_itinerary
from .hours import _tenants, facts_for
from .moves import leg_between, late_text
from .terms import category_label
from .places import distance_m, normalize_full
from .review import (KST, SOURCE_LABEL, _Budgeted, _dt, _hours_lines, _line, _worst, default_engine, public_place)

LIMIT = 3                    #: 목업의 후보 A · B · C
POOL_EXTRA = 2               #: 최종 셋을 고르기 전에 이동 계산기로 더 재 보는 후보 수
SEARCH_LIMIT = 8
SEARCH_POOL = 40             #: 검색 결과 중 일정에 맞는지 판정해 순위에 반영하는 수(이름 일치 · 가까운 순으로 고른 앞 40곳)
RADIUS_M = 3000              #: 같은 종류 후보를 찾는 반경 — `catalog_pool` 이 바꾸기 후보에 쓰는 3km 와 같다
#: 후보 하나를 판정하는 데 이동 계산기를 쓰는 시간 상한(초) — 넘으면 남은 구간은 어림값
ENGINE_BUDGET_S = 12.0


def _near(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b or None in (a.get("latitude"), a.get("longitude"), b.get("latitude"), b.get("longitude")):
        return None
    return distance_m(float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"]))


def neighbours(items: list[dict[str, Any]], item: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """같은 날 바로 앞 · 뒤 일정(장소 좌표가 있는 것). 검사 목록은 날짜 · 시각 순이다."""
    ordered = [x for x in items if x["date"] == item["date"]]
    i = next((n for n, x in enumerate(ordered) if x["id"] == item["id"]), None)
    if i is None:
        return None, None
    prev = next((x for x in reversed(ordered[:i]) if x["place"]), None)
    nxt = next((x for x in ordered[i + 1:] if x["place"]), None)
    return prev, nxt


def area_of(item: dict[str, Any]) -> dict[str, Any] | None:
    """이름 없는 줄이 가리키는 지역(`{name, kind, latitude, longitude, radius_m}`) — 없으면 None."""
    return ((item.get("parts") or {}).get("area")) or None


def reference(item: dict[str, Any], prev: dict[str, Any] | None, nxt: dict[str, Any] | None) -> tuple[float, float] | None:
    """후보를 찾을 중심 — **줄에 지역이 적혀 있으면 그 지역의 중심**(「성수 식당」은 성수에서 찾는다), 없으면 앞뒤 일정 좌표의 가운데, 앞뒤도 없으면 지금 장소.
    ☆전에는 앞뒤 일정의 한가운데만 썼다 — 앞이 성수 쪽, 뒤가 경복궁이면 중심이 동대문 근처가 돼 성수 식당이 하나도 안 나왔다(실서버 실측)."""
    area = area_of(item)
    if area:
        return float(area["latitude"]), float(area["longitude"])
    pts = [x["place"] for x in (prev, nxt) if x and x["place"]]
    if not pts and item["place"]:
        pts = [item["place"]]
    if not pts:
        return None
    return (sum(float(p["latitude"]) for p in pts) / len(pts), sum(float(p["longitude"]) for p in pts) / len(pts))


def _score(place: dict[str, Any], prev: dict[str, Any] | None, nxt: dict[str, Any] | None,
           ref: tuple[float, float] | None, anchored: bool = False) -> float:
    """줄 순서의 점수(작을수록 앞) — 지역이 적힌 줄은 **그 지역 중심에서의 거리**, 아니면 앞뒤 일정까지의 평균 거리."""
    pts = [x["place"] for x in (prev, nxt) if x and x["place"]] if not (anchored and ref) else []
    if pts:
        return sum(distance_m(float(place["latitude"]), float(place["longitude"]), float(p["latitude"]), float(p["longitude"]))
                   for p in pts) / len(pts)
    if ref:
        return distance_m(float(place["latitude"]), float(place["longitude"]), ref[0], ref[1])
    return 0.0


def _place(name: str, lat: float, lon: float, source: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "latitude": float(lat), "longitude": float(lon), "source": source,
            "kind": extra.pop("kind", None), "address": extra.pop("address", None), "category": extra.pop("category", None),
            "content_id": extra.pop("content_id", None), "content_type_id": extra.pop("content_type_id", None),
            "place_id": extra.pop("place_id", None), "ref": extra.pop("ref", None), **extra}


def _public(place: dict[str, Any]) -> dict[str, Any]:
    """응답에 싣는 모양 — 내부 칸(`_hours` · `place_id`)은 뺀다. 고르면 `edits` 로 이 객체를 그대로 보낸다."""
    return {k: place.get(k) for k in ("name", "latitude", "longitude", "source", "kind", "address", "category",
                                       "content_id", "content_type_id", "ref")}


# ── 모으기 ───────────────────────────────────────────────────────
def _kakao_places(kakao: Any, query: str, ref: tuple[float, float] | None, kind: str, notes: list[str],
                  size: int = 10) -> list[dict[str, Any]]:
    if kakao is None or not query.strip():
        return []
    hits = kakao.search(query, size=size, near=ref) if ref else kakao.search(query, size=size)
    if hits is None:                                     # 못 불렀다(예산 · 시간 초과 · 연결) — 결과 0건과 다르다
        misses = getattr(kakao, "misses", None) or {}
        notes.append("kakao:" + (max(misses, key=misses.get) if misses else "unavailable"))
        return []
    # ★음식점 · 카페가 아닌 곳은 늘 활동이다 — 전에는 일정이 식사면 약국 · 공원도 식사로 태그했다(검색에서 식당 자리에 약국이 섞였다, 실서버 실측)
    return [_place(h["name"], h["latitude"], h["longitude"], "kakao",
                   kind="dining" if h.get("category_group") in ("FD6", "CE7") else "activity", address=h.get("address"),
                   category=h.get("category") or None, ref=f"kakao:{h['id']}" if h.get("id") else None) for h in hits]


#: 관광공사 분류 — 숙박 · 쇼핑. ★`[2026-10-07]` 숙소 줄은 숙박(32)을 **읽어야** 후보가 나오고, 분류를 모르는 활동 줄은 쇼핑(38)을 **빼야** 한다
LODGING_TYPE = "32"
SHOPPING_TYPE = "38"
#: 원래 장소의 분류를 모를 때(우리 장소 표의 행 — 활동 522곳 중 194곳만 분류가 있다) 후보에서 빼는 분류. 카탈로그의 38 은 4,360건으로 가장 많아 가까운 순으로 고르면
#: 관광지(경복궁)의 대체로 약국 · 편의점이 먼저 나왔다(2026-10-07 사용자 지적). 쇼핑을 찾는 줄은 분류(38)를 줘서 들어오므로 이 기본값에 걸리지 않는다
UNTYPED_EXCLUDES = (SHOPPING_TYPE,)


def _catalog_places(conn, tenant_id: str, ref: tuple[float, float], content_type: str | None,
                    now: datetime, *, limit: int = 12, query: str | None = None, radius_m: float = RADIUS_M) -> list[dict[str, Any]]:
    """관광공사 목록(DB)에서 반경 안 활동 — 운영시간은 새벽 작업이 읽어 둔 것(`catalog_hours`)을 붙인다. 바깥을 부르지 않는다.

    `content_type` 이 있으면 **그 분류만**, 없으면(분류를 모른다) 쇼핑(38)을 뺀 활동이다. 숙박(32)이면 숙박 분류를 읽는다(전에는 활동 분류만 읽어 숙소 후보가 0 이었다)."""
    from app.domains.travel_ops.components.places.catalog_pool import nearby_activities, reuse_hours

    cands = nearby_activities(conn, tenant_id, latitude=ref[0], longitude=ref[1], radius_m=radius_m,
                              exclude_names=set(), limit=limit * 2, types=[LODGING_TYPE] if content_type == LODGING_TYPE else None)
    if content_type:
        # ★같은 분류가 없으면 비운다 — 관광지의 대체로 음식점 · 쇼핑을 끌어오지 않는다(호출한 쪽이 `no_same_kind` 로 알린다)
        cands = [c for c in cands if c.attributes.get("source_content_type_id") == content_type]
    else:
        cands = [c for c in cands if c.attributes.get("source_content_type_id") not in UNTYPED_EXCLUDES]
    if query:
        key = normalize_full(query)
        cands = [c for c in cands if key in normalize_full(c.name)]
    cands = cands[:limit]
    reuse_hours(conn, tenant_id, cands, now)
    return _tour_places(cands)


def place_kind_of(item: dict[str, Any]) -> str:
    """후보 장소와 견줄 종류 — 숙소 항목(`lodging`)의 후보는 목록 · 지도에서 **활동(`activity`) 쪽 종류의 숙박 분류**로 읽히므로 `activity` 와 견준다.
    `[2026-10-15 숙박예약 점검 LB-01]` ☆전에는 항목 종류(`lodging`)를 그대로 견줘 후보 · 검색 결과가 전부 걸러져 늘 `no_same_kind` 였다."""
    return "activity" if item.get("kind") == "lodging" else item["kind"]


def _is_lodging_place(place: dict[str, Any]) -> bool:
    """숙소로 읽히는 곳 — 관광공사 숙박 분류(32)이거나 지도의 분류에 「숙박」이 든 곳."""
    return str(place.get("content_type_id") or "") == LODGING_TYPE or "숙박" in str(place.get("category") or "")


def _is_stay_line(item: dict[str, Any]) -> bool:
    """숙소를 찾는 줄 — 「호텔」 · 「호텔 조식」 둘 다. ★`[2026-10-07 사용자]` 「그냥 호텔이라고 적어 놨는데 호텔 조식으로 인식하는 오류다 — 아침에 출발하는 장소일 수도,
    그 안에서 조식을 먹을 수도 있다. 둘 다 추천 목록에 올려서 보여 주면 된다」."""
    return (item.get("parts") or {}).get("content_type") == LODGING_TYPE


def _with_stay_meals(found: list[dict[str, Any]], *, per: int = 3) -> list[dict[str, Any]]:
    """숙소 후보 앞쪽 `per` 곳마다 **그 숙소 안에서 하는 식사** 짝을 붙인다 — 같은 장소 · 종류만 식사(`dining`). 짝은 원본 바로 뒤에 둔다(순위 판정이 같아 나란히 선다).
    ★식당을 지어내지 않는다 — 숙소의 이름과 자리 그대로이고, 그 안에 식당이 있는지는 모른다(이유 문장이 그렇게 말한다)."""
    out: list[dict[str, Any]] = []
    paired = 0
    for place in found:
        stay = {**place, "basis": place.get("basis") or "lodging"}
        out.append(stay)
        if paired < per and place.get("kind") == "activity":
            out.append({**place, "kind": "dining", "basis": "lodging_meal", "ref": None, "category": "숙소 안 식사"})
            paired += 1
    return out


def _tour_places(cands: list[Any]) -> list[dict[str, Any]]:
    return [_place(c.name, c.lat, c.lon, "tour_api", kind="activity", address=c.attributes.get("address"),
                   category=category_label(c.attributes.get("source_content_type_id")),
                   content_id=c.attributes.get("source_content_id"),
                   content_type_id=c.attributes.get("source_content_type_id"),
                   ref=f"tour:{c.attributes['source_content_id']}" if c.attributes.get("source_content_id") else None,
                   _hours={k: c.attributes[k] for k in ("hours_week", "hours_read") if c.attributes.get(k)} or None,
                   similarity=c.attributes.get("similarity"))
            for c in cands]


def _similar_places(conn, tenant_id: str, ref: tuple[float, float], lcls: list[str | None], now: datetime, *,
                    min_level: int = 1, limit: int = 12, radius_m: float = RADIUS_M, grade: str | None = None) -> list[dict[str, Any]]:
    """원래 장소와 **같은 분류 나무 가지**의 곳 — 같은 소분류 → 중분류 → 대분류 순, 가까운 순(`catalog_pool.nearby_similar`).
    ★다른 대분류로는 넘어가지 않는다(관광지의 대체로 약국 · 편의점을 권하지 않는다). 각 후보의 `similarity` 에 맞은 단계(3 · 2 · 1)가 든다."""
    from app.domains.travel_ops.components.places.catalog_pool import nearby_similar, reuse_hours

    cands = nearby_similar(conn, tenant_id, latitude=ref[0], longitude=ref[1], radius_m=radius_m, lcls=lcls,
                           exclude_names=set(), limit=limit, min_level=min_level)
    reuse_hours(conn, tenant_id, cands, now)
    return [{**place, "basis": "same_kind", "class_grade": grade} for place in _tour_places(cands)]


def _experience_places(conn, tenant_id: str, ref: tuple[float, float], group: str, now: datetime, *,
                       limit: int = 12, radius_m: float = RADIUS_M) -> list[dict[str, Any]]:
    """**비슷한 경험** — 원래 장소와 같은 종류는 모자랄 때, 같은 경험 갈래(`EXPERIENCE_GROUPS`)의 곳을 가까운 순으로. 쇼핑 · 약국 같은 생활 시설은 갈래에 없다."""
    from app.domains.travel_ops.components.places.catalog_pool import nearby_in_classes, reuse_hours

    cands = nearby_in_classes(conn, tenant_id, latitude=ref[0], longitude=ref[1], radius_m=radius_m,
                              lcls2=EXPERIENCE_GROUPS[group]["lcls2"], exclude_names=set(), limit=limit)
    reuse_hours(conn, tenant_id, cands, now)
    return [{**place, "basis": "similar_experience", "experience": group} for place in _tour_places(cands)]


def _ledger_places(conn, tenant_id: str, ref: tuple[float, float], *, limit: int = 15,
                   query: str | None = None, radius_m: int = 2000) -> list[dict[str, Any]]:
    """요식 원장에서 올린 식당(공용 장소 행) — 반경 안 가까운 순. 영업 여부는 후보 검사에서 본다."""
    dlat = radius_m / 111_000
    dlon = radius_m / (111_000 * max(0.2, math.cos(math.radians(ref[0]))))
    sql = ("SELECT place_id, name, latitude, longitude, attributes->>'address', attributes->>'dining_place_uid' FROM places "
           "WHERE tenant_id = ANY(%s) AND source_name='dining_ledger' AND trip_scope IS NULL AND kind='dining' "
           "AND latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s ")
    args: list[Any] = [_tenants(tenant_id), ref[0] - dlat, ref[0] + dlat, ref[1] - dlon, ref[1] + dlon]
    if query:
        sql += "AND name ILIKE %s "
        args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sql += "ORDER BY (latitude - %s)^2 + ((longitude - %s) * %s)^2 LIMIT %s"
    args += [ref[0], ref[1], math.cos(math.radians(ref[0])), limit * 2]
    try:
        with conn.transaction(), conn.cursor() as cur:       # 세이브포인트 — DB 오류가 바깥 트랜잭션을 망가뜨리지 않는다
            cur.execute(sql, args)
            rows = cur.fetchall()
    except Exception as exc:                              # noqa: BLE001 — 원장 열이 없는 DB(시험 · 다른 조립)면 원장 없이
        if type(exc).__name__ not in ("UndefinedColumn", "UndefinedTable"):
            raise
        return []
    out = []
    details = _ledger_details(conn, [uid for *_rest, uid in rows if uid])
    for place_id, name, lat, lon, address, uid in rows:
        if lat is None or lon is None:
            continue
        road, cuisine = details.get(uid, (None, None))
        # 종류 이름은 카카오와 같은 모양(「음식점 > 한식」) — 요리 갈래를 모르면(미상) 「음식점」까지만
        category = category_label("39") + (f" > {cuisine}" if cuisine and cuisine != "미상" else "")
        out.append(_place(name, lat, lon, "places", kind="dining", address=address or road, category=category,
                          place_id=str(place_id), ref=f"place:{place_id}"))
        if len(out) >= limit:
            break
    return out


def _ledger_details(conn, uids: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """요식 원장 식당의 (도로명 주소 · 요리 갈래) — 우리 장소 표(`places`)에는 주소 · 갈래가 없다. 원장이 없는 DB 면 빈 값(후보에서 이 칸만 빈다)."""
    if not uids:
        return {}
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT place_uid::text, road_address, category FROM dining.dn_place WHERE place_uid::text = ANY(%s)", (uids,))
            return {uid: (road, cuisine) for uid, road, cuisine in cur.fetchall()}
    except Exception as exc:                              # noqa: BLE001
        if type(exc).__name__ not in ("UndefinedTable", "UndefinedColumn", "InvalidSchemaName", "InsufficientPrivilege"):
            raise
        return {}


def _db_places_by_name(conn, tenant_id: str, query: str, ref: tuple[float, float] | None, now: datetime,
                       limit: int = 10) -> list[dict[str, Any]]:
    """검색어가 이름에 든 관광공사 목록 장소(서울)."""
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    order, args = ("(latitude - %s)^2 + ((longitude - %s) * 0.8)^2", [ref[0], ref[1]]) if ref else ("length(title)", [])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_id, content_type_id, title, address, latitude, longitude FROM place_catalog "
            "WHERE tenant_id = ANY(%s) AND source='tour_api' AND title ILIKE %s AND latitude IS NOT NULL "
            f"AND address LIKE '서울%%' ORDER BY {order} LIMIT %s", [_tenants(tenant_id), like, *args, limit])
        rows = cur.fetchall()
    from app.domains.travel_ops.components.places.catalog_pool import reuse_hours
    from app.domains.travel_ops.components.planning.planner import Cand

    cands = [Cand(f"tour_{cid}", title, "activity", float(lat), float(lon),
                  {"source_content_id": str(cid), "source_content_type_id": str(ctype)}, "place_catalog")
             for cid, ctype, title, _addr, lat, lon in rows]
    reuse_hours(conn, tenant_id, cands, now)
    out = []
    for (cid, ctype, title, address, lat, lon), cand in zip(rows, cands):
        kind = "dining" if str(ctype) == "39" else "activity"
        out.append(_place(title, lat, lon, "tour_api", kind=kind, address=address, content_id=str(cid),
                          content_type_id=str(ctype), ref=f"tour:{cid}", category=category_label(ctype),
                          _hours={k: cand.attributes[k] for k in ("hours_week", "hours_read") if cand.attributes.get(k)} or None))
    return out


#: 같은 곳으로 보는 거리(m) — 이름이 같고 이 안이면 같은 장소다(같은 상호의 **다른 지점**은 이름이 같아도 멀리 있어 남는다)
SAME_SITE_M = 300


def _taken(items: list[dict[str, Any]], item: dict[str, Any], *, include_self: bool = True
           ) -> list[tuple[str, float | None, float | None]]:
    """이미 일정에 있는 곳 — 같은 날 다른 일정의 장소(와 `include_self` 면 이 일정의 지금 장소). 후보에서 뺀다(이름 **과** 자리가 같을 때만).
    ★검색은 지금 장소를 남긴다 — 이름이 모호해 임시로 고른 곳을 「맞아요」로 확정하려고 다시 고르는 길이다."""
    out = []
    for x in items:
        if x["place"] and ((include_self and x["id"] == item["id"]) or (x["id"] != item["id"] and x["date"] == item["date"])):
            out.append((normalize_full(x["place"]["name"]), x["place"].get("latitude"), x["place"].get("longitude")))
    return out


def _is_taken(place: dict[str, Any], taken: list[tuple[str, float | None, float | None]]) -> bool:
    key = normalize_full(place["name"])
    for name, lat, lon in taken:
        if name == key and (lat is None or lon is None or (_near(place, {"latitude": lat, "longitude": lon}) or 0) <= SAME_SITE_M):
            return True
    return False


def _dedupe(places: list[dict[str, Any]], taken: list[tuple[str, float | None, float | None]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in places:
        key = normalize_full(p["name"])
        if _is_taken(p, taken):
            continue
        if any(normalize_full(o["name"]) == key and (_near(o, p) or 0) <= 150 for o in out):
            continue
        out.append(p)
    return out


# ── 후보 하나를 그 일정에 맞춰 보기 ───────────────────────────────
def assess(conn, tenant_id: str, item: dict[str, Any], place: dict[str, Any], prev: dict[str, Any] | None,
           nxt: dict[str, Any] | None, engine: Callable[..., Any] | None) -> dict[str, Any]:
    """`place` 가 이 일정의 날짜 · 시각에 맞나 — 장소 · (닿는 시간) · 운영시간 · 휴무일 줄과 맞는지(`fits`)."""
    stay_item = item["kind"] == "lodging"
    # ★`[2026-10-15]` 숙소 항목은 끝이 체크아웃 날이다(`end_date`) — 시작 날로 끝을 읽으면 끝이 시작보다 빨라진다. 숙소는 운영시간 판정 대상이 아니다(종류 `lodging`)
    start, end = _dt(item["date"], item["starts_at"]), _dt(item.get("end_date") or item["date"], item["ends_at"])
    kind = "lodging" if stay_item else (place.get("kind") or item["kind"])          # ★끼니 시간의 시장 줄(`meal_inferred`)은 식당 후보도 받는다 — 그 후보는 식당으로 판정한다
    facts = facts_for(conn, tenant_id, place, kind, start, end)
    vios = []
    if start is not None:
        vios = check_itinerary([Part(seq=1, kind=kind, title=place["name"], starts_at=start, ends_at=end,
                                     place={"name": place["name"], "attributes": dict(facts.attributes)})])
    row = {"date": item["date"], "start": item["starts_at"], "end": item["ends_at"], "place": place}
    hours_line, closed_line = _hours_lines(row, facts, vios)
    label = SOURCE_LABEL.get(place.get("source") or "", "장소 정보")
    lines = [_line("place", "ok", f"{label} 정보로 찾았어요")]
    # 앞 · 뒤 일정에 닿는 시간 — 늦으면 줄을 더한다
    late: list[str] = []
    estimated = False
    slacks: dict[str, int | None] = {"before": None, "after": None}
    if start is not None and place:
        pa = {"key": "cand", "name": place["name"], "lat": place["latitude"], "lon": place["longitude"]}
        if prev and prev["place"]:
            p_end = _dt(prev["date"], prev["ends_at"] or prev["starts_at"])
            if p_end is not None:
                leg = leg_between(engine, {"key": "prev", "name": prev["place"]["name"], "lat": prev["place"]["latitude"],
                                           "lon": prev["place"]["longitude"]}, pa, p_end, start, deep=False)
                slacks["before"] = leg.slack_min
                estimated = estimated or leg.basis == "estimate"
                if leg.slack_min < 0:
                    late.append(f"{prev['place']['name']}에서 오면 {late_text(leg.slack_min)}")
        leave = start if stay_item else end            # 숙소는 체크인 시각에 떠난다고 본다(끝은 며칠 뒤 — `review.build` 와 같다)
        if nxt and nxt["place"] and leave is not None:
            n_start = _dt(nxt["date"], nxt["starts_at"])
            if n_start is not None:
                leg = leg_between(engine, pa, {"key": "next", "name": nxt["place"]["name"], "lat": nxt["place"]["latitude"],
                                               "lon": nxt["place"]["longitude"]}, leave, n_start, deep=False)
                slacks["after"] = leg.slack_min
                estimated = estimated or leg.basis == "estimate"
                if leg.slack_min < 0:
                    late.append(f"{nxt['place']['name']}에는 {late_text(leg.slack_min)}")
    if late:
        lines.append(_line("time", "warn", " · ".join(late)))
    lines += [hours_line, closed_line]
    worst = _worst([(ln["result"], "x") for ln in lines])[0]
    fits = worst in ("ok", "unknown") and hours_line["result"] != "warn"
    return {"rows": lines, "fits": fits, "status": "bad" if worst == "bad" else ("warn" if worst == "warn" else "ok"),
            "slack": slacks, "estimated": estimated}


def _present(place: dict[str, Any], assessed: dict[str, Any], prev, nxt, ref, area: dict[str, Any] | None = None) -> dict[str, Any]:
    dist = None
    pts = [x["place"] for x in (prev, nxt) if x and x["place"]] if not (area and ref) else []
    if area and ref:
        dist, name = distance_m(float(place["latitude"]), float(place["longitude"]), ref[0], ref[1]), area["name"]   # 「성수에서 320m」
    elif pts:
        nearest = min(pts, key=lambda p: _near(place, p) or 1e12)
        dist, name = _near(place, nearest), nearest["name"]
    else:
        name = None
        if ref:
            dist = distance_m(float(place["latitude"]), float(place["longitude"]), ref[0], ref[1])
    return {"place": _public(place), "distance_m": round(dist) if dist is not None else None, "reference": name,
            "rows": assessed["rows"], "fits": assessed["fits"], "status": assessed["status"], "slack": assessed["slack"],
            "estimated": assessed["estimated"],
            # ★`[2026-10-07]` 원래 장소와 분류 나무에서 맞은 단계 — 3 같은 소분류 · 2 같은 중분류 · 1 같은 대분류 · None 은 나무로 고른 것이 아니다(식당 · 지도 검색)
            "similarity": place.get("similarity"),
            # ★`[2026-10-07 사용자]` 어느 단계로 고른 후보인가 — same_kind(같은 종류, 분류 나무) · similar_experience(비슷한 경험) · meal_inferred(끼니 시간의 식당) · None(지도 검색 등).
            #   `taste`(취향 추천 — 일정에 담은 다른 활동의 분류, `taste.py`)가 셋째 단이다
            "basis": place.get("basis"),
            # 비슷한 경험 단계일 때 어느 갈래(outdoor_walk · indoor_exhibit)인지 — 이유 문장이 쓴다
            "experience": place.get("experience"),
            # 취향 단(`basis: "taste"`)의 근거 — {with: 일정에 담은 장소 이름, count, ranked_by, lcls2}. 아니면 None
            "taste": place.get("taste"),
            # 원래 장소의 분류가 **추정**일 때만 "estimated"(이유 문장이 「관광공사 분류」라고 쓰지 않는다). 아니면 None
            "class_grade": place.get("class_grade")}


def _common_prefix(rows: list[tuple[str | None, str | None, str | None]]) -> list[str | None]:
    """여러 줄이 **같은 단계까지만** 남긴 나무 길 — 첫 줄과 다르면 그 아래는 None. 예: (HS,HS01,HS010100) · (HS,HS02,HS020100) → (HS, None, None)."""
    base = list(rows[0])
    for row in rows[1:]:
        for n in range(3):
            if base[n] != row[n]:
                for m in range(n, 3):
                    base[m] = None
                break
    return base


def _original_class(conn, tenant_id: str, current: dict[str, Any] | None) -> dict[str, Any] | None:
    """원래 장소의 관광공사 분류 — `{"content_type": 번호 | None, "lcls": [대, 중, 소] | None, "by": ...}` 또는 None(모른다).

    ①장소가 분류 번호를 들고 있으면 그것(`given`) ②**관광공사 번호**(`content_id`)로 목록 항목을 찾고 ③없으면 **같은 이름의 목록 항목**에서 찾는다.
    이름이 여러 곳에 걸치면 **같은 단계까지만**(`_common_prefix`) 쓴다 — 대분류까지도 갈리면 나무는 모른다.
    ★우리 장소 표의 행은 분류가 대개 비어 있다(활동 522곳 중 194곳만 있다) — 그래서 목록에서 찾는다."""
    if not current:
        return None
    given = str(current["content_type_id"]) if current.get("content_type_id") else None
    content_id = str(current["content_id"]) if current.get("content_id") else None
    name = (current.get("name") or "").strip()
    rows: list[tuple[Any, ...]] = []
    by = None
    try:
        with conn.transaction(), conn.cursor() as cur:
            sql = ("SELECT content_type_id, raw_json->>'lclsSystm1', raw_json->>'lclsSystm2', raw_json->>'lclsSystm3' "
                   "FROM place_catalog WHERE tenant_id = ANY(%s) AND source='tour_api' AND ")
            if content_id:
                cur.execute(sql + "content_id = %s", (_tenants(tenant_id), content_id))
                rows, by = cur.fetchall(), "content_id"
            if not rows and name:
                cur.execute(sql + "title = %s", (_tenants(tenant_id), name))
                rows, by = cur.fetchall(), "name"
    except Exception as exc:                                  # noqa: BLE001 — 목록이 없는 DB(시험 · 다른 조립)면 분류를 모른다
        if type(exc).__name__ not in ("UndefinedColumn", "UndefinedTable"):
            raise
        rows = []
    types = sorted({r[0] for r in rows if r[0]})
    lcls = [None, None, None]
    grade = None
    if rows:
        common = _common_prefix([(r[1], r[2], r[3]) for r in rows])
        lcls = common if common[0] else [None, None, None]
    if not lcls[0]:
        # ★`[2026-10-07 사용자 결정]` 목록에서 못 찾았으면 **한 번 채워 저장한 분류**(`places.attributes.inferred_class` — 이름 일치 · 임베딩 투표)를 쓴다
        from .class_fill import stored_class

        place_ref = str(current.get("place_id") or "") or (str(current.get("ref") or "")[6:] if str(current.get("ref") or "").startswith("place:") else "")
        saved = stored_class(conn, place_ref) if place_ref else None
        if saved and saved.get("lcls") and saved["lcls"][0]:
            lcls, grade, by = list(saved["lcls"]), saved.get("grade"), "stored"
    content_type = given or (types[0] if len(types) == 1 else None)
    if not content_type and not lcls[0]:
        return None
    return {"content_type": content_type, "lcls": lcls if lcls[0] else None, "by": "given" if given and not rows else by, "grade": grade}


def _original_type(conn, tenant_id: str, current: dict[str, Any] | None) -> str | None:
    """원래 장소의 관광공사 분류 **번호**(`_original_class` 의 `content_type`) — 모르면 None."""
    found = _original_class(conn, tenant_id, current)
    return found["content_type"] if found else None


# ── 비슷한 경험 갈래 — 같은 종류가 모자랄 때 「이런 경험」으로 이어 간다 ─────────────────────
#: ★`[2026-10-07 사용자]` 「경복궁을 못 가면 공원을 간다던가 할 거 아니냐」 — 같은 종류(분류 나무)의 곳이 모자라면 **같은 경험**의 곳을 권한다.
#: 갈래와 중분류 짝은 **우리가 고른 값**이다. 이름이 확인된 것: VE01 동상 · VE03 공원 · VE04 둘레길 · VE07 전시관(`itinerary.py` 주석 · 목록의 제목 표본).
#: `[추정]` 나머지(HS01 · HS02 · HS04 · NA0x · LS01 · EX06)는 카탈로그의 제목 표본(성곽 · 옛집 · 산 · 폭포 · 한강 · 산책로 · 박물관)을 보고 정했다. 쇼핑 · 약국 · 병원 같은 생활 시설은 어느 갈래에도 없다
EXPERIENCE_GROUPS: dict[str, dict[str, Any]] = {
    "outdoor_walk": {"label": "야외에서 걷고 둘러보기",
                     "lcls2": ("HS01", "HS02", "HS04", "NA01", "NA02", "NA03", "NA04", "NA05", "VE01", "VE03", "VE04", "LS01")},
    "indoor_exhibit": {"label": "실내에서 전시 보기", "lcls2": ("VE07", "EX06")},
}


def _experience_of(lcls2: str | None) -> str | None:
    for key, group in EXPERIENCE_GROUPS.items():
        if lcls2 in group["lcls2"]:
            return key
    return None


_SAME_KIND_WORDS = {3: "같은 소분류", 2: "같은 중분류", 1: "같은 대분류"}
#: 후보 순서의 단 — 같은 종류(그 밖의 꼬리표 없는 후보 포함)가 먼저, 비슷한 경험, 취향 추천은 맨 뒤
_BASIS_ORDER = {"similar_experience": 1, "taste": 2}


def with_josa(name: str) -> str:
    """이름 뒤에 「와/과」 — 받침이 있으면 「과」(경복궁과), 없으면 「와」(북촌와가 아니라 광장시장과 …). 한글이 아닌 끝 글자(숫자 · 영문)는 「와」로 둔다(읽는 소리를 알 수 없다)."""
    last = name.strip()[-1:] if name.strip() else ""
    if "가" <= last <= "힣":
        return name + ("과" if (ord(last) - 0xAC00) % 28 else "와")
    return name + "와"


def _reason(candidate: dict[str, Any], original: str | None) -> str | None:
    """후보를 고른 **이유 한 문장** — 사실만. 어느 단계(`basis`)인지 · 거리 · 그 시각 영업 · 다음 일정까지 여유 중 **아는 것만** 적는다(모르면 쓰지 않거나 모른다고 쓴다)."""
    basis = candidate.get("basis")
    name = f"{with_josa(original)} " if original else ""
    if basis == "same_kind" and candidate.get("class_grade") == "estimated":
        head = f"{name}{_SAME_KIND_WORDS.get(candidate.get('similarity'), '비슷한 종류')}로 짐작되는 곳이에요(분류를 추정했어요)"
    elif basis == "same_kind":
        head = f"{name}{_SAME_KIND_WORDS.get(candidate.get('similarity'), '비슷한 종류')}(관광공사 분류)의 곳이에요"
    elif basis == "similar_experience":
        group = EXPERIENCE_GROUPS.get(candidate.get("experience") or "", {})
        head = (f"{name}같은 종류는 가까이에 마땅한 곳이 없어, {group.get('label') or '비슷한 경험'}을 하는 곳 중 가까운 곳을 골랐어요"
                if original else f"{group.get('label') or '비슷한 경험'}을 하는 곳 중 가까운 곳이에요")
    elif basis == "taste":
        from . import taste as taste_module

        head = taste_module.reason_head(candidate.get("taste") or {})
    elif basis == "meal_inferred":
        head = "식사 시간이고 그 시간대에 식사 일정이 따로 없어 식당도 후보로 넣었어요"
    elif basis == "lodging":
        head = "숙소 후보예요"
    elif basis == "lodging_meal":
        head = "이 숙소 안에서 식사하는 경우예요(숙소 안 식당이 있는지는 확인하지 못했어요)"
    else:
        head = None
    facts: list[str] = []
    anchor = candidate.get("reference") or original            # 앞뒤 일정이 없으면 거리는 원래 장소에서 잰 값이다
    if candidate.get("distance_m") is not None and anchor:
        facts.append(f"{anchor}에서 {candidate['distance_m']}m")
    hours = next((r for r in candidate.get("rows") or [] if r.get("row") == "hours"), None)
    if hours and hours.get("result") == "ok" and hours.get("text"):
        facts.append(str(hours["text"]))
    elif hours and hours.get("result") == "unknown":
        facts.append("영업시간은 확인하지 못했어요")
    after = (candidate.get("slack") or {}).get("after")
    if isinstance(after, (int, float)) and after >= 0:
        facts.append(f"다음 일정까지 {int(after)}분 여유가 있어요")
    parts = [part for part in [head, *facts] if part]
    return " · ".join(parts) if parts else None


# ── 끼니 시간의 시장 — 거기서 밥을 먹는 일정으로 읽는다 ───────────────────────
#: 「먹으러 가는 곳」으로 읽는 분류(관광공사 중분류) — 시장(SH06). 광장시장 12:30 은 쇼핑이 아니라 식사다(2026-10-07 사용자 지적 — 대체가 올리브영이었다)
EATING_PLACE_LCLS2 = ("SH06",)
#: 끼니 시간(분) — 아침 · 점심 · 저녁. ★우리가 고른 값(측정하지 않았다)
MEAL_WINDOWS = ((7 * 60, 10 * 60), (11 * 60 + 30, 14 * 60 + 30), (17 * 60 + 30, 20 * 60 + 30))
#: 이 일정의 시각 앞뒤로 이만큼(분) 안에 **다른 식사 일정**이 있으면 거기서 먹는 것으로 본다
MEAL_GAP_MIN = 120


def _minutes(clock: Any) -> int | None:
    try:
        hour, minute = str(clock).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, TypeError):
        return None


def _eats_there(items: list[dict[str, Any]], item: dict[str, Any], cls: dict[str, Any] | None) -> bool:
    """활동 일정이 **시장**이고 끼니 시간이며 그 시간대에 따로 식사 일정이 없으면 — 거기서 먹는 일정으로 읽는다."""
    if item["kind"] != "activity" or not cls or not cls.get("lcls") or cls["lcls"][1] not in EATING_PLACE_LCLS2:
        return False
    start = _minutes(item.get("starts_at"))
    if start is None or not any(a <= start <= b for a, b in MEAL_WINDOWS):
        return False
    for other in items:
        if other["id"] == item["id"] or other["date"] != item["date"] or other["kind"] != "dining":
            continue
        at = _minutes(other.get("starts_at"))
        if at is not None and abs(at - start) <= MEAL_GAP_MIN:
            return False
    return True


# ── 대체 후보 ─────────────────────────────────────────────────────
def alternatives(conn, *, tenant_id: str, review: dict[str, Any], item: dict[str, Any], kakao: Any = None,
                 engine: Callable[..., Any] | None = None, use_engine: bool = True, now: datetime | None = None,
                 limit: int = LIMIT, rank_only: bool = False, chat: Any = None) -> dict[str, Any]:
    """이 일정의 다른 안 — `{current, reference, candidates:[{rank, place, distance_m, reference, rows, fits, status}], notes}`.

    `chat` — 주면 화면에 먼저 보이는 후보의 이유 문장을 **모델이 확인된 값으로** 다시 쓴다(`reasons.polish` — 저장 · 검사 · 실패하면 틀 문장). 안 주면 전부 틀 문장.
    `rank_only` — 이동 계산기로 후보를 재지 않고 **싼 판정(운영시간 + 어림 이동)의 순서**만 낸다(자동 추천이 쓴다: 맞는 곳은 그쪽이 후보를
    차례로 시간표로 맞춰 보며 찾으므로 후보마다 미리 재면 계산기 시간을 두 번 쓴다 — 2026-10-03 실서버 10초)."""
    now = now or datetime.now(KST)
    items = review["items"]
    prev, nxt = neighbours(items, item)
    ref = reference(item, prev, nxt)
    notes: list[str] = []
    taken = _taken(items, item)
    current = item["place"]
    found: list[dict[str, Any]] = []
    branch = item["place_state"] in ("picked_nearest", "unresolved")
    area = area_of(item)
    # ★`[2026-10-04]` 「호텔 조식」 — 일정은 식사지만 장소는 숙소다. 식당 후보 · 지도 식당 검색으로 채우지 않고 숙박 분류 후보만 본다(없으면 비워 둔다)
    lodging = bool((item.get("parts") or {}).get("lodging_meal"))
    stay_item = item["kind"] == "lodging"         # ★`[2026-10-15]` 체크인 ~ 체크아웃으로 묶인 숙소 항목 — 숙소 후보만 보이고 식사 짝은 붙이지 않는다(식사 일정이 아니다)
    want = place_kind_of(item)
    stay = _is_stay_line(item) and not stay_item  # 「호텔」 · 「호텔 조식」 — 숙소 후보와 숙소 안 식사 후보를 함께 보인다
    if stay:
        limit = limit * 2                         # 숙소마다 짝(식사)이 붙으므로 칸을 두 배로
    cls: dict[str, Any] | None = None            # 원래 장소의 분류(같은 종류 다른 곳 블록에서 찾는다)
    group: str | None = None                      # 원래 장소의 경험 갈래(비슷한 경험 단계)
    if item["place_state"] == "needs_name":
        # ★예약했다고 적힌 이름 없는 줄 — 후보를 권하지 않는다(예약한 곳을 다른 곳으로 바꾸자고 하지 않는다). 이름은 검색 · 직접 입력으로 알려 준다
        notes.append("booked_needs_name")
    elif item["place_state"] == "needs_choice":
        parts = item.get("parts") or {}
        if ref is None:
            notes.append("no_reference_point")
        elif item["kind"] == "dining" and not lodging:
            found += _ledger_places(conn, tenant_id, ref, radius_m=int(area["radius_m"]) if area else 2000)
        else:
            found += _catalog_places(conn, tenant_id, ref, parts.get("content_type"), now,
                                     radius_m=float(area["radius_m"]) if area else RADIUS_M)
        if ref is not None and len(found) < limit + POOL_EXTRA:
            # 우리 목록이 모자라면 카카오 지도로 채운다 — 「성수 식당」 같은 말 그대로 지역 중심 둘레에서.
            # ★`[2026-10-07]` 숙소 줄(「호텔 조식」)도 「숙소」로 찾는다 — 전에는 숙소 줄에서 이 길을 막아 후보가 늘 0 이었다(사용자 지적 「전에는 카카오로 줬다」).
            #   카카오가 음식점(FD6 · CE7)을 섞어 내도 아래 종류 거르기(숙소 줄은 활동만)가 버린다
            words = " ".join(w for w in ((area or {}).get("name"), parts.get("label")) if w)
            found += _kakao_places(kakao, words, ref, want, notes)
        if ref is not None and not found:
            notes.append("no_candidates_in_area")            # 그 지역 둘레에 같은 종류가 하나도 없다 — 화면이 「이름을 직접 알려 주세요」로 안내하게
    if branch:
        # ① 같은 이름의 다른 지점
        hits = _kakao_places(kakao, item["title"] or "", ref, want, notes)
        words = (item["title"] or "").split()
        brand = normalize_full(words[0]) if words else ""
        found += [p for p in hits if brand and brand in normalize_full(p["name"])] or hits
    if not found and ref and item["place_state"] not in ("picked_nearest", "needs_choice", "needs_name"):
        # ② 같은 종류의 다른 곳 — ★이름이 모호한 체인(올리브영)은 다른 지점이 없다고 해서 엉뚱한 관광지를 대신 권하지 않는다(`notes` 에 이유)
        if item["kind"] == "dining":
            found += _ledger_places(conn, tenant_id, ref)
        elif stay_item:
            # ★`[2026-10-15]` 숙소 항목은 같은 숙박 분류에서만 찾는다 — 분류를 모르는 활동처럼 쇼핑만 빼고 관광지까지 권하지 않는다. 목록이 모자라면 지도의 「숙소」로 채운다
            found += _catalog_places(conn, tenant_id, ref, LODGING_TYPE, now)
            if len(found) < limit + POOL_EXTRA:
                found += _kakao_places(kakao, "숙소", ref, want, notes)
        else:
            cls = _original_class(conn, tenant_id, current)
            eat = _eats_there(items, item, cls)
            group = _experience_of(cls["lcls"][1]) if cls and cls["lcls"] else None
            if cls and cls["lcls"]:
                # ★`[2026-10-07 사용자]` 비슷한 곳 — 같은 소분류 → 중분류 → 대분류 순(가까운 순), 다른 대분류로는 넘어가지 않는다. 끼니 시간의 시장은 같은 중분류(시장)까지만
                found += _similar_places(conn, tenant_id, ref, cls["lcls"], now, min_level=2 if eat else 1,
                                         grade=cls.get("grade") if cls.get("grade") == "estimated" else None)
            else:
                found += _catalog_places(conn, tenant_id, ref, cls["content_type"] if cls else None, now)
            if eat:
                found += [{**place, "basis": "meal_inferred"} for place in _ledger_places(conn, tenant_id, ref)]   # 거기서 밥을 먹는 일정 — 식당도 후보다
                notes.append("meal_inferred")
            if cls and not found:
                notes.append("no_same_kind")
    if branch and not found and "kakao" not in " ".join(notes):
        notes.append("no_other_branches")
    found = _dedupe(found, taken)
    if group and "meal_inferred" not in notes and len([p for p in found if p.get("basis") == "same_kind"]) < limit:
        # ★`[2026-10-07 사용자]` 같은 종류가 모자라면 같은 **경험**의 곳으로 이어 간다(궁궐 → 공원 · 둘레길 / 전시 → 박물관 · 전시관)
        more = _dedupe(_experience_places(conn, tenant_id, ref, group, now), taken + [
            (normalize_full(p["name"]), p.get("latitude"), p.get("longitude")) for p in found])
        found += more
        if more:
            notes.append("similar_experience")
    if (item["kind"] == "activity" and not stay and not lodging and ref and "meal_inferred" not in notes and len(found) < limit
            and item["place_state"] in ("found", "customer")):
        # ★`[2026-10-07 사용자 결정]` 셋째 단 — 취향 추천: 앞 두 단이 모자랄 때, 이 여행에 담은 다른 활동의 분류(센 값)에서 가까운 곳을 채운다
        from . import taste as taste_module

        profile = taste_module.profile(conn, _tenants(tenant_id), items, item["id"])
        more = _dedupe(taste_module.places(conn, tenant_id, ref, profile, now, exclude_lcls2={((cls or {}).get("lcls") or [None] * 3)[1]} - {None}),
                       taken + [(normalize_full(p["name"]), p.get("latitude"), p.get("longitude")) for p in found])
        found += more
        if more:
            notes.append("taste")
    if cls and not found:
        if "no_same_kind" not in notes:
            notes.append("no_same_kind")                  # 원래 장소만 같은 분류여서 중복 제거로 비는 경우도 같은 종류가 없는 것이다
        notes.append("no_alternative")                    # 같은 종류도 비슷한 경험도 취향 갈래도 없다 — 화면이 이유를 말하게
    # ★후보의 종류는 일정의 종류와 같다 — 맞는 것이 없으면 다른 종류로 채우지 않는다(식사 자리에 약국 · 활동 자리에 식당)
    before = len(found)
    kinds = {"activity", "dining"} if "meal_inferred" in notes else {"activity" if (lodging or stay) else want}
    found = [p for p in found if p.get("kind") in kinds]
    if stay_item:
        found = [{**p, "basis": p.get("basis") or "lodging"} for p in found if _is_lodging_place(p)]     # 숙소 항목의 후보는 숙소만 — 지도의 관광지 · 편의시설은 거른다
    if stay and item["place_state"] == "needs_choice" and found:
        # 종류를 거른 **뒤에** 짝을 붙인다 — 지도의 음식점(호텔 뷔페)이 식사 후보로 섞이지 않고, 짝은 숙소 이름 그대로인 것만 생긴다
        found = _with_stay_meals(found)
        notes.append("stay_and_meal")
    if before and not found and "no_same_kind" not in notes:
        notes.append("no_same_kind")
    if engine is None and use_engine:
        engine = default_engine(None)
    budget = engine if isinstance(engine, _Budgeted) else (_Budgeted(engine, ENGINE_BUDGET_S) if engine is not None else None)

    # 먼저 싼 판정(운영시간 + 어림 이동)으로 줄을 세우고, 앞선 `limit + POOL_EXTRA` 곳을 이동 계산기로 다시 잰 뒤 **전체 판정 순서로** `limit` 곳을 고른다
    # (어림으로 늦어 보이던 곳이 시간표로는 닿는 경우가 있어, 앞 셋만 재면 적합한 넷째가 올라오지 못한다)
    first: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for place in found[:30]:
        first.append((place, assess(conn, tenant_id, item, place, prev, nxt, None)))
    first.sort(key=lambda pa: (not pa[1]["fits"], pa[1]["status"] == "bad", -(pa[0].get("similarity") or 0),
                               _score(pa[0], prev, nxt, ref, bool(area))))
    pool = first[:limit + POOL_EXTRA]
    if "meal_inferred" in notes:
        eating_first = [pa for pa in first if pa[0].get("kind") == "dining"][:2]
        pool = [pa for pa in first if pa not in eating_first][:max(0, limit + POOL_EXTRA - len(eating_first))] + eating_first
    out = []
    for place, cheap in pool:
        full = cheap if rank_only else assess(conn, tenant_id, item, place, prev, nxt, budget)
        out.append({"rank": 0, **_present(place, full, prev, nxt, ref, area)})
    # 시간표로 잰 곳이 어림으로 잰 곳보다 앞(같은 적합성 안에서) — 계산기 시간 상한이 일찍 닿아 뒤 후보가 어림으로 평가돼도 순위가 뒤섞이지 않게
    out.sort(key=lambda c: (not c["fits"], c["status"] == "bad", _BASIS_ORDER.get(c["basis"], 0), -(c["similarity"] or 0),
                            -((c.get("taste") or {}).get("score") or 0), c["estimated"],     # 취향 후보는 임베딩 점수가 있으면 그 순서(`taste.ranked_by == "embedding"`)
                            c["distance_m"] if c["distance_m"] is not None else 10**9))
    if "meal_inferred" in notes:
        # 거기서 밥을 먹는 일정 — 시장만 셋이 아니라 식당도 둘까지 보인다(순서는 위 판정 순서를 지킨다)
        eating = [c for c in out if c["place"]["kind"] == "dining"][:2]
        rest = [c for c in out if c not in eating][:max(0, limit - len(eating))]
        picked = [id(c) for c in (*rest, *eating)]
        out = [c for c in out if id(c) in picked]
    out = out[:limit]
    original_name = (current or {}).get("name")
    for c in out:
        c["reason"] = _reason(c, original_name)
        c["reason_by"] = "template"
    if chat is not None and out:
        from . import reasons

        reasons.polish(conn, tenant_id, out, original_name, chat)
    if budget is not None and budget.exhausted:
        notes.append("engine_budget_exhausted")                     # 일부 후보의 이동은 직선 어림값이다(후보의 `estimated`)
    for rank, c in enumerate(out, start=1):
        c["rank"] = rank
    return {"item": item["id"], "current": public_place(item["place"]), "reference": {"before": prev and prev["place"]["name"],
                                                                      "after": nxt and nxt["place"]["name"],
                                                                      **({"area": area["name"]} if area else {})},
            "candidates": out, "notes": notes}


# ── 장소 검색 ─────────────────────────────────────────────────────
def search(conn, *, tenant_id: str, review: dict[str, Any], item: dict[str, Any], query: str, kakao: Any = None,
           now: datetime | None = None, limit: int = SEARCH_LIMIT) -> dict[str, Any]:
    """검색어로 찾은 장소 — 앞뒤 일정에서 가까운 순. 각 곳에 그 일정의 날짜 · 시각 검사를 붙인다(이동 계산기는 안 쓴다 — 어림)."""
    now = now or datetime.now(KST)
    q = " ".join(query.split())
    items = review["items"]
    prev, nxt = neighbours(items, item)
    ref = reference(item, prev, nxt)
    notes: list[str] = []
    area = area_of(item)
    taken = _taken(items, item, include_self=False)
    # ★`[2026-10-04]` 숙소 + 끼니(「호텔 조식」)는 식사 일정이지만 장소는 숙소 — 숙소를 찾는 검색이라 식당 목록은 안 섞는다
    lodging = bool((item.get("parts") or {}).get("lodging_meal"))
    stay_item = item["kind"] == "lodging"          # ★`[2026-10-15]` 숙소 항목 — 후보는 지도 · 목록의 숙소만(종류는 활동 쪽으로 읽힌다)
    found = _db_places_by_name(conn, tenant_id, q, ref, now)
    if ref and not lodging and not stay_item:
        found += _ledger_places(conn, tenant_id, ref, query=q, radius_m=5000)
    found += _kakao_places(kakao, q, ref, "activity" if lodging else place_kind_of(item), notes, size=10)
    key = normalize_full(q)
    current = item["place"]
    if current and key in normalize_full(current["name"]):
        # 지금 장소가 검색어에 맞으면 결과에 남긴다 — 임시로 고른 곳을 「맞아요」로 확정하려고 다시 고르는 길이다
        found.insert(0, _place(current["name"], current["latitude"], current["longitude"], current.get("source") or "places",
                               kind=current.get("kind") or place_kind_of(item), category=current.get("category"), content_id=current.get("content_id"),
                               content_type_id=current.get("content_type_id"),
                               ref=f"tour:{current['content_id']}" if current.get("content_id") else None))
    found = _dedupe(found, taken)
    # ★검색 결과도 일정의 종류와 같은 곳만 — 식당 자리를 찾는데 약국 · 공원이 섞이지 않게(맞는 것이 없으면 이유가 `no_same_kind`)
    before = len(found)
    found = [p for p in found if p.get("kind") == ("activity" if lodging else place_kind_of(item))]
    if stay_item:
        found = [p for p in found if _is_lodging_place(p)]
    if before and not found:
        notes.append("no_same_kind")
    # 이름이 검색어와 정확히 같은 곳 → 검색어로 시작하는 곳 순, 그 안에서 **일정에 맞는 곳**(휴무 · 운영시간 밖이 아닌 곳)이 앞, 같으면 앞뒤 일정에서 가까운 순
    # 평가(DB)할 풀을 고를 때도 출처 순서가 아니라 이름 일치 → 가까운 순으로 먼저 줄 세운다(뒤에 붙은 카카오 결과가 평가도 못 받고 잘리지 않게)
    found.sort(key=lambda p: (0 if normalize_full(p["name"]) == key else 1 if normalize_full(p["name"]).startswith(key) else 2, _score(p, prev, nxt, ref, bool(area))))
    judged = [(p, assess(conn, tenant_id, item, p, prev, nxt, None)) for p in found[:SEARCH_POOL]]
    judged.sort(key=lambda pa: (0 if normalize_full(pa[0]["name"]) == key else 1 if normalize_full(pa[0]["name"]).startswith(key) else 2, pa[1]["status"] == "bad",
                                not pa[1]["fits"], _score(pa[0], prev, nxt, ref, bool(area))))
    out = [{"rank": rank, **_present(p, a, prev, nxt, ref, area)} for rank, (p, a) in enumerate(judged[:limit], start=1)]
    return {"item": item["id"], "query": q, "results": out, "notes": notes}


# ── 사진 ─────────────────────────────────────────────────────────
def photos(tour: Any, ref: str) -> dict[str, Any]:
    """`ref` = `tour:<관광공사 번호>` — 그 장소에 등록된 사진 주소. 저장하지 않고 부를 때마다 받는다. 다른 출처는 사진이 없다고 답한다(지어내지 않는다)."""
    kind, _, ident = ref.partition(":")
    if kind != "tour" or not ident.isdigit():
        return {"ref": ref, "photos": [], "source_note": None, "reason": "no_photo_source"}
    if tour is None or not hasattr(tour, "images"):
        return {"ref": ref, "photos": [], "source_note": None, "reason": "tour_unavailable"}
    got = tour.images(ident)
    if got is None:
        misses = getattr(tour, "misses", None) or {}
        return {"ref": ref, "photos": [], "source_note": None,
                "reason": "tour:" + (max(misses, key=misses.get) if misses else "unavailable")}
    return {"ref": ref, "photos": got, "source_note": "ⓒ한국관광공사" if got else None, "reason": None if got else "no_photos"}


__all__ = ["LIMIT", "alternatives", "assess", "neighbours", "photos", "reference", "search"]
