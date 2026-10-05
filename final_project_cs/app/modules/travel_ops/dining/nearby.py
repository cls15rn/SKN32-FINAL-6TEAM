"""대체 식당 후보를 원장에서 들여놓는다. `[2026-10-01]`

왜.
    대체 계산(`itinerary_changes`)은 코어 `places`(공용 + 그 여행 전용)에서만 후보를 찾는다. 공용 식당이 1곳뿐인 DB 에서
    「문 닫았어요」의 후보가 0개였다 — 원장에는 반경 700m 안에 36곳이 있었는데. develop DB 는 누군가 원장 식당을
    공용 `places` 에 손으로 넣어 두어 후보가 나왔다(`dining_ledger_localtest`, 코드에 없음).

어떻게.
    계산 **직전에** 식사 장소 근처의 원장 가게를 **그 여행 전용 장소**(`trip_scope`)로 들여놓고 원장과 바로 잇는다.
    대체 계산 코드는 그대로다 — 후보 목록만 늘어난다. 일정 항목은 코어 장소 id(UUID)만 가리킬 수 있어서
    (`Item.replaced_by`) 원장 가게를 그대로 후보로 넘길 수는 없다.

경계.
    공용 표에는 넣지 않는다 — 일정 접수의 이름 찾기 · 오타 교정 · 다른 여행의 후보에 섞이지 않는다.
    코어 표에 쓰는 것은 코어(`TripStore.adopt_places`)가 하고, 여기는 원장을 읽고 요식 표(연결)에만 쓴다.
    원장을 못 읽으면 예전 목록 그대로 돌려준다 — 더해 주는 것이지 없으면 못 도는 것이 아니다.
"""
from __future__ import annotations

from typing import Any

from .ledger import link_core_place_to, nearby_shops, resolve_place

#: 식사 장소에서 이만큼 안 — 대체 계산의 도보 반경(`itinerary_changes.DINING_RADIUS_M`)과 같다
RADIUS_M = 700


def add_nearby(conn, store: Any, trip_id: Any, items: list[Any], places: list[dict[str, Any]], *,
               radius_m: int = RADIUS_M) -> list[dict[str, Any]]:
    """식사 항목 근처의 원장 가게를 그 여행 전용 장소로 들여놓은 **새 장소 목록**. 실패하면 `places` 그대로."""
    around = [(float(i.place["latitude"]), float(i.place["longitude"])) for i in items
              if i.kind == "dining" and i.place and i.place.get("latitude") is not None
              and i.place.get("longitude") is not None]
    if not around:
        return places
    try:
        with conn.transaction():                         # 실패해도 바깥 트랜잭션을 깨지 않는다
            # 신고받은 식당이 아직 연결되지 않았어도 먼저 식별한다. 이름이 달라도 같은 원장 가게는 제외한다.
            meals = [i.place for i in items if i.kind == "dining" and i.place]
            originals = {str(p["place_id"]) for p in meals}
            visible_dining = {str(p["place_id"]): p for p in [*places, *meals] if p.get("kind") == "dining"}
            resolved = {core_id: resolve_place(conn, store.tenant_id, core_id)
                        or (p.get("attributes") or {}).get("dining_place_uid")
                        for core_id, p in visible_dining.items()}
            original_uids = {resolved[core_id] for core_id in originals if resolved.get(core_id)}
            # 앞선 요청에서 이미 들여놓은 별칭도 제거한다. 일정 항목과 후보 dict가 다른 객체여도 ID로 판별한다.
            candidates = [p for p in places if str(p["place_id"]) in originals
                          or not resolved.get(str(p["place_id"]))
                          or resolved[str(p["place_id"])] not in original_uids]
            shops = nearby_shops(conn, store.tenant_id, around, radius_m=radius_m,
                                 visible_core_ids=[p["place_id"] for p in places])
            known = {(p.get("name") or "").strip() for p in places}
            known_uids = {str(p["attributes"]["dining_place_uid"]) for p in places
                          if (p.get("attributes") or {}).get("dining_place_uid")}
            known_content_ids = {str(p.get("source_content_id") or p["attributes"]["source_content_id"])
                                 for p in places if p.get("source_content_id")
                                 or (p.get("attributes") or {}).get("source_content_id")}
            shops = [s for s in shops if s["name"].strip() not in known
                     and s["place_uid"] not in known_uids
                     and (not s["content_id"] or str(s["content_id"]) not in known_content_ids)]
            if not shops:
                return candidates
            adopted = store.adopt_places(conn, trip_id, [
                {"name": s["name"], "kind": "dining", "latitude": s["latitude"], "longitude": s["longitude"],
                 "attributes": {"source": "dining_ledger", "dining_place_uid": s["place_uid"],
                                **({"source_content_id": s["content_id"]} if s["content_id"] else {})}}
                for s in shops])
            requested_uids = {s["place_uid"] for s in shops}
            # 동명 식당은 (name, kind) 유일성 제약으로 첫 행이 남는다. 실제 저장된 행의 UID로만 연결한다.
            adopted = [p for p in adopted if p.get("kind") == "dining"
                       and (p.get("attributes") or {}).get("dining_place_uid") in requested_uids]
            for place in adopted:
                link_core_place_to(conn, store.tenant_id, place["place_id"], place["attributes"]["dining_place_uid"])
    except Exception:   # noqa: BLE001 — 드라이버를 import 하지 않는다(Team 경계). 못 읽으면 예전 목록
        return places
    seen = {p["place_id"] for p in candidates}
    return candidates + [p for p in adopted if p["place_id"] not in seen]
