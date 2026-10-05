"""요식 원장을 코어가 읽을 수 있는 모양으로 돌려준다.

무엇인가.
    `places.open_at_slot` 을 채우는 자리다. 지금 그 칸은 아무도 안 채운다 —
    `dining.py` 는 읽고, 시드는 박아 넣고, `tour_api.py` 는 못 채운다고 밝힌다.
    `watch.py` 주석의 「식사 항목의 시스템 감지는 소스가 없다」도 같은 말이다.

왜 칸이 아니라 함수인가.
    `read.place` 는 시각을 받지 않는다. 그런데 「그 시각에 여는가」는 시각이
    있어야 답할 수 있다. 칸 하나에 미리 채워 두면 12시 예약과 22시 예약이
    같은 값을 보게 된다. 그래서 시각을 받는 함수로 둔다.

경계.
    코어 표를 읽지 않는다. 코어 place_id 를 우리 place_uid 로 바꾸는 것은
    `dn_core_place_link` 이고 그것도 요식 표다. 코어 표에 쓰지도 않는다.

쓰는 쪽.
    코어의 도구 계층이 부른다. `read.dining_state` 로 등록하면 된다.

        from app.modules.travel_ops.dining.ledger import dining_state
        ...
        "read.dining_state": self.dining_state,

        def dining_state(self, scope, *, place_id=None, at=None, until=None, **_):
            with self.connection_factory() as conn:
                return dining_state(conn, scope.tenant_id, place_id, at, until)

모르는 것은 모른다고 돌려준다.
    이어지지 않은 장소, 규칙이 없는 요일, 확인한 적 없는 속성은 모두 None 이다.
    받는 쪽이 None 을 「아니다」로 읽으면 안 된다. 그 구분은 Team 이 한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))

#: 코어 `dietary` 와 우리 속성 코드의 대응.
#: 코어는 「되는 것」과 「안 되는 것」을 두 목록으로 나눠 받는다. 모르는 것은
#: 어느 목록에도 넣지 않는다 — 그래야 Team 이 모름으로 읽는다.
DIETARY = {
    "halal": "halal",
    "vegetarian": "vegetarian_menu",
    "kids": "kids_allowed",
}


def _as_datetime(value: Any) -> datetime | None:
    """문자열로 온 시각을 datetime 으로. 못 읽으면 None — 지금으로 대체하지 않는다.

    지금 시각으로 대체하면 저녁 예약을 물었는데 점심 영업으로 답한다.
    """
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        got = datetime.fromisoformat(text)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=KST)


def resolve_place(conn, tenant_id: str, core_place_id: str) -> str | None:
    """코어 장소를 요식 원장 장소로. 이어지지 않았으면 None.

    억지로 이름으로 찾지 않는다. 잘못 이으면 다른 식당의 영업시간으로
    판정하게 되고, 그것은 틀린 답을 자신 있게 말하는 것이다.

    ★아직 이어지지 않았으면 `dining.link_core_place`(220)로 이어 본다(2026-10-01).
      연결은 rebuild 때만 만들어져서, 그 뒤 여행에 들어온 장소는 늘 「모름」이었다.
      관광공사 콘텐츠 ID 가 같은 가게, 아니면 매칭기와 같은 규칙으로 하나만 정해질 때만 잇는다.
      함수나 코어 표가 없는 DB(요식만 세운 DB · 220 이전)에서는 예전처럼 None 이다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT place_uid FROM dining.dn_core_place_link "
            "WHERE tenant_id = %s AND core_place_id = %s",
            (tenant_id, core_place_id))
        row = cur.fetchone()
    if row:
        return str(row[0])
    try:
        with conn.transaction(), conn.cursor() as cur:   # 실패해도 바깥 트랜잭션을 깨지 않는다
            cur.execute("SELECT dining.link_core_place(%s, %s)", (tenant_id, core_place_id))
            row = cur.fetchone()
    except Exception:   # noqa: BLE001 — 드라이버를 import 하지 않는다(Team 경계). 못 이으면 모름
        return None
    return str(row[0]) if row and row[0] else None


def dining_state(conn, tenant_id: str, place_id: str | None,
                 at: Any = None, until: Any = None) -> dict[str, Any] | None:
    """그 시각 그 장소의 요식 판정. 어느 장소인지 모르면 None.

    돌려주는 것
        open_at_slot          그 시각에 여는가. None 은 모름이다
        needs_check           종료가 임박해 마지막 주문을 확인해야 하는가
        needs_holiday_check   명절인데 그 장소의 규칙을 모르는가
        holiday_context       무슨 날인지와 확인할 곳. 경고가 아니면 None
        hours                 코어 attributes 형식의 영업시간
        dietary               맞는 것으로 확인된 조건
        dietary_absent        아닌 것으로 확인된 조건
        card_payment          카드 결제 가능 여부. None 은 모름이다
        confirmed_at          영업 정보를 확인한 시각. 현장 확인이 아니다
        source                어디서 온 값인지
    """
    if place_id is None:
        return None                      # 어느 장소인지 모르면 조회하지 않는다
    starts_at = _as_datetime(at)
    if starts_at is None:
        return None                      # 언제인지 모르면 답할 수 없다
    ends_at = _as_datetime(until)

    place_uid = resolve_place(conn, tenant_id, str(place_id))
    if place_uid is None:
        # 이어지지 않은 장소다. 「영업 안 함」이 아니라 「모름」이다.
        return {"place_id": str(place_id), "linked": False,
                "open_at_slot": None, "needs_check": None,
                "needs_holiday_check": None, "holiday_context": None,
                "attributes": {}, "hours": None, "break": None,
                "dietary": [], "dietary_absent": [], "card_payment": None,
                "confirmed_at": None, "source": "dining_ledger"}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT open_at_slot, needs_check, needs_holiday_check, "
            "       holiday_context, attributes, hours_confirmed_at "
            "FROM dining.core_place_state(%s, %s, %s, %s)",
            (tenant_id, str(place_id), starts_at, ends_at))
        got = cur.fetchone()

        dietary, absent = [], []
        for core_name, attr_code in DIETARY.items():
            cur.execute("SELECT dining.meets_condition(%s, %s)", (place_uid, attr_code))
            meets = cur.fetchone()[0]
            if meets is True:
                dietary.append(core_name)
            elif meets is False:
                absent.append(core_name)
            # None 은 어느 쪽에도 넣지 않는다. 모르는 것은 모르는 것이다.
        cur.execute("SELECT dining.meets_condition(%s, %s)", (place_uid, "card_payment"))
        card_payment = cur.fetchone()[0]

    if got is None:
        return {"place_id": str(place_id), "linked": True,
                "open_at_slot": None, "needs_check": None,
                "needs_holiday_check": None, "holiday_context": None,
                "attributes": {}, "hours": None, "break": None,
                "dietary": dietary, "dietary_absent": absent, "card_payment": card_payment,
                "confirmed_at": None, "source": "dining_ledger"}

    open_at, needs_check, needs_holiday, holiday_ctx, attributes, confirmed = got
    # attributes 를 통째로 넘긴다. hours 만 꺼내면 break 가 사라져서,
    # 브레이크타임이 있는 집을 「11:00~22:00 내내 영업」으로 보여 주게 된다.
    # 판정은 open_at_slot 이 맞게 하더라도 화면에 잘못 적히면 사람이 헛걸음한다.
    return {
        "place_id": str(place_id),
        "linked": True,
        "open_at_slot": open_at,
        "needs_check": needs_check,
        "needs_holiday_check": needs_holiday,
        "holiday_context": holiday_ctx,
        "attributes": attributes or {},
        "hours": (attributes or {}).get("hours"),
        "break": (attributes or {}).get("break"),
        "dietary": dietary,
        "dietary_absent": absent,
        "card_payment": card_payment,
        "confirmed_at": confirmed,
        # 좌표를 바깥에서 채울 때 출처를 남기는 것과 같은 이유다.
        # 우리 값과 코어 값이 한 dict 에 섞이면 틀렸을 때 어디를 고칠지 모른다.
        "source": "dining_ledger",
    }


def dining_states(conn, tenant_id: str, slots: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """후보별 방문 구간을 한 연결에서 읽는다. 장소마다 하나의 구간을 받는다."""
    return {str(slot["place_id"]): dining_state(conn, tenant_id, slot["place_id"],
                                               slot.get("at"), slot.get("until"))
            for slot in slots}


def slot_verdicts(conn, slots: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """등록 · 생성기 판정용 — 원장 가게의 그 방문 시각에 여는가. 돌려주는 값 = {seq: 판정}. `[2026-10-02]`

    ★등록할 때는 코어 장소가 아직 없어 `dining_state`(코어 place_id 로 찾는다)를 못 쓴다. 칸마다 원장 가게를 바로 찾는다 —
      `place_uid`(원장 가게 ID, 생성기 후보)가 먼저, 없으면 `content_id`(일정 접수가 싣는 관광공사 ID).
    ★판정은 `dining.open_at_slot` 그대로 — 휴무 · 브레이크 · 자정 넘김 · 폐업을 이미 본다. None 은 모름이다.
    ★원장에 없거나 한 관광공사 ID 가 두 가게로 이어졌으면 결과에 넣지 않는다 — 고르지 않는다(모름).
    """
    out: dict[int, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for slot in slots:
            if slot.get("place_uid"):
                where, key = "p.place_uid::text = %s", str(slot["place_uid"])
            else:
                where, key = ("p.place_uid IN (SELECT r.place_uid FROM dining.dn_source_record r "
                              "WHERE r.source_code = 'tourapi_kor_food' AND r.external_id = %s "
                              "AND r.match_status <> 'rejected')"), str(slot["content_id"])
            cur.execute(
                "SELECT p.record_status = 'closed', dining.open_at_slot(p.place_uid, %s, %s) "
                f"FROM dining.dn_place p WHERE {where} LIMIT 2",
                (slot["at"], slot.get("until"), key))
            rows = cur.fetchall()
            if len(rows) == 1:
                closed, open_at = rows[0]
                out[int(slot["seq"])] = {"open_at_slot": open_at, "closed": bool(closed)}
    return out


def planner_shops(conn) -> list[dict[str, Any]]:
    """일정 생성기의 식당 후보 — 원장의 실제 가게 전부(폐업 · 합성 · 좌표 없음 제외). 읽기만 한다. `[2026-10-02]`

    ★사용자 결정 — 식당은 관광공사 API 를 실시간으로 부르지 않는다. 생성기도 원장에서 고른다.
    ★관광공사 ID 가 없는 가게(미쉐린 · 비건 · 할랄 큐레이션 · 인허가)도 후보다. ID 는 있으면 같이 싣는다.
    ★영업 판정은 여기서 하지 않는다 — 방문 시각이 정해진 뒤 `slot_verdicts` · `open_among` 이 한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.place_uid, p.name_ko, p.lat, p.lng, coalesce(p.road_address, p.jibun_address), "
            "       (SELECT min(r.external_id) FROM dining.dn_source_record r "
            "         WHERE r.place_uid = p.place_uid AND r.source_code = 'tourapi_kor_food' "
            "           AND r.external_id IS NOT NULL) "
            "FROM dining.dn_place p "
            "WHERE p.record_status <> 'closed' AND NOT p.is_synthetic AND p.lat IS NOT NULL AND p.lng IS NOT NULL "
            "ORDER BY p.name_ko, p.place_uid")
        rows = cur.fetchall()
    return [{"place_uid": str(uid), "name": name, "lat": float(lat), "lng": float(lng),
             "address": address, "content_id": content_id}
            for uid, name, lat, lng, address, content_id in rows]


def open_among(conn, place_uids: list[str], at: Any, until: Any = None) -> dict[str, bool | None]:
    """여러 원장 가게가 같은 방문 시각에 여는가 — 한 번에 묻는다(생성기가 닫힌 식사를 바꿀 때). `[2026-10-02]`"""
    if not place_uids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.place_uid::text, dining.open_at_slot(p.place_uid, %s, %s) "
            "FROM dining.dn_place p WHERE p.place_uid::text = ANY(%s)",
            (at, until, [str(uid) for uid in place_uids]))
        return dict(cur.fetchall())


class PlannerLedger:
    """일정 생성기가 원장을 읽는 자리 — 생성기는 이 셋만 부른다(`planner.plan_trip(ledger=…)`). `[2026-10-02]`

    ★생성기와 같은 연결을 쓴다. 요식 표가 없는 DB 에서 SQL 이 실패해도 바깥 트랜잭션을 깨지 않게 저장점 안에서 묻는다.
    ★후보를 못 읽으면 빈 목록(원장 식당 없이 짠다), 판정을 못 읽으면 예외 — 받는 쪽(`with_ledger`)이 「모름」으로 둔다.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def shops(self) -> list[dict[str, Any]]:
        try:
            with self._conn.transaction():
                return planner_shops(self._conn)
        except Exception:   # noqa: BLE001 — 드라이버를 import 하지 않는다(Team 경계). 요식 표가 없는 DB
            return []

    def verdicts(self, slots: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        with self._conn.transaction():
            return slot_verdicts(self._conn, slots)

    def open_among(self, place_uids: list[str], at: Any, until: Any = None) -> dict[str, bool | None]:
        try:
            with self._conn.transaction():
                return open_among(self._conn, place_uids, at, until)
        except Exception:   # noqa: BLE001 — 못 읽으면 모두 모름
            return {}


def find_place_by_name(conn, name: str | None) -> dict[str, Any] | None:
    """일정 접수의 장소 찾기용 — 이름이 같은 관광공사 출처 가게가 **하나뿐**이면 관광공사 결과 모양으로.

    ★`[2026-10-01]` 일정 접수가 액티비티 CSV 만 봐서 식당을 하나도 못 찾았다(「토속촌삼계탕」).
      CSV 에 없을 때 여기를 본다. 외부 API 가 아니라 원장 DB 다.
    ★관광공사 콘텐츠 ID 가 있는 가게만 — 결과가 `tour_api` 출처로 저장되므로 그 ID 가 있어야 한다.
      그 ID 로 판정 때 원장과 다시 이어진다(220 `link_core_place`).
    ★같은 이름이 둘 이상이면 고르지 않는다. 폐업 · 서울 밖 · 좌표 없음 · 합성 가게도 내보내지 않는다.
    ★요식 표가 없는 DB(요식만 빠진 DB)에서는 None — 예전처럼 「못 찾음」이다.
    """
    key = re.sub(r"[^가-힣a-z0-9]", "", (name or "").lower())     # 아래 SQL 과 같은 규칙
    if not key:
        return None
    try:
        with conn.transaction(), conn.cursor() as cur:   # 실패해도 바깥 트랜잭션을 깨지 않는다
            cur.execute(
                "SELECT p.name_ko, p.lat, p.lng, coalesce(p.road_address, p.jibun_address), "
                "       min(r.external_id), count(DISTINCT r.external_id) "
                "FROM dining.dn_place p "
                "JOIN dining.dn_source_record r ON r.place_uid = p.place_uid "
                "     AND r.source_code = 'tourapi_kor_food' AND r.external_id IS NOT NULL "
                "WHERE regexp_replace(lower(p.name_ko), '[^가-힣a-z0-9]', '', 'g') = %s "
                "  AND p.record_status <> 'closed' AND NOT p.is_synthetic "
                "  AND p.lat IS NOT NULL AND p.lng IS NOT NULL "
                "GROUP BY p.place_uid, p.name_ko, p.lat, p.lng, p.road_address, p.jibun_address "
                "LIMIT 2", (key,))
            rows = cur.fetchall()
    except Exception:   # noqa: BLE001 — 드라이버를 import 하지 않는다(Team 경계). 못 읽으면 못 찾음
        return None
    if len(rows) != 1:
        return None
    title, lat, lng, address, content_id, ids = rows[0]
    if ids != 1 or not str(address or "").startswith("서울"):
        return None
    return {"content_id": str(content_id), "content_type_id": "39", "matched_title": title,
            "latitude": float(lat), "longitude": float(lng), "address": address}


def nearby_shops(conn, tenant_id: str, around: list[tuple[float, float]], *, radius_m: int, limit: int | None = None,
                 visible_core_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """대체 식당 후보 — 기준 좌표들 중 하나에서 `radius_m` 안의 원장 가게, 가까운 순. 읽기만 한다.

    ★`[2026-10-01]` 대체 계산은 코어 `places` 에서만 후보를 찾아, 공용 식당이 1곳뿐인 DB 에서는 후보가 0개였다.
    ★폐업 · 합성 · 좌표 없는 가게는 내보내지 않는다. 그 여행이 이미 보는 코어 장소와 이어진 가게도 뺀다(중복 후보).
    ★영업 판정은 여기서 하지 않는다 — 들여놓은 뒤 `dining_states` 가 방문 시각으로 한다.
    ★기본은 반경 안의 전체 후보 — 영업 판정 전에 개수를 자르면 뒤에 있는 유효 후보를 놓친다.
    """
    if not around:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "WITH pts AS (SELECT * FROM unnest(%s::float8[], %s::float8[]) AS t(lat, lng)) "
            "SELECT p.place_uid, p.name_ko, p.lat, p.lng, "
            "       (SELECT min(r.external_id) FROM dining.dn_source_record r "
            "         WHERE r.place_uid = p.place_uid AND r.source_code = 'tourapi_kor_food' "
            "           AND r.external_id IS NOT NULL), "
            "       min(dining.distance_m(p.lat, p.lng, pts.lat, pts.lng)) AS d "
            "FROM dining.dn_place p CROSS JOIN pts "
            "WHERE p.record_status <> 'closed' AND NOT p.is_synthetic "
            "  AND p.lat IS NOT NULL AND p.lng IS NOT NULL "
            "  AND dining.distance_m(p.lat, p.lng, pts.lat, pts.lng) <= %s "
            "  AND NOT EXISTS (SELECT 1 FROM dining.dn_core_place_link l "
            "                   WHERE l.tenant_id = %s AND l.place_uid = p.place_uid "
            "                     AND l.core_place_id::text = ANY(%s::text[])) "
            "GROUP BY p.place_uid, p.name_ko, p.lat, p.lng "
            "ORDER BY d, p.place_uid LIMIT %s",
            ([float(a) for a, _ in around], [float(b) for _, b in around], radius_m,
             tenant_id, [str(i) for i in (visible_core_ids or [])], limit))
        rows = cur.fetchall()
    return [{"place_uid": str(uid), "name": name, "latitude": float(lat), "longitude": float(lng),
             "content_id": content_id, "distance_m": float(d)}
            for uid, name, lat, lng, content_id, d in rows]


def link_core_place_to(conn, tenant_id: str, core_place_id: str, place_uid: str) -> None:
    """들여놓은 코어 장소를 그 원장 가게와 잇는다(이미 이어져 있으면 그대로). 요식 표에만 쓴다."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dining.dn_core_place_link (tenant_id, core_place_id, place_uid, linked_by) "
            "VALUES (%s, %s, %s, 'nearby') ON CONFLICT ON CONSTRAINT dn_core_place_link_pkey DO NOTHING",
            (tenant_id, str(core_place_id), str(place_uid)))


def merge_state(place: dict[str, Any] | None,
                state: dict[str, Any] | None) -> dict[str, Any] | None:
    """코어 장소 정보에 원장 판정을 얹는다. DB 를 쓰지 않는다.

    Team 은 도구로 받은 dict 만 갖고 있고 연결이 없다. 그래서 합치는 일을
    따로 떼어 둔다. 도구를 통해 오든 함수를 직접 부르든 같은 규칙으로 합쳐진다.

    코어가 이미 가진 값을 덮어쓰지 않는다. 비어 있을 때만 채운다.
    덮어쓰면 코어가 확인해 둔 값을 우리가 지우게 되고, 그것은 우리 권한이 아니다.
    """
    if place is None:
        return None
    if state is None or not state.get("linked"):
        return place

    out = dict(place)
    filled = []
    # 코어 SQL 칸 이름은 hours_confirmed_at 이지만 도구가 돌려주는 dict 의 키는
    # confirmed_at 이다 (_PLACE_COLUMNS). 받는 쪽 이름에 맞춘다.
    for core_key, our_key in (("open_at_slot", "open_at_slot"),
                              ("confirmed_at", "confirmed_at")):
        if out.get(core_key) is None and state.get(our_key) is not None:
            out[core_key] = state[our_key]
            filled.append(core_key)

    # 목록은 합친다. 코어가 아는 것을 지우지 않는다.
    for key in ("dietary", "dietary_absent"):
        merged = list(out.get(key) or [])
        for item in state.get(key) or []:
            if item not in merged:
                merged.append(item)
                filled.append(key)
        out[key] = merged

    # 판정에 쓰지는 않되 Team 이 표시할 수 있게 함께 넘긴다.
    out["dining"] = {k: state[k] for k in
                     ("needs_check", "needs_holiday_check", "holiday_context",
                      "hours", "break")}
    if filled:
        out["dining_source"] = "dining_ledger"
    return out


def enrich_place(conn, tenant_id: str, place: dict[str, Any] | None,
                 at: Any = None, until: Any = None) -> dict[str, Any] | None:
    """`read.place` 결과에 원장을 얹는다. 조회와 합치기를 한 번에 한다.

    `read.place` 가 시각을 받게 되면 이 함수를 그 안에서 부르면 된다.
    시각이 없으면 아무것도 채우지 않는다 — 시각 없이 답할 수 있는 척하지 않는다.
    """
    if place is None:
        return None
    state = dining_state(conn, tenant_id, place.get("place_id"), at, until)
    return merge_state(place, state)
